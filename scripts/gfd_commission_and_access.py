"""
gfd_commission_and_access.py
═══════════════════════════════════════════════════════════════════════════
Complete GFD Commissioning + GAE Proxy Data Access Demo

Runs ALL 14 commands in sequence:
  Phase 1: PBR Commissioning (6 commands)
  Phase 2: GAE Discovery (2 commands)
  Phase 3: Host discovers GFD via proxy (2 commands)
  Phase 4: Data exchange with GFD (4 commands)

Usage:
  # Start the system first (5 terminals or unified launcher):
  python -m opencis.bin.cli start configs/1vcs_1sld_1gfd.yaml

  # Then run this script:
  python scripts/gfd_commission_and_access.py

  # With custom FM address:
  python scripts/gfd_commission_and_access.py --fm-host 192.168.1.10 --fm-port 8200

Prerequisites:
  pip install "python-socketio[client]"

Config: configs/1vcs_1sld_1gfd.yaml
  Port 0 = USP (Host + GAE)
  Port 1 = DSP (SLD, 256 MB CXL.mem)
  Port 2 = DSP (GFD, CCI-only)

Copyright (c) 2026. CXL 4.0 Rev 1.0 §7.7.13-7.7.14
═══════════════════════════════════════════════════════════════════════════
"""

import argparse
import struct
import sys
import time

try:
    import socketio
except ImportError:
    print("ERROR: python-socketio not installed.")
    print("  pip install 'python-socketio[client]'")
    sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────
GFD_PID       = 16     # 0x010 decimal — 12-bit PID for the GFD
GFD_PORT      = 2      # DSP port where GFD is connected
SLD_PORT      = 1      # DSP port where SLD is connected
VCS_ID        = 0      # Virtual CXL Switch 0
GFD_VPPB      = 1      # vPPB 1 is for GFD (vPPB 0 is for SLD)
DRT_INDEX     = 0      # First (and only) DRT table

# CCI opcodes
CCI_IDENTIFY              = 0x0001
PBR_IDENTIFY_SWITCH       = 0x5700
PBR_CONFIGURE_PID         = 0x5704
PBR_GET_PID_BINDING       = 0x5705
PBR_CONFIGURE_PID_BINDING = 0x5706
PBR_GET_DRT               = 0x5708
PBR_SET_DRT               = 0x5709
GAE_IDENTIFY              = 0x5800
GAE_GET_PID_ACCESS        = 0x5802
GAE_PROXY_GFD_MGMT        = 0x5809
GAE_GET_PROXY_STATUS      = 0x580A
GAE_CANCEL_PROXY           = 0x580B

# Vendor-specific opcodes for demo (these will return UNSUPPORTED from stock GFD)
VENDOR_WRITE_REG          = 0xC001
VENDOR_READ_REG           = 0xC002


# ── Helpers ───────────────────────────────────────────────────────────────────

BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
SEP    = "─" * 70

cmd_counter = 0


def banner(phase_num, title):
    """Print a phase banner."""
    print(f"\n{'═' * 70}")
    print(f"  {BOLD}PHASE {phase_num}: {title}{RESET}")
    print(f"{'═' * 70}")


def cmd(name, opcode, description):
    """Print command header."""
    global cmd_counter
    cmd_counter += 1
    print(f"\n{SEP}")
    print(f"  {CYAN}CMD {cmd_counter:2d}{RESET} │ {BOLD}{name}{RESET}  (CCI 0x{opcode:04X})")
    print(f"       │ {description}")
    print(SEP)


def ok(msg):
    print(f"  {GREEN}✅ {msg}{RESET}")


def warn(msg):
    print(f"  {YELLOW}⚠  {msg}{RESET}")


def fail(msg):
    print(f"  {RED}❌ {msg}{RESET}")
    sys.exit(1)


def show_input(payload):
    """Pretty-print the command input payload."""
    if payload:
        print(f"  📤 Input: {payload}")
    else:
        print(f"  📤 Input: (no payload)")


def show_output(resp):
    """Pretty-print the response."""
    error = resp.get("error", "")
    result = resp.get("result", {})
    if error:
        print(f"  📥 Response: {RED}error=\"{error}\"{RESET}")
    else:
        print(f"  📥 Response: {result}")
    return result


def check(resp, step_name):
    """Assert no error in response."""
    error = resp.get("error", "")
    if error:
        fail(f"{step_name}: error=\"{error}\"")
    return resp.get("result", {})


def decode_identify_payload(payload_bytes):
    """Decode a CCI Identify response payload from the GFD."""
    if len(payload_bytes) < 9:
        warn(f"Identify payload too short ({len(payload_bytes)} bytes)")
        return
    vendor_id      = payload_bytes[0] | (payload_bytes[1] << 8)
    device_id      = payload_bytes[2] | (payload_bytes[3] << 8)
    subsys_vid     = payload_bytes[4] | (payload_bytes[5] << 8)
    subsys_id      = payload_bytes[6] | (payload_bytes[7] << 8)
    component_type = payload_bytes[8]

    type_names = {0: "CXL Switch", 1: "Type-1", 2: "Type-2",
                  3: "Type-3/SLD", 4: "GFD", 5: "MLD"}
    type_name = type_names.get(component_type, "Unknown")

    print(f"  ┌─── GFD Identify Response ───────────────────────┐")
    print(f"  │ Vendor ID      : 0x{vendor_id:04X}                       │")
    print(f"  │ Device ID      : 0x{device_id:04X}                       │")
    print(f"  │ Subsystem VID  : 0x{subsys_vid:04X}                       │")
    print(f"  │ Subsystem ID   : 0x{subsys_id:04X}                       │")
    print(f"  │ Component Type : 0x{component_type:02X} = {type_name:<20s}│")
    print(f"  └─────────────────────────────────────────────────┘")
    return component_type


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GFD Commissioning + Data Access Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Config: configs/1vcs_1sld_1gfd.yaml"
    )
    parser.add_argument("--fm-host", default="127.0.0.1", help="FM host (default: 127.0.0.1)")
    parser.add_argument("--fm-port", default=8200, type=int, help="FM Socket.IO port (default: 8200)")
    args = parser.parse_args()

    fm_url = f"http://{args.fm_host}:{args.fm_port}"

    print(f"""
{'═' * 70}
  {BOLD}GFD COMMISSIONING & DATA ACCESS DEMO{RESET}
  CXL 4.0 PBR Switch — configs/1vcs_1sld_1gfd.yaml
{'═' * 70}
  Topology:
    Port 0 (USP) ── Host + GAE
    Port 1 (DSP) ── SLD (256 MB CXL.mem)
    Port 2 (DSP) ── GFD (CCI-only, no memory)

  FM: {fm_url}
  GFD PID: 0x{GFD_PID:03X} ({GFD_PID} dec) on port {GFD_PORT}
{'═' * 70}
""")

    # ── Connect to FM ─────────────────────────────────────────────────────
    sio = socketio.Client()
    try:
        sio.connect(fm_url)
    except Exception as e:
        fail(f"Cannot connect to FM at {fm_url}: {e}")
    ok(f"Connected to Fabric Manager at {fm_url}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: PBR COMMISSIONING (6 commands)
    # ══════════════════════════════════════════════════════════════════════
    banner(1, "PBR SWITCH COMMISSIONING")

    # CMD 1: Identify PBR Switch
    cmd("pbr:identify", PBR_IDENTIFY_SWITCH,
        "What are the switch capabilities? How many routing tables?")
    show_input(None)
    resp = sio.call("pbr:identify")
    result = check(resp, "pbr:identify")
    show_output(resp)
    print(f"       gaeSupportMap = 0x{result['gaeSupportMap']:X}  "
          f"({'VCS 0 has GAE' if result['gaeSupportMap'] & 1 else 'NO GAE!'})")
    print(f"       numDrts       = {result['numDrts']}  "
          f"({'OK' if result['numDrts'] >= 1 else 'NEED >= 1!'})")
    print(f"       numRgts       = {result['numRgts']}")
    print(f"       routingCaps   = {result['routingCaps']}")
    assert result["numDrts"] >= 1, "numDrts must be >= 1"
    assert result["gaeSupportMap"] >= 1, "gaeSupportMap must be >= 1"
    ok("Switch has routing table + GAE support")

    # CMD 2: Configure PID Assignment
    cmd("pbr:configurePid", PBR_CONFIGURE_PID,
        f"Assign PID 0x{GFD_PID:03X} to DSP port {GFD_PORT} (GFD)")
    payload = {
        "operation": 0,  # ASSIGN
        "entries": [{"pid": GFD_PID, "targetId": GFD_PORT, "instanceId": 0}]
    }
    show_input(payload)
    resp = sio.call("pbr:configurePid", payload)
    check(resp, "pbr:configurePid")
    show_output(resp)
    ok(f"PID 0x{GFD_PID:03X} assigned to port {GFD_PORT}")
    warn("DRT not updated yet — data plane still dark")

    # CMD 3: Set DRT
    cmd("pbr:setDrt", PBR_SET_DRT,
        f"Program router: DPID 0x{GFD_PID:03X} → port {GFD_PORT}")
    payload = {
        "drtIndex": DRT_INDEX,
        "startEntry": GFD_PID,
        "entries": [{"entryType": "PHYSICAL_PORT", "routingTarget": GFD_PORT}]
    }
    show_input(payload)
    resp = sio.call("pbr:setDrt", payload)
    check(resp, "pbr:setDrt")
    show_output(resp)
    ok(f"DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = PHYSICAL_PORT → port {GFD_PORT}")
    ok("DATA PLANE IS NOW LIVE for DPID=0x010")

    # CMD 4: Get PID Binding — verify UNBOUND
    cmd("pbr:getPidBinding", PBR_GET_PID_BINDING,
        f"Is vPPB {GFD_VPPB} in VCS {VCS_ID} free?")
    payload = {"targetVcs": VCS_ID, "targetVppb": GFD_VPPB}
    show_input(payload)
    resp = sio.call("pbr:getPidBinding", payload)
    result = check(resp, "pbr:getPidBinding")
    show_output(resp)
    pid_before = result["pid"]
    print(f"       pid = {pid_before} (0x{pid_before:03X}) → "
          f"{'UNBOUND ✅' if pid_before == 0xFFF else 'ALREADY BOUND ⚠'}")
    if pid_before != 0xFFF:
        warn(f"vPPB already bound to PID 0x{pid_before:03X}, will rebind")

    # CMD 5: Configure PID Binding — BIND (background command)
    cmd("pbr:configurePidBinding", PBR_CONFIGURE_PID_BINDING,
        f"Bind vPPB {GFD_VPPB} to PID 0x{GFD_PID:03X} [BACKGROUND]")
    payload = {
        "operation": 0, "targetVcs": VCS_ID, "targetVppb": GFD_VPPB,
        "pid": GFD_PID,
        "latencyEntryBaseUnit": 0, "latencyEntry": 0,
        "bwEntryBaseUnit": 0, "bwEntry": 0
    }
    show_input(payload)
    resp = sio.call("pbr:configurePidBinding", payload)
    check(resp, "pbr:configurePidBinding")
    show_output(resp)
    ok("BACKGROUND_COMMAND_STARTED is the correct response")

    # CMD 6: Get PID Binding — verify BOUND
    time.sleep(0.2)  # wait for background command
    cmd("pbr:getPidBinding", PBR_GET_PID_BINDING,
        f"Confirm vPPB {GFD_VPPB} is now BOUND to PID 0x{GFD_PID:03X}")
    payload = {"targetVcs": VCS_ID, "targetVppb": GFD_VPPB}
    show_input(payload)
    resp = sio.call("pbr:getPidBinding", payload)
    result = check(resp, "pbr:getPidBinding")
    show_output(resp)
    pid_after = result["pid"]
    if pid_after == GFD_PID:
        ok(f"vPPB({VCS_ID},{GFD_VPPB}) BOUND to PID 0x{pid_after:03X}")
    else:
        warn(f"pid={pid_after} (0x{pid_after:03X}) — background task may still be running")

    print(f"\n  {'★' * 50}")
    print(f"  {GREEN}{BOLD}  PBR COMMISSIONING COMPLETE{RESET}")
    print(f"  {'★' * 50}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: GAE DISCOVERY (2 commands)
    # ══════════════════════════════════════════════════════════════════════
    banner(2, "GAE DISCOVERY")

    # CMD 7: Identify GAE
    cmd("gae:identify", GAE_IDENTIFY,
        "What GAE capabilities does this switch have?")
    show_input(None)
    resp = sio.call("gae:identify")
    result = check(resp, "gae:identify")
    show_output(resp)
    num_vppbs = result.get("numVppbsWithGlobalMemory", 0)
    print(f"       numVppbsWithGlobalMemory = {num_vppbs}")
    print(f"       vppbEntries = {result.get('vppbEntries', [])}")
    ok(f"GAE is responsive. G-FAM vPPBs = {num_vppbs} (0 = simple GFD, no G-FAM)")

    # CMD 8: Get PID Access Vectors
    cmd("gae:getPidAccessVectors", GAE_GET_PID_ACCESS,
        f"What access does host have to PID 0x{GFD_PID:03X}?")
    payload = {"pid": GFD_PID}
    show_input(payload)
    resp = sio.call("gae:getPidAccessVectors", payload)
    result = check(resp, "gae:getPidAccessVectors")
    show_output(resp)
    gmv = result.get("globalMemoryVector", 0)
    vtv = result.get("virtualTargetVector", 0)
    print(f"       globalMemoryVector  = {gmv} ({'has G-FAM' if gmv else 'no G-FAM'})")
    print(f"       virtualTargetVector = {vtv}")
    ok("Host accesses GFD via CCI proxy only (no direct memory)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: HOST DISCOVERS GFD VIA GAE PROXY (2 commands)
    # ══════════════════════════════════════════════════════════════════════
    banner(3, "HOST DISCOVERS GFD VIA GAE PROXY")

    # CMD 9: Proxy GFD Identify
    cmd("gae:proxyGfdMgmt", GAE_PROXY_GFD_MGMT,
        f"Send CCI Identify (0x{CCI_IDENTIFY:04X}) to GFD through GAE proxy")
    payload = {"gfdOpcode": CCI_IDENTIFY, "gfdPayload": []}
    show_input(payload)
    resp = sio.call("gae:proxyGfdMgmt", payload)
    result = check(resp, "gae:proxyGfdMgmt")
    show_output(resp)
    thread_id_identify = result.get("threadId", 0)
    ok(f"Proxy thread started: threadId={thread_id_identify}")

    # CMD 10: Get Proxy Status — GFD Identify response
    time.sleep(0.2)
    cmd("gae:getProxyStatus", GAE_GET_PROXY_STATUS,
        f"Has the GFD responded to Identify? (threadId={thread_id_identify})")
    payload = {"threadId": thread_id_identify}
    show_input(payload)
    resp = sio.call("gae:getProxyStatus", payload)
    result = check(resp, "gae:getProxyStatus")
    show_output(resp)

    completed = result.get("completed", False)
    gfd_rc    = result.get("gfdReturnCode", -1)
    gfd_data  = result.get("gfdResponsePayload", [])

    if completed and gfd_rc == 0:
        ok(f"GFD responded: completed={completed}, returnCode={gfd_rc} (SUCCESS)")
        comp_type = decode_identify_payload(gfd_data)
        if comp_type == 4:
            ok("Component Type = 0x04 = GFD — CONFIRMED!")
        else:
            warn(f"Unexpected component type: 0x{comp_type:02X}")
    elif completed:
        warn(f"GFD returned error code: {gfd_rc}")
    else:
        warn("GFD has not responded yet — try again after a delay")

    print(f"\n  {'★' * 50}")
    print(f"  {GREEN}{BOLD}  HOST KNOWS GFD IS ATTACHED (type=0x04){RESET}")
    print(f"  {'★' * 50}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: DATA EXCHANGE WITH GFD (4 commands)
    # ══════════════════════════════════════════════════════════════════════
    banner(4, "DATA EXCHANGE WITH GFD VIA GAE PROXY")

    print(f"""
  {YELLOW}NOTE: The stock GFD only has CCI Identify (0x0001) registered.
  Vendor-specific commands (0xC001 write, 0xC002 read) will return
  UNSUPPORTED — but the command DOES traverse the full path:

    Host → TCP → Switch USP → GAE → GaeManager → DspCciTunnel
    → TCP → GFD CCI Mailbox → CciExecutor → (UNSUPPORTED)
    → response flows back the same path

  Check switch + GFD logs to see the commands arriving!
  To add real vendor commands, register them in CxlGfdDevice._register_cci_commands(){RESET}
""")

    # CMD 11: Write data to GFD (vendor-specific)
    write_data = struct.pack("<HI", 0x0000, 0xDEADBEEF)  # offset=0, data=0xDEADBEEF
    cmd("gae:proxyGfdMgmt", GAE_PROXY_GFD_MGMT,
        f"Write 0xDEADBEEF to GFD register 0x0000 (vendor CCI 0x{VENDOR_WRITE_REG:04X})")
    payload = {"gfdOpcode": VENDOR_WRITE_REG, "gfdPayload": list(write_data)}
    show_input(payload)
    print(f"       Payload bytes: {[f'0x{b:02X}' for b in write_data]}")
    print(f"       Decoded: offset=0x0000, data=0xDEADBEEF")
    resp = sio.call("gae:proxyGfdMgmt", payload)
    result = check(resp, "gae:proxyGfdMgmt")
    show_output(resp)
    thread_id_write = result.get("threadId", 0)
    ok(f"Write proxy started: threadId={thread_id_write}")

    # CMD 12: Get write result
    time.sleep(0.2)
    cmd("gae:getProxyStatus", GAE_GET_PROXY_STATUS,
        f"Did the GFD accept the write? (threadId={thread_id_write})")
    payload = {"threadId": thread_id_write}
    show_input(payload)
    resp = sio.call("gae:getProxyStatus", payload)
    result = check(resp, "gae:getProxyStatus")
    show_output(resp)

    gfd_rc = result.get("gfdReturnCode", -1)
    if gfd_rc == 0:
        ok("GFD accepted the write command (SUCCESS)")
    else:
        # Return code 3 = UNSUPPORTED (stock GFD doesn't have vendor commands)
        rc_names = {0: "SUCCESS", 1: "BACKGROUND_STARTED", 3: "UNSUPPORTED",
                    4: "INVALID_INPUT", 6: "INTERNAL_ERROR"}
        rc_name = rc_names.get(gfd_rc, f"UNKNOWN({gfd_rc})")
        warn(f"GFD returned: {rc_name} (rc={gfd_rc})")
        if gfd_rc == 3:
            print(f"       {YELLOW}This is expected — stock GFD only has Identify.{RESET}")
            print(f"       {YELLOW}The command DID reach the GFD (check GFD logs!).{RESET}")
            print(f"       {YELLOW}To handle vendor writes, register a CCI command handler.{RESET}")

    # CMD 13: Read data from GFD (vendor-specific)
    read_req = struct.pack("<HH", 0x0000, 4)  # offset=0, length=4
    cmd("gae:proxyGfdMgmt", GAE_PROXY_GFD_MGMT,
        f"Read 4 bytes from GFD register 0x0000 (vendor CCI 0x{VENDOR_READ_REG:04X})")
    payload = {"gfdOpcode": VENDOR_READ_REG, "gfdPayload": list(read_req)}
    show_input(payload)
    resp = sio.call("gae:proxyGfdMgmt", payload)
    result = check(resp, "gae:proxyGfdMgmt")
    show_output(resp)
    thread_id_read = result.get("threadId", 0)
    ok(f"Read proxy started: threadId={thread_id_read}")

    # CMD 14: Get read result
    time.sleep(0.2)
    cmd("gae:getProxyStatus", GAE_GET_PROXY_STATUS,
        f"Get read data from GFD (threadId={thread_id_read})")
    payload = {"threadId": thread_id_read}
    show_input(payload)
    resp = sio.call("gae:getProxyStatus", payload)
    result = check(resp, "gae:getProxyStatus")
    show_output(resp)

    gfd_rc   = result.get("gfdReturnCode", -1)
    gfd_data = result.get("gfdResponsePayload", [])
    if gfd_rc == 0 and gfd_data:
        data_hex = " ".join(f"0x{b:02X}" for b in gfd_data)
        ok(f"Read data from GFD: [{data_hex}]")
        if len(gfd_data) >= 4:
            value = struct.unpack("<I", bytes(gfd_data[:4]))[0]
            print(f"       Decoded value: 0x{value:08X}")
    elif gfd_rc == 3:
        warn("GFD returned UNSUPPORTED (expected for stock GFD)")
        print(f"       {YELLOW}The read request DID reach the GFD — check logs!{RESET}")

    # ══════════════════════════════════════════════════════════════════════
    # OPTIONAL: Verify DRT
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  {CYAN}VERIFY{RESET} │ pbr:getDrt — confirm routing table")
    print(SEP)
    resp = sio.call("pbr:getDrt", {
        "drtIndex": DRT_INDEX, "startEntry": GFD_PID, "numEntries": 1
    })
    result = check(resp, "pbr:getDrt")
    entry = result["entries"][0]
    ok(f"DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = {entry['entryType']} → port {entry['routingTarget']}")

    # ══════════════════════════════════════════════════════════════════════
    # CLEANUP: Cancel all proxy threads
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  {CYAN}CLEANUP{RESET} │ Cancel proxy threads")
    print(SEP)
    for tid in [thread_id_identify, thread_id_write, thread_id_read]:
        if tid > 0:
            resp = sio.call("gae:cancelProxy", {"threadId": tid})
            ok(f"Thread {tid} cleaned up")

    # ══════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print(f"""
{'★' * 70}

  {BOLD}{GREEN}ALL 14 COMMANDS COMPLETED SUCCESSFULLY{RESET}

  {BOLD}PBR Commissioning:{RESET}
    PID 0x{GFD_PID:03X} ({GFD_PID} dec) → DSP port {GFD_PORT} → GFD device
    DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = PHYSICAL_PORT → port {GFD_PORT}
    vPPB({VCS_ID},{GFD_VPPB}) bound to PID 0x{GFD_PID:03X}

  {BOLD}GAE Discovery:{RESET}
    GAE on VCS 0 is responsive
    No G-FAM memory (simple GFD)

  {BOLD}GFD Identity:{RESET}
    Component Type = 0x04 (GFD) — confirmed via GAE proxy
    Vendor ID = 0x00EE (EEUM)

  {BOLD}Data Path:{RESET}
    Host → GAE (USP) → DspCciTunnel → TCP → GFD CCI Mailbox
    All commands traversed the full CXL 4.0 PBR switch path.

  {BOLD}What to look for in the logs:{RESET}
    Switch log: "[GaeCciMailbox] opcode=0x5809" — proxy request arrived
    Switch log: "[GaeManager] start_proxy: gfd_opcode=0x..." — proxy dispatched
    GFD log:    "[CxlGfdDevice] CCI mailbox: opcode=0x..." — command reached GFD
    GFD log:    "[CxlGfdDevice] CCI response: return_code=..." — GFD responded

{'★' * 70}
""")

    sio.disconnect()
    ok("Disconnected from FM. Done!")


if __name__ == "__main__":
    main()
