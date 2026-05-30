#!/usr/bin/env python3
"""
tests/smbus_slave_master_sim.py
================================
Standalone SMBus Slave + Master simulator.

Mimics exactly how a real QEMU SMBus Slave/Master pair would interact
with FmSmbusDualPortServer:

  SmbusSlave   → TCP:8301   sends DSP0237 SMBus+MCTP request frames
  SmbusMaster  ← TCP:8302   reads  DSP0237 SMBus+MCTP response frames

Both run in separate threads (like real independent QEMU devices):
  • Master thread  : connects to 8302, blocks on recv, prints each response
  • Slave thread   : connects to 8301, sends CCI commands one by one

Usage:
    Terminal 1:  python run_pbr_env.py          (start FM + Switch)
    Terminal 2:  python tests/smbus_slave_master_sim.py

Options:
    --host          FM host          (default: 127.0.0.1)
    --slave-port    Request port     (default: 8301)
    --master-port   Response port    (default: 8302)
    --i2c-fm        FM 7-bit I2C addr   (default: 0x10)
    --i2c-dev       Device 7-bit I2C addr (default: 0x20)
    --fm-eid        FM endpoint ID   (default: 0x08)
    --dev-eid       Device EID       (default: 0x09)
    --delay         Delay (s) between commands (default: 0.2)
    --timeout       Response timeout (default: 15)
"""

import sys
import socket
import struct
import threading
import time
import queue
import argparse

# ── Colour codes ──────────────────────────────────────────────────────────────
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
MAGENTA= "\033[95m"
DIM    = "\033[2m"
RESET  = "\033[0m"

SLAVE_TAG  = f"{CYAN}[SMBus Slave  →8301]{RESET}"
MASTER_TAG = f"{MAGENTA}[SMBus Master ←8302]{RESET}"
SYS_TAG    = f"{YELLOW}[Sim]{RESET}"

# ── Protocol constants ────────────────────────────────────────────────────────
SMBUS_MCTP_CMD = 0x0F
MCTP_HDR_VER   = 0x01
MCTP_MSG_TYPE  = 0x7E   # CXL FM API (no IC bit)

CCI_RETURN_CODES = {
    0x0000: ("SUCCESS",                   GREEN),
    0x0001: ("BACKGROUND_COMMAND_STARTED",YELLOW),
    0x0002: ("INVALID_INPUT",             RED),
    0x0003: ("UNSUPPORTED",               RED),
    0x0004: ("INTERNAL_ERROR",            RED),
    0x0005: ("RETRY_REQUIRED",            YELLOW),
    0x0006: ("BUSY",                      YELLOW),
}

OPCODE_NAMES = {
    0x5700: "IDENTIFY_PBR_SWITCH",
    0x5704: "CONFIGURE_PID_ASSIGNMENT",
    0x5705: "GET_PID_BINDING",
    0x5706: "CONFIGURE_PID_BINDING",
    0x5708: "GET_DRT",
    0x5709: "SET_DRT",
    0x5800: "IDENTIFY_GAE",
}


# ═══════════════════════════════════════════════════════════════════════════════
# CRC-8 PEC (polynomial 0x07)
# ═══════════════════════════════════════════════════════════════════════════════

def crc8_smbus(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ═══════════════════════════════════════════════════════════════════════════════
# Hex dump helper
# ═══════════════════════════════════════════════════════════════════════════════

def hex_dump(data: bytes, indent: str = "      ") -> str:
    if not data:
        return f"{indent}(empty)"
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        h = " ".join(f"{b:02X}" for b in chunk)
        a = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{i:04X}  {h:<47}  |{a}|")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# DSP0237 Frame Builder  (Slave side)
# ═══════════════════════════════════════════════════════════════════════════════

def build_smbus_request(
    opcode: int,
    cci_payload: bytes = b"",
    cci_tag: int = 0,
    msg_tag: int = 0,
    fm_i2c_addr: int = 0x10,
    dev_i2c_addr: int = 0x20,
    fm_eid: int = 0x08,
    dev_eid: int = 0x09,
) -> bytes:
    """
    Build a complete DMTF DSP0237 SMBus+MCTP request frame.

    Layout:
      [0]  dest_slave_addr = fm_i2c_addr << 1       (write dir)
      [1]  command_code    = 0x0F
      [2]  byte_count      = len(body)
      [3]  src_slave_addr  = dev_i2c_addr << 1 | 1  (read bit)
      [4]  hdr_ver         = 0x01
      [5]  dest_eid        = fm_eid
      [6]  src_eid         = dev_eid
      [7]  flags           = SOM|EOM|TO=1|msg_tag
      [8]  msg_type        = 0x7E
      [9..20]   CCI message header (12 bytes)
      [21+]     CCI payload
      [last]    PEC
    """
    plen = len(cci_payload)

    # CCI message header (12 bytes)
    cci_hdr = bytearray(12)
    cci_hdr[0] = 0x00                       # REQUEST
    cci_hdr[1] = cci_tag & 0xFF
    cci_hdr[2] = 0x00
    cci_hdr[3] = opcode & 0xFF
    cci_hdr[4] = (opcode >> 8) & 0xFF
    cci_hdr[5] = plen & 0xFF
    cci_hdr[6] = (plen >> 8) & 0xFF
    cci_hdr[7] = (plen >> 16) & 0x1F

    # MCTP flags: SOM=1 EOM=1 PktSeq=0 TO=1 (tag owner) msg_tag
    flags = 0xC8 | (msg_tag & 0x07)

    body = bytes([
        (dev_i2c_addr << 1) | 0x01,  # src_slave_addr (read bit)
        MCTP_HDR_VER,
        fm_eid,
        dev_eid,
        flags,
        MCTP_MSG_TYPE,
    ]) + bytes(cci_hdr) + cci_payload

    dest_addr  = (fm_i2c_addr << 1) & 0xFE   # write direction
    byte_count = len(body)
    frame = bytes([dest_addr, SMBUS_MCTP_CMD, byte_count]) + body
    frame += bytes([crc8_smbus(frame)])
    return frame


# ═══════════════════════════════════════════════════════════════════════════════
# DSP0237 Response Parser  (Master side)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_smbus_response(raw: bytes) -> dict:
    """
    Parse a DSP0237 SMBus+MCTP response frame received on port 8302.

    Returns a dict with all decoded fields.
    Raises ValueError if frame is too short.
    """
    if len(raw) < 20:
        raise ValueError(f"Response too short: {len(raw)} bytes (min 20)")

    byte_count   = raw[0]
    fm_src_addr  = raw[1]
    hdr_ver      = raw[2]
    dest_eid     = raw[3]
    src_eid      = raw[4]
    flags        = raw[5]
    som          = (flags >> 7) & 1
    eom          = (flags >> 6) & 1
    pkt_seq      = (flags >> 4) & 3
    to           = (flags >> 3) & 1
    msg_tag      = flags & 0x7
    msg_type_byte= raw[6]
    msg_type     = msg_type_byte & 0x7F
    pec          = raw[-1]
    pec_computed = crc8_smbus(raw[:-1])
    pec_ok       = (pec == pec_computed)

    # CCI response header at raw[7..18]
    cci          = raw[7:19]
    category     = cci[0] & 0x0F
    cci_tag      = cci[1]
    opcode       = int.from_bytes(cci[3:5], "little")
    payload_len  = (int.from_bytes(cci[5:7], "little") |
                    ((cci[7] & 0x1F) << 16))
    is_background= (cci[7] >> 7) & 1
    return_code  = int.from_bytes(cci[8:10], "little")
    cci_payload  = raw[19:19 + payload_len]

    return {
        "raw":          raw,
        "byte_count":   byte_count,
        "fm_src_addr":  fm_src_addr,
        "hdr_ver":      hdr_ver,
        "dest_eid":     dest_eid,
        "src_eid":      src_eid,
        "som":          som, "eom": eom, "pkt_seq": pkt_seq,
        "to":           to, "msg_tag": msg_tag,
        "msg_type":     msg_type,
        "pec":          pec, "pec_ok": pec_ok,
        "category":     category,
        "cci_tag":      cci_tag,
        "opcode":       opcode,
        "return_code":  return_code,
        "background":   is_background,
        "cci_payload":  cci_payload,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Socket helpers
# ═══════════════════════════════════════════════════════════════════════════════

def tcp_connect(host: str, port: int, timeout: float) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    return s


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by server")
        buf += chunk
    return buf


def recv_smbus_response_frame(sock: socket.socket) -> bytes:
    """Read one complete DSP0237 response frame (length-delimited)."""
    bc  = recv_exact(sock, 1)               # byte_count byte
    rest = recv_exact(sock, bc[0] + 1)      # body + PEC
    return bc + rest


# ═══════════════════════════════════════════════════════════════════════════════
# CCI payload builders
# ═══════════════════════════════════════════════════════════════════════════════

def build_configure_pid_assignment(pid: int = 0x010, target_id: int = 1) -> bytes:
    """CONFIGURE_PID_ASSIGNMENT: SET, 1 entry, pid → target_id."""
    hdr     = struct.pack("<BBHH", 0, 0, 1, 0)  # operation=SET, reserved, count=1, rsvd
    entry   = struct.pack("<HH", pid, target_id)
    return hdr + entry

def build_get_pid_binding(vcs_id: int = 0, vppb_id: int = 0) -> bytes:
    return struct.pack("<BB", vcs_id, vppb_id)

def build_set_drt(pid: int = 0x010, port: int = 1) -> bytes:
    return struct.pack("<HBB", pid, 1, 0) + struct.pack("<BBH", 0, 0, port)

def build_get_drt(pid: int = 0x010) -> bytes:
    return struct.pack("<H", pid)

def build_configure_pid_binding(
    vcs_id: int = 0, vppb_id: int = 0,
    pid: int = 0x010, bind: bool = True
) -> bytes:
    op = 0 if bind else 1
    return struct.pack("<BBBBBH", op, vcs_id, vppb_id, 0, 0, pid)


# ═══════════════════════════════════════════════════════════════════════════════
# Test command sequence
# ═══════════════════════════════════════════════════════════════════════════════

def get_test_commands() -> list:
    """Return ordered list of (description, opcode, cci_payload)."""
    return [
        ("IDENTIFY_PBR_SWITCH      (0x5700)",
            0x5700, b""),
        ("CONFIGURE_PID_ASSIGNMENT (0x5704)",
            0x5704, build_configure_pid_assignment()),
        ("GET_PID_BINDING before   (0x5705)",
            0x5705, build_get_pid_binding()),
        ("SET_DRT                  (0x5709)",
            0x5709, build_set_drt()),
        ("GET_DRT                  (0x5708)",
            0x5708, build_get_drt()),
        ("CONFIGURE_PID_BINDING    (0x5706)",
            0x5706, build_configure_pid_binding()),
        ("GET_PID_BINDING after    (0x5705)",
            0x5705, build_get_pid_binding()),
        ("IDENTIFY_GAE             (0x5800)",
            0x5800, b""),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# SMBus Master thread — reads responses from port 8302
# ═══════════════════════════════════════════════════════════════════════════════

class SmbusMaster(threading.Thread):
    """
    Simulates the QEMU SMBus Master device.

    Connects to FM port 8302 and blocks waiting for DSP0237 response
    frames. Each received frame is decoded and placed in result_queue.
    Runs until stop_event is set.
    """

    def __init__(
        self,
        host: str,
        port: int,
        num_expected: int,
        result_queue: queue.Queue,
        timeout: float,
    ):
        super().__init__(name="SmbusMaster", daemon=True)
        self.host         = host
        self.port         = port
        self.num_expected = num_expected
        self.result_queue = result_queue
        self.timeout      = timeout
        self.sock         = None
        self.ready        = threading.Event()
        self.error        = None

    def run(self):
        try:
            print(f"{MASTER_TAG} Connecting to {self.host}:{self.port} ...",
                  end=" ", flush=True)
            self.sock = tcp_connect(self.host, self.port, self.timeout)
            print(f"{GREEN}connected{RESET}")
            self.ready.set()

            received = 0
            while received < self.num_expected:
                raw = recv_smbus_response_frame(self.sock)
                received += 1

                try:
                    resp = parse_smbus_response(raw)
                except ValueError as exc:
                    self.result_queue.put({"error": str(exc), "raw": raw})
                    continue

                self.result_queue.put(resp)
                self._print_response(received, raw, resp)

        except socket.timeout:
            self.error = f"TIMEOUT waiting for response on port {self.port}"
            print(f"\n{MASTER_TAG} {RED}{self.error}{RESET}")
            self.result_queue.put({"error": self.error})
        except Exception as exc:
            self.error = str(exc)
            print(f"\n{MASTER_TAG} {RED}Error: {exc}{RESET}")
            self.result_queue.put({"error": self.error})
        finally:
            if self.sock:
                self.sock.close()

    def _print_response(self, seq: int, raw: bytes, r: dict) -> None:
        rc       = r["return_code"]
        rcname, rccolor = CCI_RETURN_CODES.get(rc, (f"0x{rc:04X}", RED))
        opname   = OPCODE_NAMES.get(r["opcode"], f"0x{r['opcode']:04X}")
        pec_str  = f"{GREEN}OK{RESET}" if r["pec_ok"] else f"{RED}BAD{RESET}"

        print(f"\n{MASTER_TAG} {BOLD}Response #{seq} received  ←  port {self.port}{RESET}")
        print(f"  Raw bytes ({len(raw)} bytes):")
        print(hex_dump(raw))
        print(f"  ── Decoded ──────────────────────────────────────────")
        print(f"  byte_count  : {r['byte_count']}")
        print(f"  fm_src_addr : 0x{r['fm_src_addr']:02X}  "
              f"(i2c 0x{r['fm_src_addr'] >> 1:02X})")
        print(f"  dest_eid    : 0x{r['dest_eid']:02X}   src_eid : 0x{r['src_eid']:02X}")
        print(f"  SOM:{r['som']} EOM:{r['eom']} TO:{r['to']} msg_tag:{r['msg_tag']}")
        print(f"  msg_type    : 0x{r['msg_type']:02X}  (CXL_FM_API)")
        print(f"  category    : {r['category']}  (RESPONSE)")
        print(f"  opcode      : {BOLD}0x{r['opcode']:04X}{RESET}  ({opname})")
        print(f"  cci_tag     : {r['cci_tag']}")
        print(f"  return_code : {rccolor}{rcname}{RESET}  (0x{rc:04X})")
        print(f"  background  : {bool(r['background'])}")
        print(f"  pec         : 0x{r['pec']:02X}  [{pec_str}]")
        print(f"  payload     : {len(r['cci_payload'])} bytes")
        if r["cci_payload"]:
            print(f"  ── CCI Payload ──────────────────────────────────────")
            print(hex_dump(r["cci_payload"]))


# ═══════════════════════════════════════════════════════════════════════════════
# SMBus Slave thread — sends requests to port 8301
# ═══════════════════════════════════════════════════════════════════════════════

class SmbusSlave(threading.Thread):
    """
    Simulates the QEMU SMBus Slave device.

    Connects to FM port 8301 and sends DSP0237 SMBus+MCTP request
    frames for each CCI command in the test sequence.
    """

    def __init__(
        self,
        host: str,
        port: int,
        commands: list,
        delay: float,
        timeout: float,
        master_ready: threading.Event,
        fm_i2c_addr: int,
        dev_i2c_addr: int,
        fm_eid: int,
        dev_eid: int,
    ):
        super().__init__(name="SmbusSlave", daemon=True)
        self.host         = host
        self.port         = port
        self.commands     = commands
        self.delay        = delay
        self.timeout      = timeout
        self.master_ready = master_ready
        self.fm_i2c_addr  = fm_i2c_addr
        self.dev_i2c_addr = dev_i2c_addr
        self.fm_eid       = fm_eid
        self.dev_eid      = dev_eid
        self.sock         = None
        self.error        = None

    def run(self):
        try:
            # Wait for Master to connect before sending anything
            print(f"{SLAVE_TAG}  Waiting for SMBus Master to be ready ...",
                  end=" ", flush=True)
            self.master_ready.wait(timeout=self.timeout)
            print(f"{GREEN}Master ready{RESET}")
            time.sleep(0.1)   # small grace period

            print(f"{SLAVE_TAG}  Connecting to {self.host}:{self.port} ...",
                  end=" ", flush=True)
            self.sock = tcp_connect(self.host, self.port, self.timeout)
            print(f"{GREEN}connected{RESET}")

            for seq, (desc, opcode, cci_payload) in enumerate(self.commands, 1):
                cci_tag = seq
                msg_tag = seq % 8

                frame = build_smbus_request(
                    opcode     = opcode,
                    cci_payload= cci_payload,
                    cci_tag    = cci_tag,
                    msg_tag    = msg_tag,
                    fm_i2c_addr= self.fm_i2c_addr,
                    dev_i2c_addr=self.dev_i2c_addr,
                    fm_eid     = self.fm_eid,
                    dev_eid    = self.dev_eid,
                )

                self._print_request(seq, desc, opcode, cci_payload, frame)
                self.sock.sendall(frame)
                time.sleep(self.delay)

        except Exception as exc:
            self.error = str(exc)
            print(f"\n{SLAVE_TAG}  {RED}Error: {exc}{RESET}")
        finally:
            if self.sock:
                self.sock.close()

    def _print_request(
        self, seq: int, desc: str, opcode: int,
        cci_payload: bytes, frame: bytes
    ) -> None:
        opname = OPCODE_NAMES.get(opcode, f"0x{opcode:04X}")
        print(f"\n{SLAVE_TAG}  {BOLD}Sending command #{seq}/{len(self.commands)}{RESET}"
              f"  →  port {self.port}")
        print(f"  Command     : {desc}")
        print(f"  Opcode      : {BOLD}0x{opcode:04X}{RESET}  ({opname})")
        print(f"  CCI payload : {len(cci_payload)} bytes")
        print(f"  Frame total : {len(frame)} bytes")
        print(f"  ── Request frame bytes ──────────────────────────────")
        print(hex_dump(frame))


# ═══════════════════════════════════════════════════════════════════════════════
# Main simulation
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation(args) -> int:
    commands = get_test_commands()
    total    = len(commands)

    print(f"\n{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║  SMBus Slave + Master Simulator                               ║")
    print(f"║  Slave  → TCP:{args.slave_port}   (QEMU SMBus Slave sends requests)    ║")
    print(f"║  Master ← TCP:{args.master_port}   (QEMU SMBus Master reads responses) ║")
    print(f"║  Commands  : {total}                                              ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print(RESET)

    result_queue = queue.Queue()

    # ── Start Master thread first (it must be connected before Slave sends) ──
    master = SmbusMaster(
        host        = args.host,
        port        = args.master_port,
        num_expected= total,
        result_queue= result_queue,
        timeout     = args.timeout,
    )
    master.start()

    if not master.ready.wait(timeout=args.timeout):
        print(f"{SYS_TAG} {RED}Master failed to connect in {args.timeout}s{RESET}")
        return 1

    # ── Start Slave thread ────────────────────────────────────────────────────
    slave = SmbusSlave(
        host         = args.host,
        port         = args.slave_port,
        commands     = commands,
        delay        = args.delay,
        timeout      = args.timeout,
        master_ready = master.ready,
        fm_i2c_addr  = args.i2c_fm,
        dev_i2c_addr = args.i2c_dev,
        fm_eid       = args.fm_eid,
        dev_eid      = args.dev_eid,
    )
    slave.start()

    # ── Wait for both to finish ───────────────────────────────────────────────
    slave.join(timeout=args.timeout * 2)
    master.join(timeout=args.timeout * 2)

    # ── Collect results ───────────────────────────────────────────────────────
    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = failed = 0
    for r in results:
        if "error" in r:
            failed += 1
        else:
            rc = r.get("return_code", 0xFFFF)
            if rc in (0x0000, 0x0001):
                passed += 1
            else:
                failed += 1

    if slave.error:
        print(f"\n{SYS_TAG} {RED}Slave error  : {slave.error}{RESET}")
    if master.error:
        print(f"{SYS_TAG} {RED}Master error : {master.error}{RESET}")

    color  = GREEN if failed == 0 else RED
    status = "ALL PASSED ✓" if failed == 0 else f"{failed} FAILED ✗"

    print(f"\n{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════════════╗")
    print(f"║  Results : {passed}/{total} passed     {color}{status}{RESET}{BOLD}{CYAN}"
          + " " * max(0, 29 - len(f"{passed}/{total} passed     {status}")) + "║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print(RESET)

    return 0 if failed == 0 else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="SMBus Slave + Master simulator for FmSmbusDualPortServer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host",        default="127.0.0.1",
                   help="FM host address")
    p.add_argument("--slave-port",  type=int, default=8301,
                   help="FM request port (SMBus Slave sends here)")
    p.add_argument("--master-port", type=int, default=8302,
                   help="FM response port (SMBus Master reads here)")
    p.add_argument("--i2c-fm",  type=lambda x: int(x, 0), default=0x10,
                   help="FM 7-bit I2C address")
    p.add_argument("--i2c-dev", type=lambda x: int(x, 0), default=0x20,
                   help="Device 7-bit I2C address")
    p.add_argument("--fm-eid",  type=lambda x: int(x, 0), default=0x08,
                   help="FM MCTP endpoint ID")
    p.add_argument("--dev-eid", type=lambda x: int(x, 0), default=0x09,
                   help="Device MCTP endpoint ID")
    p.add_argument("--delay",   type=float, default=0.2,
                   help="Delay (seconds) between CCI commands")
    p.add_argument("--timeout", type=float, default=15.0,
                   help="Socket/response timeout in seconds")
    args = p.parse_args()

    sys.exit(run_simulation(args))


if __name__ == "__main__":
    main()
