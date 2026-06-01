"""
tests/test_smbus_dual_port_pbr_cmds.py
========================================
Invokes ALL 6 PBR switch CCI commands over the FmSmbusDualPortServer
(ports 8301/8302) using realistic request payloads, and prints the
formatted TX response for each command on both the server side
(_print_tx_packet) and the client side (parse_and_print_response).

Commands tested (full GFD commissioning sequence):
  ① IDENTIFY_PBR_SWITCH    (0x5700) — learn num_drts, gae_support_map
  ② CONFIGURE_PID_ASSIGNMENT(0x5704) — assign PID 0x010 → port 1
  ③ GET_PID_BINDING         (0x5705) — verify unbound (pid=0xFFF)
  ④ CONFIGURE_PID_BINDING   (0x5706) — bind (background command)
  ⑤ GET_DRT                 (0x5708) — read DRT[0] entry for DPID 0x010
  ⑥ SET_DRT                 (0x5709) — write DRT[0][0x010]=PHYSICAL_PORT→1

Response payload byte layout used here is the REAL layout from the
cci/fabric_manager/pbr_switch/ command files (no guessing).

Run with:
  python -m pytest tests/test_smbus_dual_port_pbr_cmds.py -v -s
  python tests/test_smbus_dual_port_pbr_cmds.py        # standalone
"""

from __future__ import annotations
import asyncio
import struct
import sys
import pytest

from opencis.cxl.component.mctp.fm_smbus_dual_port_server import FmSmbusDualPortServer
from opencis.cxl.component.mctp.smbus_mctp_framing import SMBUS_MCTP_COMMAND_CODE
from opencis.cxl.cci.common import CCI_RETURN_CODE, CCI_FM_API_COMMAND_OPCODE


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

FM_I2C_ADDR   = 0x10
DEV_I2C_ADDR  = 0x20
FM_EID        = 0x08
DEV_EID       = 0x09
MCTP_HDR_VER  = 0x01
MCTP_MSG_TYPE = 0x7E       # CXL FM API

OPCODE_IDENTIFY    = int(CCI_FM_API_COMMAND_OPCODE.IDENTIFY_PBR_SWITCH)     # 0x5700
OPCODE_CFG_PID     = int(CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_ASSIGNMENT)# 0x5704
OPCODE_GET_BIND    = int(CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING)         # 0x5705
OPCODE_CFG_BIND    = int(CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_BINDING)   # 0x5706
OPCODE_GET_DRT     = int(CCI_FM_API_COMMAND_OPCODE.GET_DRT)                 # 0x5708
OPCODE_SET_DRT     = int(CCI_FM_API_COMMAND_OPCODE.SET_DRT)                 # 0x5709

RESPONSE_TIMEOUT = 30.0

# DRT / PID constants
PID           = 0x010   # The PID we assign in this commissioning example
TARGET_PORT   = 1       # Physical switch port connected to the GFD
DRT_INDEX     = 0
VCS_ID        = 0
VPPB_ID       = 0
PID_UNASSIGNED = 0xFFF


# ─────────────────────────────────────────────────────────────────────────────
# PEC (SMBus CRC-8, polynomial 0x07)
# ─────────────────────────────────────────────────────────────────────────────

def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ─────────────────────────────────────────────────────────────────────────────
# ── Realistic request payload builders ───────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def build_identify_request_payload() -> bytes:
    """Opcode 0x5700 — no request payload."""
    return b""


def build_configure_pid_assignment_payload(
    pid: int = PID,
    target_id: int = TARGET_PORT,
    instance_id: int = 0,
    operation: int = 0,   # 0=ASSIGN, 1=CLEAR
) -> bytes:
    """
    Opcode 0x5704 — ConfigurePidAssignment request payload.
    Layout: 4-byte header + N×5-byte PidAssignmentEntry
      Byte  0   : operation (bits[2:0])
      Byte  1   : reserved
      Bytes 2-3 : num_targets (uint16 LE)
      Per entry (5 bytes):
        Bytes 0-1 : pid (uint16 LE, bits[11:0])
        Bytes 2-3 : target_id (uint16 LE)
        Byte  4   : instance_id (uint8)
    """
    header = bytearray(4)
    header[0] = operation & 0x07
    # header[1] reserved = 0
    struct.pack_into("<H", header, 2, 1)  # num_targets = 1

    entry = bytearray(5)
    struct.pack_into("<H", entry, 0, pid & 0x0FFF)
    struct.pack_into("<H", entry, 2, target_id)
    entry[4] = instance_id & 0xFF

    return bytes(header) + bytes(entry)


def build_get_pid_binding_payload(
    vcs_id: int = VCS_ID,
    vppb_id: int = VPPB_ID,
) -> bytes:
    """
    Opcode 0x5705 — GetPidBinding request payload.
    Layout: 2 bytes
      Byte 0 : target_vcs  (uint8)
      Byte 1 : target_vppb (uint8)
    """
    return bytes([vcs_id & 0xFF, vppb_id & 0xFF])


def build_configure_pid_binding_payload(
    pid: int = PID,
    vcs_id: int = VCS_ID,
    vppb_id: int = VPPB_ID,
    operation: int = 0,   # 0=BIND, 1=UNBIND
) -> bytes:
    """
    Opcode 0x5706 — ConfigurePidBinding request payload.
    Layout: 28 bytes (0x1C)
      Byte  0x00 : operation (bits[2:0])
      Byte  0x01 : target_vcs
      Byte  0x02 : target_vppb
      Byte  0x03 : reserved
      Bytes 0x04-0x05 : pid (uint16 LE, bits[11:0])
      Bytes 0x06-0x07 : reserved
      Bytes 0x08-0x17 : HMAT latency+bw fields (uint64+uint16+uint64+uint16)
      (all zero for our test)
    """
    data = bytearray(0x1C)
    data[0x00] = operation & 0x07
    data[0x01] = vcs_id & 0xFF
    data[0x02] = vppb_id & 0xFF
    # 0x03 reserved
    struct.pack_into("<H", data, 0x04, pid & 0x0FFF)
    # 0x06-0x1B: HMAT fields (all zero for our test)
    return bytes(data)


def build_get_drt_payload(
    drt_index: int = DRT_INDEX,
    start_entry: int = PID,    # we want the entry for DPID=PID
    num_entries: int = 1,
) -> bytes:
    """
    Opcode 0x5708 — GetDrt request payload.
    Layout: 6 bytes
      Byte  0x00 : drt_index (uint8)
      Byte  0x01 : reserved
      Bytes 0x02-0x03 : num_entries (uint16 LE)
      Bytes 0x04-0x05 : start_entry (uint16 LE)
    """
    data = bytearray(6)
    data[0x00] = drt_index & 0xFF
    # 0x01 reserved
    struct.pack_into("<H", data, 0x02, num_entries)
    struct.pack_into("<H", data, 0x04, start_entry)
    return bytes(data)


def build_set_drt_payload(
    drt_index: int = DRT_INDEX,
    start_entry: int = PID,
    entry_type: int = 1,         # 1 = PHYSICAL_PORT
    routing_target: int = TARGET_PORT,
) -> bytes:
    """
    Opcode 0x5709 — SetDrt request payload.
    Layout: 6-byte header + N×2-byte DrtEntry
      Byte  0x00 : drt_index (uint8)
      Byte  0x01 : reserved
      Bytes 0x02-0x03 : num_entries (uint16 LE)
      Bytes 0x04-0x05 : start_entry (uint16 LE)
      Per DrtEntry (2 bytes):
        Byte 0 : entry_type (bits[1:0] = DrtEntryType)
        Byte 1 : routing_target (physical port or RGT index)
    """
    header = bytearray(6)
    header[0x00] = drt_index & 0xFF
    struct.pack_into("<H", header, 0x02, 1)           # num_entries = 1
    struct.pack_into("<H", header, 0x04, start_entry)

    entry = bytearray(2)
    entry[0] = entry_type & 0x03
    entry[1] = routing_target & 0xFF

    return bytes(header) + bytes(entry)


# ─────────────────────────────────────────────────────────────────────────────
# ── Realistic RESPONSE payload builders (what the switch returns) ─────────────
# ─────────────────────────────────────────────────────────────────────────────

def make_identify_response_payload(
    num_drts: int = 1,
    num_rgts: int = 0,
    gae_support_map: int = 1,   # bit 0 = VCS 0 has GAE
    routing_caps: int = 0,
) -> bytes:
    """Identify PBR Switch — 12-byte response."""
    data = bytearray(12)
    data[0x00:0x08] = gae_support_map.to_bytes(8, "little")
    data[0x08] = num_drts & 0xFF
    data[0x09] = num_rgts & 0xFF
    # 0x0A reserved = 0
    data[0x0B] = routing_caps & 0xFF
    return bytes(data)


def make_get_pid_binding_response(pid: int = PID_UNASSIGNED) -> bytes:
    """GetPidBinding — 24-byte response (pid + HMAT fields, all zero except pid)."""
    data = bytearray(0x18)
    struct.pack_into("<H", data, 0x00, pid & 0x0FFF)
    return bytes(data)


def make_get_drt_response(
    drt_index: int = DRT_INDEX,
    start_entry: int = PID,
    entry_type: int = 1,      # PHYSICAL_PORT
    routing_target: int = TARGET_PORT,
) -> bytes:
    """GetDrt — 10-byte response (8-byte header + 1×2-byte DrtEntry)."""
    data = bytearray(8)
    data[0x00] = drt_index & 0xFF
    struct.pack_into("<H", data, 0x02, 1)           # num_entries = 1
    struct.pack_into("<H", data, 0x04, start_entry)
    # 0x06 associated_rgt_index = 0
    # 0x07 reserved = 0
    drt_entry = bytes([entry_type & 0x03, routing_target & 0xFF])
    return bytes(data) + drt_entry


# ─────────────────────────────────────────────────────────────────────────────
# ── Smart Mock: returns the right response for each opcode ───────────────────
# ─────────────────────────────────────────────────────────────────────────────

class PbrSmartMockClient:
    """
    Mock MctpCciApiClient that returns a realistic response for each
    PBR switch opcode, simulating what the real switch CCI executor returns.
    """

    def __init__(self):
        self.calls: list[tuple[int, bytes]] = []

    async def send_raw_cci(self, opcode: int, payload: bytes, port_index: int = 0):
        self.calls.append((opcode, payload))

        if opcode == OPCODE_IDENTIFY:       # 0x5700
            return (int(CCI_RETURN_CODE.SUCCESS),
                    make_identify_response_payload(), False)

        elif opcode == OPCODE_CFG_PID:      # 0x5704
            return (int(CCI_RETURN_CODE.SUCCESS), b"", False)

        elif opcode == OPCODE_GET_BIND:     # 0x5705
            # Return 0xFFF (unbound) for first call, PID for later calls
            call_num = sum(1 for op, _ in self.calls if op == OPCODE_GET_BIND)
            pid = PID_UNASSIGNED if call_num == 1 else PID
            return (int(CCI_RETURN_CODE.SUCCESS),
                    make_get_pid_binding_response(pid), False)

        elif opcode == OPCODE_CFG_BIND:     # 0x5706 — background
            return (int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED), b"", True)

        elif opcode == OPCODE_GET_DRT:      # 0x5708
            return (int(CCI_RETURN_CODE.SUCCESS),
                    make_get_drt_response(), False)

        elif opcode == OPCODE_SET_DRT:      # 0x5709
            return (int(CCI_RETURN_CODE.SUCCESS), b"", False)

        else:
            return (int(CCI_RETURN_CODE.UNSUPPORTED), b"", False)


# ─────────────────────────────────────────────────────────────────────────────
# ── Low-level frame helpers ──────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def build_slave_request(
    opcode: int,
    cci_payload: bytes = b"",
    cci_tag: int = 0,
    msg_tag: int = 0,
) -> bytes:
    """Build a DSP0237 SMBus+MCTP request frame."""
    plen = len(cci_payload)
    cci_hdr = bytearray(12)
    cci_hdr[0] = 0x00                  # REQUEST category
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
    pec        = _crc8(frame)
    return frame + bytes([pec])


async def _recv_smbus_resp(reader: asyncio.StreamReader) -> bytes:
    """Read one complete SMBus+MCTP response frame (length-prefixed)."""
    bc_byte    = await reader.readexactly(1)
    byte_count = bc_byte[0]
    rest       = await reader.readexactly(byte_count + 1)  # body + PEC
    return bc_byte + rest


def parse_response(raw: bytes) -> dict:
    """Parse raw SMBus+MCTP response into named fields."""
    assert len(raw) >= 20, f"Response too short: {len(raw)} bytes"
    cci_hdr     = raw[7:19]
    plen        = int.from_bytes(cci_hdr[5:7], "little") | ((cci_hdr[7] & 0x1F) << 16)
    return_code = int.from_bytes(cci_hdr[8:10], "little")
    return {
        "byte_count":   raw[0],
        "fm_src_addr":  raw[1],
        "dest_eid":     raw[3],
        "src_eid":      raw[4],
        "msg_tag":      raw[5] & 0x7,
        "msg_type":     raw[6] & 0x7F,
        "category":     cci_hdr[0] & 0x0F,
        "cci_tag":      cci_hdr[1],
        "opcode":       int.from_bytes(cci_hdr[3:5], "little"),
        "return_code":  return_code,
        "background":   bool(cci_hdr[7] >> 7),
        "plen":         plen,
        "cci_payload":  raw[19:19 + plen],
        "pec":          raw[-1],
        "pec_ok":       raw[-1] == _crc8(raw[:-1]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ── Client-side formatted printer (mirrors server _print_tx_packet) ──────────
# ─────────────────────────────────────────────────────────────────────────────

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


def _decode_identify_response(payload: bytes) -> str:
    if len(payload) < 12:
        return "  (payload too short)"
    gae_map  = int.from_bytes(payload[0:8], "little")
    num_drts = payload[8]
    num_rgts = payload[9]
    routing  = payload[11]
    return (
        f"    gae_support_map : 0x{gae_map:016X}\n"
        f"    num_drts        : {num_drts}\n"
        f"    num_rgts        : {num_rgts}\n"
        f"    routing_caps    : 0x{routing:02X}"
    )


def _decode_get_pid_binding_response(payload: bytes) -> str:
    if len(payload) < 24:
        return "  (payload too short)"
    pid = struct.unpack_from("<H", payload, 0)[0] & 0x0FFF
    bound = "UNBOUND" if pid == 0xFFF else f"BOUND → PID 0x{pid:03X}"
    return f"    pid             : 0x{pid:03X}  ({bound})"


def _decode_get_drt_response(payload: bytes) -> str:
    if len(payload) < 10:
        return "  (payload too short)"
    drt_idx    = payload[0]
    num_ent    = struct.unpack_from("<H", payload, 2)[0]
    start_ent  = struct.unpack_from("<H", payload, 4)[0]
    assoc_rgt  = payload[6]
    lines = [
        f"    drt_index       : {drt_idx}",
        f"    num_entries     : {num_ent}",
        f"    start_entry     : 0x{start_ent:03X}  (DPID)",
        f"    assoc_rgt_index : {assoc_rgt}",
    ]
    for i in range(num_ent):
        off = 8 + i * 2
        if off + 2 > len(payload):
            break
        entry_type = payload[off] & 0x03
        target     = payload[off + 1]
        etype_str  = {0: "INVALID", 1: "PHYSICAL_PORT", 2: "RGT_INDEX", 3: "RESERVED"}.get(entry_type, "?")
        lines.append(f"    entry[{i}]         : type={etype_str}  target={target}")
    return "\n".join(lines)


def print_rx_cmd_banner(cmd_num: int, opcode: int, payload: bytes) -> None:
    """Print a banner for the command we are about to send."""
    name = _OPCODE_NAMES.get(opcode, f"0x{opcode:04X}")
    sep  = "─" * 62
    print(f"\n\033[1m\033[96m{'━' * 62}")
    print(f"  ⑦{cmd_num}  {name}  (opcode 0x{opcode:04X})")
    print(f"{'━' * 62}\033[0m")
    if payload:
        print("\033[2m  Request payload:\033[0m")
        print(_hex_dump(payload))
    else:
        print("\033[2m  Request payload: (none)\033[0m")
    print(f"\033[2m{sep}\033[0m")


def print_client_rx(resp: dict) -> None:
    """Print the CLIENT's view of the received (TX from server) response."""
    opcode = resp["opcode"]
    rc     = resp["return_code"]
    rc_str = _RC_NAMES.get(rc, f"0x{rc:04X}")
    is_bg  = resp["background"]
    payload = resp["cci_payload"]
    pec_ok  = resp["pec_ok"]

    rc_color = "\033[92m" if rc == 0 else "\033[93m" if rc == 1 else "\033[91m"
    pec_str  = "\033[92mOK\033[0m" if pec_ok else "\033[91mBAD\033[0m"
    bg_str   = "\033[93mYES (background)\033[0m" if is_bg else "no"

    print(f"\033[1m\033[95m  ◄─ CLIENT RECEIVED (from master port 8302)\033[0m")
    print(f"    opcode      : 0x{opcode:04X}  ({_OPCODE_NAMES.get(opcode, '?')})")
    print(f"    return_code : {rc_color}{rc_str}\033[0m  (0x{rc:04X})")
    print(f"    background  : {bg_str}")
    print(f"    payload_len : {len(payload)} bytes")
    print(f"    pec         : [{pec_str}]")

    # Opcode-specific payload decode
    if payload and opcode == OPCODE_IDENTIFY:
        print("\033[95m  -- Identify Response --\033[0m")
        print(_decode_identify_response(payload))
    elif payload and opcode == OPCODE_GET_BIND:
        print("\033[95m  -- GetPidBinding Response --\033[0m")
        print(_decode_get_pid_binding_response(payload))
    elif payload and opcode == OPCODE_GET_DRT:
        print("\033[95m  -- GetDrt Response --\033[0m")
        print(_decode_get_drt_response(payload))
    elif payload:
        print("\033[95m  -- Payload --\033[0m")
        print(_hex_dump(payload))

    print(f"\033[1m{'─' * 62}\033[0m\n")


# ─────────────────────────────────────────────────────────────────────────────
# ── Server lifecycle helpers ──────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def _start_server(mock_client):
    server = FmSmbusDualPortServer(
        host="127.0.0.1",
        req_port=0,      # OS assigns free port
        resp_port=0,
        mctp_client=mock_client,
        fm_i2c_addr=FM_I2C_ADDR,
    )
    task = asyncio.create_task(server.run())
    await server.wait_for_ready()
    return server, server.get_req_port(), server.get_resp_port(), task


async def _stop_server(server, task):
    await server._stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# ── Core: send one command and receive formatted response ─────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def send_pbr_command(
    req_port: int,
    resp_port: int,
    opcode: int,
    request_payload: bytes,
    cci_tag: int = 0,
    msg_tag: int = 0,
) -> dict:
    """
    Full round-trip (QEMU-style per-request connections):
      1. Connect master to resp_port FIRST
      2. Connect slave to req_port, send frame
      3. Master reads response
      4. Both disconnect
    Returns parsed response dict.
    """
    cmd_name = _OPCODE_NAMES.get(opcode, f"0x{opcode:04X}")
    print_rx_cmd_banner(list(_OPCODE_NAMES.keys()).index(opcode) + 1,
                        opcode, request_payload)

    # Build request frame
    frame = build_slave_request(opcode, request_payload, cci_tag, msg_tag)

    # Step 1: Master connects first
    m_reader, m_writer = await asyncio.open_connection("127.0.0.1", resp_port)

    # Step 2: Slave connects and sends
    s_reader, s_writer = await asyncio.open_connection("127.0.0.1", req_port)
    s_writer.write(frame)
    await s_writer.drain()

    # Step 3: Master reads response (server-side _print_tx_packet fires here)
    raw = await asyncio.wait_for(
        _recv_smbus_resp(m_reader), timeout=RESPONSE_TIMEOUT
    )

    # Step 4: Both disconnect (QEMU model)
    s_writer.close()
    m_writer.close()
    try:
        await s_writer.wait_closed()
        await m_writer.wait_closed()
    except Exception:
        pass

    resp = parse_response(raw)
    print_client_rx(resp)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# ── pytest test: all 6 PBR commands in commissioning order ───────────────────
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pbr_all_commands_commissioning_sequence():
    """
    Full GFD commissioning sequence over the dual-port SMBus server.
    Sends all 6 PBR CCI commands with realistic payloads and verifies
    each response.  The server's _print_tx_packet fires for every TX.
    """
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)

    print(f"\n\033[1m\033[93m{'═' * 62}")
    print("  GFD Commissioning via SMBus Dual-Port (8301/8302)")
    print(f"  req_port={req_port}  resp_port={resp_port}")
    print(f"{'═' * 62}\033[0m")

    try:
        # ① Identify PBR Switch — learn num_drts
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_IDENTIFY,
            request_payload=build_identify_request_payload(),
            cci_tag=1, msg_tag=1,
        )
        assert r["return_code"] == 0,       f"IDENTIFY: rc={r['return_code']}"
        assert r["opcode"]      == OPCODE_IDENTIFY
        assert len(r["cci_payload"]) == 12, "IDENTIFY: payload must be 12 bytes"
        assert r["pec_ok"],                 "IDENTIFY: PEC invalid"
        assert r["background"]  is False,   "IDENTIFY: must be foreground"
        num_drts = r["cci_payload"][8]
        assert num_drts >= 1,               f"IDENTIFY: num_drts={num_drts} must be >= 1"

        # ② Configure PID Assignment — assign PID 0x010 → port 1
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_CFG_PID,
            request_payload=build_configure_pid_assignment_payload(PID, TARGET_PORT),
            cci_tag=2, msg_tag=2,
        )
        assert r["return_code"] == 0,          f"CFG_PID: rc={r['return_code']}"
        assert r["opcode"]      == OPCODE_CFG_PID
        assert r["cci_payload"] == b"",        "CFG_PID: response payload must be empty"
        assert r["background"]  is False

        # ③ Get PID Binding — verify unbound (pid=0xFFF)
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_GET_BIND,
            request_payload=build_get_pid_binding_payload(VCS_ID, VPPB_ID),
            cci_tag=3, msg_tag=3,
        )
        assert r["return_code"] == 0,          f"GET_BIND: rc={r['return_code']}"
        assert r["opcode"]      == OPCODE_GET_BIND
        assert len(r["cci_payload"]) == 24,    "GET_BIND: payload must be 24 bytes"
        pid_val = struct.unpack_from("<H", r["cci_payload"], 0)[0] & 0x0FFF
        assert pid_val == PID_UNASSIGNED,      f"GET_BIND: expected 0xFFF, got 0x{pid_val:03X}"

        # ④ Configure PID Binding — BIND (background)
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_CFG_BIND,
            request_payload=build_configure_pid_binding_payload(
                PID, VCS_ID, VPPB_ID, operation=0
            ),
            cci_tag=4, msg_tag=4,
        )
        assert r["return_code"] == int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED), \
            f"CFG_BIND: expected BACKGROUND, got rc={r['return_code']}"
        assert r["background"]  is True, "CFG_BIND: background bit must be 1"
        assert r["opcode"]      == OPCODE_CFG_BIND

        # ⑤ Get DRT — read entry for DPID 0x010
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_GET_DRT,
            request_payload=build_get_drt_payload(DRT_INDEX, PID, 1),
            cci_tag=5, msg_tag=5,
        )
        assert r["return_code"] == 0,          f"GET_DRT: rc={r['return_code']}"
        assert r["opcode"]      == OPCODE_GET_DRT
        assert len(r["cci_payload"]) >= 10,    "GET_DRT: payload too short"
        # Check DRT entry: PHYSICAL_PORT → port 1
        entry_type = r["cci_payload"][8] & 0x03
        target_port = r["cci_payload"][9]
        assert entry_type   == 1,          f"GET_DRT: entry_type={entry_type} (expected PHYSICAL_PORT=1)"
        assert target_port  == TARGET_PORT, f"GET_DRT: target={target_port} (expected {TARGET_PORT})"

        # ⑥ Set DRT — write DRT[0][0x010] = PHYSICAL_PORT → port 1
        r = await send_pbr_command(
            req_port, resp_port,
            opcode=OPCODE_SET_DRT,
            request_payload=build_set_drt_payload(DRT_INDEX, PID, 1, TARGET_PORT),
            cci_tag=6, msg_tag=6,
        )
        assert r["return_code"] == 0,          f"SET_DRT: rc={r['return_code']}"
        assert r["opcode"]      == OPCODE_SET_DRT
        assert r["cci_payload"] == b"",        "SET_DRT: response payload must be empty"
        assert r["background"]  is False

        assert len(mock.calls) == 6, f"Expected 6 send_raw_cci calls, got {len(mock.calls)}"

        print(f"\n\033[1m\033[92m{'═' * 62}")
        print("  ✓  All 6 PBR commands: PASSED")
        print(f"     {len(mock.calls)} send_raw_cci() calls forwarded to switch")
        print(f"{'═' * 62}\033[0m\n")

    finally:
        await _stop_server(server, task)


# ─────────────────────────────────────────────────────────────────────────────
# ── Individual command tests (single-command focus, easier to debug) ──────────
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pbr_identify():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        r = await send_pbr_command(req_port, resp_port, OPCODE_IDENTIFY, b"")
        assert r["return_code"] == 0
        assert len(r["cci_payload"]) == 12
        assert r["cci_payload"][8] >= 1   # num_drts
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_pbr_configure_pid_assignment():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        payload = build_configure_pid_assignment_payload(PID, TARGET_PORT)
        r = await send_pbr_command(req_port, resp_port, OPCODE_CFG_PID, payload)
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_pbr_get_pid_binding_unbound():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        payload = build_get_pid_binding_payload(VCS_ID, VPPB_ID)
        r = await send_pbr_command(req_port, resp_port, OPCODE_GET_BIND, payload)
        assert r["return_code"] == 0
        pid_val = struct.unpack_from("<H", r["cci_payload"], 0)[0] & 0x0FFF
        assert pid_val == PID_UNASSIGNED
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_pbr_configure_pid_binding_background():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        payload = build_configure_pid_binding_payload(PID, VCS_ID, VPPB_ID)
        r = await send_pbr_command(req_port, resp_port, OPCODE_CFG_BIND, payload)
        assert r["return_code"] == int(CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED)
        assert r["background"] is True
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_pbr_get_drt():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        payload = build_get_drt_payload(DRT_INDEX, PID, 1)
        r = await send_pbr_command(req_port, resp_port, OPCODE_GET_DRT, payload)
        assert r["return_code"] == 0
        assert len(r["cci_payload"]) >= 10
        entry_type = r["cci_payload"][8] & 0x03
        assert entry_type == 1   # PHYSICAL_PORT
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_pbr_set_drt():
    mock = PbrSmartMockClient()
    server, req_port, resp_port, task = await _start_server(mock)
    try:
        payload = build_set_drt_payload(DRT_INDEX, PID, 1, TARGET_PORT)
        r = await send_pbr_command(req_port, resp_port, OPCODE_SET_DRT, payload)
        assert r["return_code"] == 0
        assert r["cci_payload"] == b""
    finally:
        await _stop_server(server, task)


# ─────────────────────────────────────────────────────────────────────────────
# ── Standalone entry point ────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\033[1m\033[93mRunning PBR commissioning sequence (standalone mode)\033[0m")
    asyncio.run(test_pbr_all_commands_commissioning_sequence())
    print("\033[1m\033[92mDone.\033[0m")
