"""
tests/test_smbus_mctp_server_pbr_cmds.py
=========================================
Invokes ALL 6 PBR switch CCI commands over FmSmbusMctpServer (single port,
bidirectional -- same connection for RX and TX).  After each command the
server's _print_tx_packet fires and prints the formatted response.
The client also pretty-prints what it received.

Commands tested (full GFD commissioning sequence):
  (1) IDENTIFY_PBR_SWITCH     (0x5700) -- learn num_drts, gae_support_map
  (2) CONFIGURE_PID_ASSIGNMENT (0x5704) -- assign PID 0x010 -> port 1
  (3) GET_PID_BINDING          (0x5705) -- verify unbound (pid=0xFFF)
  (4) CONFIGURE_PID_BINDING    (0x5706) -- bind (background command)
  (5) GET_DRT                  (0x5708) -- read DRT[0] entry for DPID 0x010
  (6) SET_DRT                  (0x5709) -- write DRT[0][0x010]=PHYSICAL_PORT->1

Connection model (FmSmbusMctpServer):
  - Single TCP port -- request AND response on the same socket
  - Client connects once, sends frame, reads response on the same reader
  - Unlike dual-port server (8301/8302), no separate master/slave ports

Run with:
  python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py -v -s
  python tests/test_smbus_mctp_server_pbr_cmds.py        # standalone
"""

from __future__ import annotations
import asyncio
import struct
import pytest

from opencis.cxl.component.mctp.fm_smbus_mctp_server import FmSmbusMctpServer
from opencis.cxl.component.mctp.smbus_mctp_framing import SMBUS_MCTP_COMMAND_CODE
from opencis.cxl.cci.common import CCI_RETURN_CODE, CCI_FM_API_COMMAND_OPCODE


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

FM_I2C_ADDR   = 0x10
DEV_I2C_ADDR  = 0x20
FM_EID        = 0x08
DEV_EID       = 0x09
MCTP_HDR_VER  = 0x01
MCTP_MSG_TYPE = 0x7E       # CXL FM API

OPCODE_IDENTIFY = int(CCI_FM_API_COMMAND_OPCODE.IDENTIFY_PBR_SWITCH)      # 0x5700
OPCODE_CFG_PID  = int(CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_ASSIGNMENT) # 0x5704
OPCODE_GET_BIND = int(CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING)          # 0x5705
OPCODE_CFG_BIND = int(CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_BINDING)    # 0x5706
OPCODE_GET_DRT  = int(CCI_FM_API_COMMAND_OPCODE.GET_DRT)                  # 0x5708
OPCODE_SET_DRT  = int(CCI_FM_API_COMMAND_OPCODE.SET_DRT)                  # 0x5709

RESPONSE_TIMEOUT = 30.0

PID           = 0x010
TARGET_PORT   = 1
DRT_INDEX     = 0
VCS_ID        = 0
VPPB_ID       = 0
PID_UNASSIGNED = 0xFFF


# -----------------------------------------------------------------------------
# PEC (SMBus CRC-8, polynomial 0x07)
# -----------------------------------------------------------------------------

def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# -----------------------------------------------------------------------------
# Request payload builders  (real byte layouts from cci/fabric_manager/pbr_switch)
# -----------------------------------------------------------------------------

def build_identify_payload() -> bytes:
    """0x5700 -- no request payload."""
    return b""


def build_configure_pid_assignment_payload(
    pid: int = PID, target_id: int = TARGET_PORT,
    instance_id: int = 0, operation: int = 0,
) -> bytes:
    """0x5704 -- 4-byte header + 1x5-byte PidAssignmentEntry."""
    header = bytearray(4)
    header[0] = operation & 0x07
    struct.pack_into("<H", header, 2, 1)   # num_targets = 1
    entry = bytearray(5)
    struct.pack_into("<H", entry, 0, pid & 0x0FFF)
    struct.pack_into("<H", entry, 2, target_id)
    entry[4] = instance_id & 0xFF
    return bytes(header) + bytes(entry)


def build_get_pid_binding_payload(vcs: int = VCS_ID, vppb: int = VPPB_ID) -> bytes:
    """0x5705 -- 2 bytes: target_vcs, target_vppb."""
    return bytes([vcs & 0xFF, vppb & 0xFF])


def build_configure_pid_binding_payload(
    pid: int = PID, vcs: int = VCS_ID, vppb: int = VPPB_ID, operation: int = 0,
) -> bytes:
    """0x5706 -- 28 bytes (0x1C): operation, target_vcs, target_vppb, reserved, pid, HMAT."""
    data = bytearray(0x1C)
    data[0x00] = operation & 0x07
    data[0x01] = vcs & 0xFF
    data[0x02] = vppb & 0xFF
    struct.pack_into("<H", data, 0x04, pid & 0x0FFF)
    return bytes(data)


def build_get_drt_payload(
    drt_index: int = DRT_INDEX, start_entry: int = PID, num_entries: int = 1,
) -> bytes:
    """0x5708 -- 6 bytes: drt_index, reserved, num_entries(LE), start_entry(LE)."""
    data = bytearray(6)
    data[0x00] = drt_index & 0xFF
    struct.pack_into("<H", data, 0x02, num_entries)
    struct.pack_into("<H", data, 0x04, start_entry)
    return bytes(data)


def build_set_drt_payload(
    drt_index: int = DRT_INDEX, start_entry: int = PID,
    entry_type: int = 1, routing_target: int = TARGET_PORT,
) -> bytes:
    """0x5709 -- 6-byte header + 1x2-byte DrtEntry."""
    header = bytearray(6)
    header[0x00] = drt_index & 0xFF
    struct.pack_into("<H", header, 0x02, 1)
    struct.pack_into("<H", header, 0x04, start_entry)
    return bytes(header) + bytes([entry_type & 0x03, routing_target & 0xFF])


# -----------------------------------------------------------------------------
# Response payload builders (what the mock switch returns)
# -----------------------------------------------------------------------------

def make_identify_response(num_drts: int = 1) -> bytes:
    """12-byte Identify PBR Switch response."""
    data = bytearray(12)
    data[0:8] = (1).to_bytes(8, "little")   # gae_support_map = 1 (VCS 0 has GAE)
    data[8]   = num_drts & 0xFF
    return bytes(data)


def make_get_pid_binding_response(pid: int = PID_UNASSIGNED) -> bytes:
    """24-byte GetPidBinding response."""
    data = bytearray(0x18)
    struct.pack_into("<H", data, 0, pid & 0x0FFF)
    return bytes(data)


def make_get_drt_response() -> bytes:
    """10-byte GetDrt response: 8-byte header + 1x2-byte DrtEntry."""
    header = bytearray(8)
    struct.pack_into("<H", header, 0x02, 1)           # num_entries = 1
    struct.pack_into("<H", header, 0x04, PID)         # start_entry = DPID
    entry = bytes([0x01, TARGET_PORT])                # PHYSICAL_PORT -> port 1
    return bytes(header) + entry


# -----------------------------------------------------------------------------
# Smart mock -- returns the right response for each opcode
# -----------------------------------------------------------------------------

class PbrSmartMock:
    """Simulates the real switch CCI executor for all 6 PBR opcodes."""

    def __init__(self):
        self.calls: list[tuple[int, bytes]] = []

    async def send_raw_cci(self, opcode: int, payload: bytes, port_index: int = 0):
        self.calls.append((opcode, payload))

        if opcode == OPCODE_IDENTIFY:
            return (int(CCI_RETURN_CODE.SUCCESS), make_identify_response(), False)

        elif opcode == OPCODE_CFG_PID:
            return (int(CCI_RETURN_CODE.SUCCESS), b"", False)

        elif opcode == OPCODE_GET_BIND:
            call_num = sum(1 for op, _ in self.calls if op == OPCODE_GET_BIND)
            pid = PID_UNASSIGNED if call_num == 1 else PID
            return (int(CCI_RETURN_CODE.SUCCESS), make_get_pid_binding_response(pid), False)

        elif opcode == OPCODE_CFG_BIND:
            return (int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED), b"", True)

        elif opcode == OPCODE_GET_DRT:
            return (int(CCI_RETURN_CODE.SUCCESS), make_get_drt_response(), False)

        elif opcode == OPCODE_SET_DRT:
            return (int(CCI_RETURN_CODE.SUCCESS), b"", False)

        else:
            return (int(CCI_RETURN_CODE.UNSUPPORTED), b"", False)


# -----------------------------------------------------------------------------
# Frame builder / parser (single-port: request and response same socket)
# -----------------------------------------------------------------------------

def build_request_frame(
    opcode: int,
    cci_payload: bytes = b"",
    cci_tag: int = 0,
    msg_tag: int = 0,
) -> bytes:
    """Build a DSP0237 SMBus+MCTP request frame."""
    plen = len(cci_payload)
    cci_hdr = bytearray(12)
    cci_hdr[0] = 0x00                     # REQUEST
    cci_hdr[1] = cci_tag & 0xFF
    cci_hdr[3] = opcode & 0xFF
    cci_hdr[4] = (opcode >> 8) & 0xFF
    cci_hdr[5] = plen & 0xFF
    cci_hdr[6] = (plen >> 8) & 0xFF
    cci_hdr[7] = (plen >> 16) & 0x1F
    cci_msg = bytes(cci_hdr) + cci_payload

    flags = 0xC0 | 0x08 | (msg_tag & 0x7)  # SOM|EOM|TO
    body = bytes([
        (DEV_I2C_ADDR << 1) | 0x01,
        MCTP_HDR_VER, FM_EID, DEV_EID, flags, MCTP_MSG_TYPE,
    ]) + cci_msg

    dest_addr  = (FM_I2C_ADDR << 1) & 0xFE
    byte_count = len(body)
    frame      = bytes([dest_addr, SMBUS_MCTP_COMMAND_CODE, byte_count]) + body
    return frame + bytes([_crc8(frame)])


async def recv_smbus_response(reader: asyncio.StreamReader) -> bytes:
    """
    Read one complete SMBus+MCTP response frame.
    Response is length-prefixed: first byte = byte_count, then byte_count+1 bytes.
    """
    bc   = await reader.readexactly(1)
    rest = await reader.readexactly(bc[0] + 1)   # body + PEC
    return bc + rest


def parse_response(raw: bytes) -> dict:
    """Parse raw SMBus+MCTP response frame into named fields."""
    assert len(raw) >= 20, f"Response too short: {len(raw)} bytes"
    cci_hdr     = raw[7:19]
    plen        = int.from_bytes(cci_hdr[5:7], "little") | ((cci_hdr[7] & 0x1F) << 16)
    return_code = int.from_bytes(cci_hdr[8:10], "little")
    return {
        "byte_count":  raw[0],
        "fm_src_addr": raw[1],
        "dest_eid":    raw[3],
        "src_eid":     raw[4],
        "msg_tag":     raw[5] & 0x7,
        "msg_type":    raw[6] & 0x7F,
        "category":    cci_hdr[0] & 0x0F,
        "cci_tag":     cci_hdr[1],
        "opcode":      int.from_bytes(cci_hdr[3:5], "little"),
        "return_code": return_code,
        "background":  bool(cci_hdr[7] >> 7),
        "plen":        plen,
        "cci_payload": raw[19:19 + plen],
        "pec":         raw[-1],
        "pec_ok":      raw[-1] == _crc8(raw[:-1]),
    }


# -----------------------------------------------------------------------------
# Client-side pretty printer (what the client received from the server)
# -----------------------------------------------------------------------------

_OPCODE_NAMES = {
    OPCODE_IDENTIFY: "IDENTIFY_PBR_SWITCH",
    OPCODE_CFG_PID:  "CONFIGURE_PID_ASSIGNMENT",
    OPCODE_GET_BIND: "GET_PID_BINDING",
    OPCODE_CFG_BIND: "CONFIGURE_PID_BINDING",
    OPCODE_GET_DRT:  "GET_DRT",
    OPCODE_SET_DRT:  "SET_DRT",
}

_RC_NAMES = {
    0: "SUCCESS",
    1: "BACKGROUND_COMMAND_STARTED",
    2: "INVALID_INPUT",
    3: "UNSUPPORTED",
    4: "INTERNAL_ERROR",
}

def _hex_dump(data: bytes, indent: str = "    ") -> str:
    if not data:
        return f"{indent}(empty)"
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{i:04X}  {hex_part:<47}  |{asc_part}|")
    return "\n".join(lines)


def print_client_received(resp: dict) -> None:
    """Print what the client received -- mirrors server _print_tx_packet style."""
    opcode  = resp["opcode"]
    rc      = resp["return_code"]
    rc_str  = _RC_NAMES.get(rc, f"0x{rc:04X}")
    is_bg   = resp["background"]
    payload = resp["cci_payload"]
    pec_ok  = resp["pec_ok"]

    rc_color = "\033[92m" if rc == 0 else "\033[93m" if rc == 1 else "\033[91m"
    pec_str  = "\033[92mOK\033[0m" if pec_ok else "\033[91mBAD\033[0m"
    bg_str   = "\033[93mYES (background)\033[0m" if is_bg else "no"

    print(f"\033[1m\033[95m  <-- CLIENT RECEIVED (same socket -- single port)\033[0m")
    print(f"    opcode      : 0x{opcode:04X}  ({_OPCODE_NAMES.get(opcode, '?')})")
    print(f"    return_code : {rc_color}{rc_str}\033[0m  (0x{rc:04X})")
    print(f"    background  : {bg_str}")
    print(f"    payload_len : {len(payload)} bytes")
    print(f"    pec         : [{pec_str}]")

    if payload and opcode == OPCODE_IDENTIFY:
        gae_map  = int.from_bytes(payload[0:8], "little")
        num_drts = payload[8]
        num_rgts = payload[9]
        print("\033[95m  -- Identify PBR Switch Response --\033[0m")
        print(f"    gae_support_map : 0x{gae_map:016X}")
        print(f"    num_drts        : {num_drts}")
        print(f"    num_rgts        : {num_rgts}")

    elif payload and opcode == OPCODE_GET_BIND:
        pid = struct.unpack_from("<H", payload, 0)[0] & 0x0FFF
        bound = "UNBOUND" if pid == 0xFFF else f"BOUND -> PID 0x{pid:03X}"
        print("\033[95m  -- GetPidBinding Response --\033[0m")
        print(f"    pid : 0x{pid:03X}  ({bound})")

    elif payload and opcode == OPCODE_GET_DRT:
        num_ent   = struct.unpack_from("<H", payload, 2)[0]
        start_ent = struct.unpack_from("<H", payload, 4)[0]
        print("\033[95m  -- GetDrt Response --\033[0m")
        print(f"    num_entries : {num_ent}")
        print(f"    start_entry : 0x{start_ent:03X}  (DPID)")
        for i in range(num_ent):
            off = 8 + i * 2
            if off + 2 <= len(payload):
                etype  = payload[off] & 0x03
                target = payload[off + 1]
                etype_str = {0: "INVALID", 1: "PHYSICAL_PORT",
                             2: "RGT_INDEX", 3: "RESERVED"}.get(etype, "?")
                print(f"    entry[{i}] : type={etype_str}  target={target}")

    elif payload:
        print("\033[95m  -- Payload --\033[0m")
        print(_hex_dump(payload))

    print(f"\033[1m{'-' * 60}\033[0m\n")


def print_cmd_banner(cmd_num: int, opcode: int, payload: bytes) -> None:
    print(f"\n\033[1m\033[96m{'=' * 60}")
    print(f"  (7){cmd_num}  {_OPCODE_NAMES.get(opcode, f'0x{opcode:04X}')}  "
          f"(opcode 0x{opcode:04X})")
    print(f"{'=' * 60}\033[0m")
    if payload:
        print("\033[2m  Request payload:\033[0m")
        print(_hex_dump(payload))
    else:
        print("\033[2m  Request payload: (none)\033[0m")
    print(f"\033[2m{'-' * 60}\033[0m")


# -----------------------------------------------------------------------------
# Server lifecycle helpers
# -----------------------------------------------------------------------------

async def _start_server(mock):
    server = FmSmbusMctpServer(
        host="127.0.0.1",
        port=0,              # OS assigns free port
        mctp_client=mock,
        fm_i2c_addr=FM_I2C_ADDR,
    )
    task = asyncio.create_task(server.run())
    await server.wait_for_ready()
    port = server.get_port()
    assert port > 0
    return server, port, task


async def _stop_server(server, task):
    await server._stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# -----------------------------------------------------------------------------
# Core: send one command and receive response on the SAME connection
# -----------------------------------------------------------------------------

async def send_pbr_command(
    port: int,
    opcode: int,
    request_payload: bytes,
    cci_tag: int = 0,
    msg_tag: int = 0,
) -> dict:
    """
    Single-port round-trip (FmSmbusMctpServer):
      1. Connect to port
      2. Send request frame
      3. Read response on SAME connection (server writes back)
      4. Disconnect
    """
    cmd_num = list(_OPCODE_NAMES.keys()).index(opcode) + 1
    print_cmd_banner(cmd_num, opcode, request_payload)

    frame = build_request_frame(opcode, request_payload, cci_tag, msg_tag)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(frame)
    await writer.drain()

    # Server processes and writes response on SAME socket
    # _print_tx_packet fires server-side here
    raw = await asyncio.wait_for(recv_smbus_response(reader), timeout=RESPONSE_TIMEOUT)

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    resp = parse_response(raw)
    print_client_received(resp)
    return resp


# -----------------------------------------------------------------------------
# -- pytest: all 6 PBR commands in sequence -----------------------------------
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smbus_mctp_server_all_pbr_commands():
    """
    Full GFD commissioning sequence over FmSmbusMctpServer (single-port).
    Sends all 6 PBR CCI commands with realistic payloads.
    The server's _print_tx_packet fires for every TX -- printed in the
    same green format as _print_rx_packet (cyan).
    """
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)

    print(f"\n\033[1m\033[93m{'=' * 60}")
    print("  GFD Commissioning via FmSmbusMctpServer (single-port)")
    print(f"  port={port}")
    print(f"{'=' * 60}\033[0m")

    try:
        # (1) Identify PBR Switch
        r = await send_pbr_command(port, OPCODE_IDENTIFY, build_identify_payload(),
                                   cci_tag=1, msg_tag=1)
        assert r["return_code"] == 0, f"IDENTIFY rc={r['return_code']}"
        assert r["opcode"] == OPCODE_IDENTIFY
        assert len(r["cci_payload"]) == 12
        assert r["cci_payload"][8] >= 1, "num_drts must be >= 1"
        assert r["pec_ok"], "PEC invalid"
        assert r["background"] is False

        # (2) Configure PID Assignment
        r = await send_pbr_command(
            port, OPCODE_CFG_PID,
            build_configure_pid_assignment_payload(PID, TARGET_PORT),
            cci_tag=2, msg_tag=2,
        )
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
        assert r["background"] is False

        # (3) Get PID Binding -- expect unbound (0xFFF)
        r = await send_pbr_command(
            port, OPCODE_GET_BIND,
            build_get_pid_binding_payload(VCS_ID, VPPB_ID),
            cci_tag=3, msg_tag=3,
        )
        assert r["return_code"] == 0
        assert len(r["cci_payload"]) == 24
        pid_val = struct.unpack_from("<H", r["cci_payload"], 0)[0] & 0x0FFF
        assert pid_val == PID_UNASSIGNED, f"Expected 0xFFF, got 0x{pid_val:03X}"

        # (4) Configure PID Binding -- background
        r = await send_pbr_command(
            port, OPCODE_CFG_BIND,
            build_configure_pid_binding_payload(PID, VCS_ID, VPPB_ID, operation=0),
            cci_tag=4, msg_tag=4,
        )
        assert r["return_code"] == int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED)
        assert r["background"] is True

        # (5) Get DRT
        r = await send_pbr_command(
            port, OPCODE_GET_DRT,
            build_get_drt_payload(DRT_INDEX, PID, 1),
            cci_tag=5, msg_tag=5,
        )
        assert r["return_code"] == 0
        assert len(r["cci_payload"]) >= 10
        assert r["cci_payload"][8] & 0x03 == 1, "DRT entry_type must be PHYSICAL_PORT"
        assert r["cci_payload"][9] == TARGET_PORT

        # (6) Set DRT
        r = await send_pbr_command(
            port, OPCODE_SET_DRT,
            build_set_drt_payload(DRT_INDEX, PID, 1, TARGET_PORT),
            cci_tag=6, msg_tag=6,
        )
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
        assert r["background"] is False

        assert len(mock.calls) == 6

        print(f"\n\033[1m\033[92m{'=' * 60}")
        print(f"  PASSED  All 6 PBR commands PASSED  "
              f"({len(mock.calls)} send_raw_cci calls)")
        print(f"{'=' * 60}\033[0m\n")

    finally:
        await _stop_server(server, task)


# -----------------------------------------------------------------------------
# Individual command tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smbus_mctp_identify():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(port, OPCODE_IDENTIFY, b"")
        assert r["return_code"] == 0
        assert len(r["cci_payload"]) == 12
        assert r["cci_payload"][8] >= 1   # num_drts
        assert r["pec_ok"]
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_smbus_mctp_configure_pid_assignment():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(
            port, OPCODE_CFG_PID,
            build_configure_pid_assignment_payload(PID, TARGET_PORT),
        )
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_smbus_mctp_get_pid_binding_unbound():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(
            port, OPCODE_GET_BIND,
            build_get_pid_binding_payload(VCS_ID, VPPB_ID),
        )
        assert r["return_code"] == 0
        pid_val = struct.unpack_from("<H", r["cci_payload"], 0)[0] & 0x0FFF
        assert pid_val == PID_UNASSIGNED
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_smbus_mctp_configure_pid_binding_background():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(
            port, OPCODE_CFG_BIND,
            build_configure_pid_binding_payload(PID, VCS_ID, VPPB_ID),
        )
        assert r["return_code"] == int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED)
        assert r["background"] is True
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_smbus_mctp_get_drt():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(
            port, OPCODE_GET_DRT,
            build_get_drt_payload(DRT_INDEX, PID, 1),
        )
        assert r["return_code"] == 0
        assert r["cci_payload"][8] & 0x03 == 1   # PHYSICAL_PORT
        assert r["cci_payload"][9] == TARGET_PORT
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_smbus_mctp_set_drt():
    mock = PbrSmartMock()
    server, port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(
            port, OPCODE_SET_DRT,
            build_set_drt_payload(DRT_INDEX, PID, 1, TARGET_PORT),
        )
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
    finally:
        await _stop_server(server, task)


# -----------------------------------------------------------------------------
# Standalone entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("\033[1m\033[93mRunning PBR commissioning over FmSmbusMctpServer\033[0m")
    asyncio.run(test_smbus_mctp_server_all_pbr_commands())
    print("\033[1m\033[92mDone.\033[0m")
