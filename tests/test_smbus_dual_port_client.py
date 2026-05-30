#!/usr/bin/env python3
"""
tests/test_smbus_dual_port_client.py
======================================
Standalone integration test client for FmSmbusDualPortServer.

Simulates BOTH the QEMU SMBus Slave (sends requests on port 8301) and
the QEMU SMBus Master (reads responses on port 8302).

Architecture under test:
    [This client]                   [FM process]
    SMBus Slave ──TCP:8301──► Request Server
                                     │ depacketize → CCI → switch → response
                                     │ build SMBus+MCTP response frame
                                     │ push to asyncio.Queue
    SMBus Master ◄──TCP:8302── Response Server
                                     └ drain Queue → write to Master socket

Usage:
    Terminal 1:  python run_pbr_env.py
    Terminal 2:  python tests/test_smbus_dual_port_client.py [OPTIONS]

Options:
    --host              FM host (default: 127.0.0.1)
    --req-port          Request port (default: 8301)
    --resp-port         Response port (default: 8302)
    --timeout           Response receive timeout seconds (default: 10)
    --pec               Verify PEC on received responses
    --stop-on-error     Stop on first test failure
"""

import sys
import socket
import struct
import time
import argparse
import threading

# ── Colour codes ──────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RESET = "\033[0m"

# ── Protocol constants ────────────────────────────────────────────────────────
SMBUS_MCTP_CMD = 0x0F
MCTP_HDR_VER   = 0x01
MCTP_MSG_TYPE  = 0x7E   # CXL FM API

CCI_RETURN_CODE = {
    0x0000: "SUCCESS",
    0x0001: "BACKGROUND_COMMAND_STARTED",
    0x0002: "INVALID_INPUT",
    0x0003: "UNSUPPORTED",
    0x0004: "INTERNAL_ERROR",
    0x0005: "RETRY_REQUIRED",
    0x0006: "BUSY",
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


# ── CRC-8 PEC ─────────────────────────────────────────────────────────────────
def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ── Hex dump ──────────────────────────────────────────────────────────────────
def hex_dump(data: bytes, indent: str = "    ") -> str:
    if not data:
        return f"{indent}(empty)"
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        h = " ".join(f"{b:02X}" for b in chunk)
        a = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{i:04X}  {h:<47}  |{a}|")
    return "\n".join(lines)


# ── Frame builder ─────────────────────────────────────────────────────────────
def build_request_frame(
    opcode: int,
    cci_payload: bytes = b"",
    cci_tag: int = 0,
    msg_tag: int = 0,
    fm_i2c_addr: int = 0x10,
    dev_i2c_addr: int = 0x20,
    fm_eid: int = 0x08,
    dev_eid: int = 0x09,
) -> bytes:
    """Build a complete DSP0237 SMBus+MCTP request frame."""
    plen = len(cci_payload)
    cci_hdr = bytearray(12)
    cci_hdr[0] = 0x00                        # REQUEST
    cci_hdr[1] = cci_tag & 0xFF
    cci_hdr[3] = opcode & 0xFF
    cci_hdr[4] = (opcode >> 8) & 0xFF
    cci_hdr[5] = plen & 0xFF
    cci_hdr[6] = (plen >> 8) & 0xFF

    flags = 0xC8 | (msg_tag & 0x07)          # SOM|EOM|TO|tag

    body = bytes([
        (dev_i2c_addr << 1) | 0x01,          # src_slave_addr
        MCTP_HDR_VER,
        fm_eid,
        dev_eid,
        flags,
        MCTP_MSG_TYPE,
    ]) + bytes(cci_hdr) + cci_payload

    dest_addr  = (fm_i2c_addr << 1) & 0xFE
    byte_count = len(body)
    frame = bytes([dest_addr, SMBUS_MCTP_CMD, byte_count]) + body
    frame += bytes([crc8(frame)])
    return frame


# ── Response parser ───────────────────────────────────────────────────────────
def parse_response_frame(raw: bytes, verify_pec: bool = False) -> dict:
    """Parse a DSP0237 SMBus+MCTP response frame from the FM."""
    if len(raw) < 20:
        raise ValueError(f"Response too short: {len(raw)} bytes (min 20)")

    byte_count  = raw[0]
    fm_src_addr = raw[1]
    dest_eid    = raw[3]
    src_eid     = raw[4]
    msg_tag     = raw[5] & 0x07
    msg_type    = raw[6] & 0x7F
    pec         = raw[-1]
    pec_ok      = (crc8(raw[:-1]) == pec)

    if verify_pec and not pec_ok:
        raise ValueError(f"PEC mismatch: got 0x{pec:02X}, computed 0x{crc8(raw[:-1]):02X}")

    cci = raw[7:19]
    opcode      = int.from_bytes(cci[3:5], "little")
    cci_tag     = cci[1]
    return_code = int.from_bytes(cci[8:10], "little")
    background  = (cci[7] >> 7) & 1
    payload_len = int.from_bytes(cci[5:7], "little") | ((cci[7] & 0x1F) << 16)
    cci_payload = raw[19:19+payload_len]

    return {
        "byte_count": byte_count, "fm_src_addr": fm_src_addr,
        "dest_eid": dest_eid, "src_eid": src_eid,
        "msg_tag": msg_tag, "msg_type": msg_type,
        "pec": pec, "pec_ok": pec_ok,
        "opcode": opcode, "cci_tag": cci_tag,
        "return_code": return_code, "background": background,
        "cci_payload": cci_payload,
    }


# ── Exact-length socket recv ──────────────────────────────────────────────────
def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Server closed connection")
        buf += chunk
    return buf


# ── Receive one full response frame from the response socket ──────────────────
def recv_response_frame(resp_sock: socket.socket) -> bytes:
    """Read one complete DSP0237 response frame from the Master socket."""
    bc_byte = recv_exact(resp_sock, 1)          # byte_count
    byte_count = bc_byte[0]
    rest = recv_exact(resp_sock, byte_count + 1) # body + PEC
    return bc_byte + rest


# ── Print response ────────────────────────────────────────────────────────────
def print_response(seq: int, opcode: int, resp: dict) -> None:
    opname = OPCODE_NAMES.get(opcode, f"0x{opcode:04X}")
    rc     = resp["return_code"]
    rcname = CCI_RETURN_CODE.get(rc, f"0x{rc:04X}")

    if rc == 0:
        rc_str = f"{GREEN}{rcname}{RESET}"; status = f"{GREEN}✓ PASS{RESET}"
    elif rc == 1:
        rc_str = f"{YELLOW}{rcname}{RESET}"; status = f"{YELLOW}✓ PASS (background){RESET}"
    else:
        rc_str = f"{RED}{rcname}{RESET}";   status = f"{RED}✗ FAIL{RESET}"

    pec_str = f"{GREEN}OK{RESET}" if resp["pec_ok"] else f"{RED}BAD{RESET}"
    print(f"\n{BOLD}  ┌─ Response #{seq} received on resp-port ──────────────────{RESET}")
    print(f"  │  opcode      : {BOLD}0x{opcode:04X}{RESET}  ({opname})")
    print(f"  │  return_code : {rc_str}")
    print(f"  │  background  : {resp['background']}")
    print(f"  │  cci_tag     : {resp['cci_tag']}   msg_tag: {resp['msg_tag']}")
    print(f"  │  src_eid     : 0x{resp['src_eid']:02X}  dest_eid: 0x{resp['dest_eid']:02X}")
    print(f"  │  pec         : 0x{resp['pec']:02X}  [{pec_str}]")
    print(f"  │  payload     : {len(resp['cci_payload'])} bytes")
    if resp["cci_payload"]:
        print(hex_dump(resp["cci_payload"], "  │    "))
    print(f"  └─ {status}")


# ── CCI payload builders ──────────────────────────────────────────────────────
def payload_configure_pid_assignment(pid=0x010, target_id=1):
    return struct.pack("<BBHH", 0, 0, 1, 0) + struct.pack("<HH", pid, target_id)

def payload_get_pid_binding(vcs_id=0, vppb_id=0):
    return struct.pack("<BB", vcs_id, vppb_id)

def payload_set_drt(pid=0x010, port=1):
    return struct.pack("<HBB", pid, 1, 0) + struct.pack("<BBH", 0, 0, port)

def payload_get_drt(pid=0x010):
    return struct.pack("<H", pid)

def payload_configure_pid_binding(vcs_id=0, vppb_id=0, pid=0x010, bind=True):
    return struct.pack("<BBBBBH", 0 if bind else 1, vcs_id, vppb_id, 0, 0, pid)


# ── Test sequence ─────────────────────────────────────────────────────────────
TESTS = [
    ("IDENTIFY_PBR_SWITCH",      0x5700, b""),
    ("CONFIGURE_PID_ASSIGNMENT", 0x5704, payload_configure_pid_assignment()),
    ("GET_PID_BINDING (before)", 0x5705, payload_get_pid_binding()),
    ("SET_DRT",                  0x5709, payload_set_drt()),
    ("GET_DRT",                  0x5708, payload_get_drt()),
    ("CONFIGURE_PID_BINDING",    0x5706, payload_configure_pid_binding()),
    ("GET_PID_BINDING (after)",  0x5705, payload_get_pid_binding()),
    ("IDENTIFY_GAE",             0x5800, b""),
]


# ── Main ──────────────────────────────────────────────────────────────────────
def run_tests(host, req_port, resp_port, timeout, verify_pec, stop_on_error):
    print(f"\n{BOLD}{CYAN}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  FmSmbusDualPortServer Integration Test                         ║")
    print(f"║  Slave  → TCP:{req_port}   (send requests)                       ║")
    print(f"║  Master ← TCP:{resp_port}   (read responses)                      ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(RESET)

    # ── Connect to BOTH ports ─────────────────────────────────────────────────
    print(f"Connecting slave  → {host}:{req_port}  ...", end=" ", flush=True)
    try:
        req_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        req_sock.settimeout(timeout)
        req_sock.connect((host, req_port))
        print(f"{GREEN}OK{RESET}")
    except ConnectionRefusedError:
        print(f"{RED}REFUSED{RESET}")
        print(f"\n  {RED}Is FM running?{RESET}  Start with:  python run_pbr_env.py\n")
        return 1

    print(f"Connecting master ← {host}:{resp_port}  ...", end=" ", flush=True)
    try:
        resp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        resp_sock.settimeout(timeout)
        resp_sock.connect((host, resp_port))
        print(f"{GREEN}OK{RESET}")
    except ConnectionRefusedError:
        print(f"{RED}REFUSED{RESET}")
        print(f"\n  {RED}FM resp port {resp_port} not open?{RESET}\n")
        req_sock.close()
        return 1

    passed = failed = 0

    for seq, (desc, opcode, payload) in enumerate(TESTS, start=1):
        opname = OPCODE_NAMES.get(opcode, f"0x{opcode:04X}")
        print(f"\n{BOLD}── Test {seq}/{len(TESTS)}: {desc} ────────────────────────────────{RESET}")

        # ── Build + print request frame ──────────────────────────────────────
        frame = build_request_frame(
            opcode, payload, cci_tag=seq, msg_tag=seq % 8
        )
        print(f"{DIM}  TX → req-port {req_port}  ({len(frame)} bytes):{RESET}")
        print(hex_dump(frame, "    "))

        # ── Send request ─────────────────────────────────────────────────────
        try:
            req_sock.sendall(frame)
        except Exception as exc:
            print(f"{RED}  Send error: {exc}{RESET}")
            failed += 1
            if stop_on_error:
                break
            continue

        # ── Receive response ──────────────────────────────────────────────────
        print(f"{DIM}  Waiting for response on resp-port {resp_port} ...{RESET}")
        try:
            raw_resp = recv_response_frame(resp_sock)
        except socket.timeout:
            print(f"{RED}  TIMEOUT: no response in {timeout}s{RESET}")
            failed += 1
            if stop_on_error:
                break
            continue
        except Exception as exc:
            print(f"{RED}  Recv error: {exc}{RESET}")
            failed += 1
            if stop_on_error:
                break
            continue

        print(f"{DIM}  RX ← resp-port {resp_port}  ({len(raw_resp)} bytes):{RESET}")
        print(hex_dump(raw_resp, "    "))

        # ── Parse + evaluate ──────────────────────────────────────────────────
        try:
            resp = parse_response_frame(raw_resp, verify_pec=verify_pec)
        except Exception as exc:
            print(f"{RED}  Parse error: {exc}{RESET}")
            failed += 1
            if stop_on_error:
                break
            continue

        print_response(seq, opcode, resp)
        rc = resp["return_code"]
        if rc in (0, 1):
            passed += 1
        else:
            failed += 1
            if stop_on_error:
                break

        time.sleep(0.05)

    req_sock.close()
    resp_sock.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    total = passed + failed
    print(f"\n{BOLD}{CYAN}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print(f"║  Results: {passed}/{total} tests passed"
          + " " * (53 - len(f"{passed}/{total} tests passed")) + "║")
    if failed == 0:
        print("║  ✓  ALL TESTS PASSED                                            ║")
    else:
        print(f"║  ✗  {failed} TEST(S) FAILED"
              + " " * (59 - len(f"  ✗  {failed} TEST(S) FAILED")) + "║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(RESET)

    return 0 if failed == 0 else 1


def main():
    p = argparse.ArgumentParser(
        description="Dual-port SMBus+MCTP integration test client"
    )
    p.add_argument("--host",           default="127.0.0.1")
    p.add_argument("--req-port",  type=int, default=8301,
                   help="Request port  — FM SMBus Slave listener (default 8301)")
    p.add_argument("--resp-port", type=int, default=8302,
                   help="Response port — FM SMBus Master listener (default 8302)")
    p.add_argument("--timeout",   type=int, default=10,
                   help="Response timeout in seconds (default 10)")
    p.add_argument("--pec",  action="store_true", help="Verify PEC on responses")
    p.add_argument("--stop-on-error", action="store_true")
    args = p.parse_args()

    sys.exit(run_tests(
        args.host, args.req_port, args.resp_port,
        args.timeout, args.pec, args.stop_on_error
    ))


if __name__ == "__main__":
    main()
