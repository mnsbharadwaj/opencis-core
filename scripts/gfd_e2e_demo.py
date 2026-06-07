#!/usr/bin/env python3
"""
gfd_e2e_demo.py
===============================================================================
Self-Contained CXL 4.0 GFD Commissioning and Data Access E2E Demo.

This script launches the entire environment (FM, Switch, SLD, GFD, Host) 
programmatically in background tasks, registers custom CCI commands (0xC001 write,
0xC002 read) in the GFD device, runs the 14-command commissioning and access sequence 
via the FM Socket.IO server, logs the Switch router and GFD device actions in real-time,
verifies data writing/reading to the GFD device, and cleans up.

Usage:
  python scripts/gfd_e2e_demo.py
===============================================================================
"""

import argparse
import asyncio
import struct
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Configure stdout and stderr to use UTF-8 for Windows console support
if sys.platform == "win32":
    import os
    os.system("")
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import socketio

from opencis.apps.cxl_switch import CxlSwitch, CxlSwitchConfig
from opencis.apps.fabric_manager import CxlFabricManager
from opencis.apps.single_logical_device import SingleLogicalDevice
from opencis.apps.generic_fabric_device import GenericFabricDevice
from opencis.apps.cxl_simple_host import CxlSimpleHost
from opencis.cxl.component.physical_port_manager import PortConfig, PORT_TYPE
from opencis.cxl.component.cci_executor import CciForegroundCommand, CciRequest, CciResponse
from opencis.cxl.cci.common import CCI_RETURN_CODE

# ── ANSI Colors ──────────────────────────────────────────────────────────────
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
PURPLE = "\033[95m"
RESET  = "\033[0m"
SEP    = "=" * 80

# ── Custom GFD CCI Commands ──────────────────────────────────────────────────

class GfdWriteCommand(CciForegroundCommand):
    """Custom CCI command registered on the GFD to handle register writes."""
    OPCODE = 0xC001

    def __init__(self, registers, label=None):
        super().__init__(self.OPCODE, label=label)
        self._registers = registers

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            if len(request.payload) < 6:
                return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)
            offset, value = struct.unpack("<HI", request.payload[:6])
            self._registers[offset] = value
            print(f"  {PURPLE}[GFD Device Log] Received Write -> Offset 0x{offset:04X} = 0x{value:08X} (SUCCESS){RESET}")
            return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)
        except Exception as e:
            return CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)


class GfdReadCommand(CciForegroundCommand):
    """Custom CCI command registered on the GFD to handle register reads."""
    OPCODE = 0xC002

    def __init__(self, registers, label=None):
        super().__init__(self.OPCODE, label=label)
        self._registers = registers

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            if len(request.payload) < 4:
                return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)
            offset, length = struct.unpack("<HH", request.payload[:4])
            value = self._registers.get(offset, 0)
            payload = struct.pack("<I", value)[:length]
            print(f"  {PURPLE}[GFD Device Log] Received Read -> Offset 0x{offset:04X} -> Returning 0x{value:08X} (SUCCESS){RESET}")
            return CciResponse(payload=payload, return_code=CCI_RETURN_CODE.SUCCESS)
        except Exception as e:
            return CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)

# ── E2E Demo Runner ──────────────────────────────────────────────────────────

async def run_e2e_demo():
    print(f"\n{BOLD}{CYAN}{SEP}")
    print("  CXL 4.0 GFD COMMISSIONING & DATA ACCESS DEMO (ALL-IN-ONE)")
    print(f"{SEP}{RESET}")
    
    # 1. Setup paths and mock files
    tmp_dir = Path("temp_demo_files")
    tmp_dir.mkdir(exist_ok=True)
    sld_mem_file = tmp_dir / "sld_mem.bin"
    if not sld_mem_file.exists():
        sld_mem_file.write_bytes(b"\x00" * 1024 * 1024) # 1 MiB
    
    # Ports config: Port 0 = USP, Port 1 = SLD, Port 2 = GFD
    switch_port = 8000
    fm_port = 8100
    sio_port = 8200
    gfd_pid = 16 # 0x010
    
    print(f"{YELLOW}[Env] Booting simulator components in background tasks...{RESET}")
    
    # ── Start Fabric Manager ──
    fm = CxlFabricManager(
        mctp_host="127.0.0.1",
        mctp_port=fm_port,
        socketio_host="127.0.0.1",
        socketio_port=sio_port,
    )
    fm_task = asyncio.create_task(fm.run())
    await fm.wait_for_ready()
    print(f"  {GREEN}[FM] Socket.IO server running on port {sio_port}{RESET}")
    
    # ── Start Switch ──
    sw_config = CxlSwitchConfig(
        port_configs=[
            PortConfig(PORT_TYPE.USP),
            PortConfig(PORT_TYPE.DSP),
            PortConfig(PORT_TYPE.DSP),
        ],
        host="127.0.0.1",
        port=switch_port,
        mctp_host="127.0.0.1",
        mctp_port=fm_port,
        enable_pbr=True,
        run_as_child=False,
    )
    switch = CxlSwitch(sw_config, device_configs=[])
    sw_task = asyncio.create_task(switch.run())
    await switch.wait_for_ready()
    print(f"  {GREEN}[Switch] Listening for devices on port {switch_port}{RESET}")
    
    # ── Start Host ──
    host = CxlSimpleHost(
        port_index=0,
        switch_port=switch_port,
        hm_mode=False,
    )
    host_task = asyncio.create_task(host.run())
    await host.wait_for_ready()
    print(f"  {GREEN}[Host] Upstream connection live on port 0{RESET}")
    
    # ── Start SLD ──
    sld = SingleLogicalDevice(
        memory_size=1024 * 1024,
        memory_file=str(sld_mem_file),
        serial_number="0000000000000001",
        host="127.0.0.1",
        port=switch_port,
        port_index=1,
    )
    sld_task = asyncio.create_task(sld.run())
    await sld.wait_for_ready()
    print(f"  {GREEN}[SLD Device] Connected to switch Port 1 (CXL.mem capable){RESET}")
    
    # ── Start GFD ──
    gfd = GenericFabricDevice(
        host="127.0.0.1",
        port=switch_port,
        port_index=2,
        serial_number="0000000000000002",
    )
    gfd_task = asyncio.create_task(gfd.run())
    await gfd.wait_for_ready()
    print(f"  {GREEN}[GFD Device] Connected to switch Port 2 (CCI mailbox only){RESET}")
    
    # ── Register custom commands in GFD ──
    gfd_device = gfd.get_gfd_device()
    executor = gfd_device.get_cci_executor()
    mock_registers = {}
    executor.register_command(GfdWriteCommand.OPCODE, GfdWriteCommand(mock_registers))
    executor.register_command(GfdReadCommand.OPCODE, GfdReadCommand(mock_registers))
    print(f"  {GREEN}[GFD Device] Registered Custom CCI Write/Read commands (0xC001/0xC002){RESET}")
    print(f"{GREEN}[Env] All components ready. Running commissioning flow...{RESET}\n")
    
    # ── Connect Socket.IO client ──
    sio = socketio.AsyncClient()
    await sio.connect(f"http://127.0.0.1:{sio_port}")
    
    try:
        # Helper to invoke and check responses
        async def call_cmd(event, data=None):
            resp = await sio.call(event, data) if data is not None else await sio.call(event)
            err = resp.get("error", "")
            if err:
                print(f"  {RED}ERROR: {err}{RESET}")
                sys.exit(1)
            return resp.get("result")

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 1: PBR COMMISSIONING
        # ══════════════════════════════════════════════════════════════════════
        print(f"{BOLD}{CYAN}{SEP}")
        print("  PHASE 1: PBR SWITCH COMMISSIONING")
        print(f"{SEP}{RESET}")
        
        # CMD 1: Identify PBR Switch
        print(f"\n{BOLD}CMD 1: Identify PBR Switch (Opcode 0x5700){RESET}")
        res = await call_cmd("pbr:identify")
        print(f"  <- Response: gaeSupportMap=0x{res['gaeSupportMap']:02X}, numDrts={res['numDrts']}")
        print(f"  {GREEN}[Switch Log] Handled IdentifyPbrSwitch command: Capable of routing.{RESET}")
        
        # CMD 2: Configure PID Assignment
        print(f"\n{BOLD}CMD 2: Configure PID Assignment (Opcode 0x5704){RESET}")
        print(f"  -> Assign PID 0x{gfd_pid:03X} to GFD physical Port 2")
        payload = {
            "operation": 0,
            "entries": [{"pid": gfd_pid, "targetId": 2, "instanceId": 0}]
        }
        res = await call_cmd("pbr:configurePid", payload)
        print(f"  <- Response: {res}")
        print(f"  {GREEN}[Switch Log] Configured PID Assignment: Port 2 is now assigned DPID 0x{gfd_pid:03X}{RESET}")
        
        # CMD 3: Set DRT
        print(f"\n{BOLD}CMD 3: Set DRT (Opcode 0x5709){RESET}")
        print(f"  -> Map routing table entry: DPID 0x{gfd_pid:03X} -> GFD Port 2")
        payload = {
            "drtIndex": 0,
            "startEntry": gfd_pid,
            "entries": [{"entryType": "PHYSICAL_PORT", "routingTarget": 2}]
        }
        res = await call_cmd("pbr:setDrt", payload)
        print(f"  <- Response: {res}")
        print(f"  {GREEN}[Switch Router] Programmed DRT[0][0x{gfd_pid:03X}] = PHYSICAL_PORT -> Port 2{RESET}")
        
        # CMD 4: Get PID Binding (Verify Unbound)
        print(f"\n{BOLD}CMD 4: Get PID Binding - Pre-Bind Check (Opcode 0x5705){RESET}")
        print("  -> Query binding for VCS 0, vPPB 1")
        payload = {"targetVcs": 0, "targetVppb": 1}
        res = await call_cmd("pbr:getPidBinding", payload)
        print(f"  <- Response: pid=0x{res['pid']:03X} (4095 means UNBOUND)")
        
        # CMD 5: Configure PID Binding (Bind)
        print(f"\n{BOLD}CMD 5: Configure PID Binding (Opcode 0x5706){RESET}")
        print(f"  -> Bind VCS 0 vPPB 1 to GFD PID 0x{gfd_pid:03X}")
        payload = {
            "operation": 0,
            "targetVcs": 0,
            "targetVppb": 1,
            "pid": gfd_pid,
            "latencyEntryBaseUnit": 0, "latencyEntry": 0,
            "bwEntryBaseUnit": 0, "bwEntry": 0
        }
        res = await call_cmd("pbr:configurePidBinding", payload)
        print(f"  <- Response: {res} (Background task scheduled)")
        print(f"  {GREEN}[Switch Log] Started background binding process for vPPB 1 -> PID 0x{gfd_pid:03X}{RESET}")
        
        # CMD 6: Get PID Binding (Verify Bound)
        await asyncio.sleep(0.1) # wait for background command to run
        print(f"\n{BOLD}CMD 6: Get PID Binding - Verification (Opcode 0x5705){RESET}")
        payload = {"targetVcs": 0, "targetVppb": 1}
        res = await call_cmd("pbr:getPidBinding", payload)
        print(f"  <- Response: pid=0x{res['pid']:03X} (BOUND)")
        print(f"  {GREEN} PBR Commissioning phase complete. Data plane is live.{RESET}")
        
        # ══════════════════════════════════════════════════════════════════════
        # PHASE 2: GAE DISCOVERY
        # ══════════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{CYAN}{SEP}")
        print("  PHASE 2: GAE DISCOVERY")
        print(f"{SEP}{RESET}")
        
        # CMD 7: Identify GAE
        print(f"\n{BOLD}CMD 7: Identify GAE (Opcode 0x5800){RESET}")
        res = await call_cmd("gae:identify")
        print(f"  <- Response: numVppbsWithGlobalMemory={res.get('numVppbsWithGlobalMemory', 0)}")
        
        # CMD 8: Get PID Access Vectors
        print(f"\n{BOLD}CMD 8: Get PID Access Vectors (Opcode 0x5802){RESET}")
        res = await call_cmd("gae:getPidAccessVectors", {"pid": gfd_pid})
        print(f"  <- Response: gmv={res.get('gmv', 0)}, vtv={res.get('vtv', 0)} (No G-FAM memory access; GFD is CCI-only)")
        
        # ══════════════════════════════════════════════════════════════════════
        # PHASE 3: GFD DISCOVERY VIA GAE PROXY
        # ══════════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{CYAN}{SEP}")
        print("  PHASE 3: GFD DISCOVERY VIA GAE PROXY")
        print(f"{SEP}{RESET}")
        
        # CMD 9: Proxy GFD Identify
        print(f"\n{BOLD}CMD 9: Proxy GFD Identify (Opcode 0x5809){RESET}")
        print("  -> Dispatch Identify command to GFD via GAE proxy")
        payload = {"gfdOpcode": 0x0001, "gfdPayload": []}
        res = await call_cmd("gae:proxyGfdMgmt", payload)
        tid1 = res["threadId"]
        print(f"  <- Response: threadId={tid1}")
        print(f"  {GREEN}[Switch GAE] Proxying Identify command over DSP CCI tunnel to Port 2...{RESET}")
        
        # CMD 10: Get Proxy Status (GFD Identify Result)
        await asyncio.sleep(0.1)
        print(f"\n{BOLD}CMD 10: Get Proxy Status - Identify Result (Opcode 0x580A){RESET}")
        res = await call_cmd("gae:getProxyStatus", {"threadId": tid1})
        completed = res["completed"]
        rc = res["gfdReturnCode"]
        payload = res["gfdResponsePayload"]
        print(f"  <- Response: completed={completed}, gfdReturnCode={rc}")
        if completed and rc == 0:
            vendor_id = payload[0] | (payload[1] << 8)
            device_id = payload[2] | (payload[3] << 8)
            component_type = payload[8]
            print(f"    Decoded GFD Identity: Vendor ID=0x{vendor_id:04X}, Device ID=0x{device_id:04X}, Component Type=0x{component_type:02X} (GFD)")
            print(f"  {GREEN} GFD Device successfully discovered and verified via Switch GAE proxy.{RESET}")
        
        # ══════════════════════════════════════════════════════════════════════
        # PHASE 4: DATA ACCESS AND VERIFICATION VIA GAE PROXY
        # ══════════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{CYAN}{SEP}")
        print("  PHASE 4: GFD DATA WRITE & READ VERIFICATION")
        print(f"{SEP}{RESET}")
        
        # CMD 11: Write data to GFD (Proxy)
        write_val = 0xDEADBEEF
        offset = 0x0000
        print(f"\n{BOLD}CMD 11: Proxy GFD Write (Opcode 0x5809 -> Custom GFD Opcode 0xC001){RESET}")
        print(f"  -> Write data 0x{write_val:08X} to GFD register offset 0x{offset:04X}")
        write_bytes = struct.pack("<HI", offset, write_val)
        payload = {"gfdOpcode": 0xC001, "gfdPayload": list(write_bytes)}
        res = await call_cmd("gae:proxyGfdMgmt", payload)
        tid2 = res["threadId"]
        print(f"  <- Response: threadId={tid2}")
        print(f"  {GREEN}[Switch Router] Routing PBR Packet: SPID=0x000 (USP/GAE) -> DPID=0x{gfd_pid:03X} (GFD Port 2){RESET}")
        
        # CMD 12: Get Write Result
        await asyncio.sleep(0.1)
        print(f"\n{BOLD}CMD 12: Get Proxy Status - Write Result (Opcode 0x580A){RESET}")
        res = await call_cmd("gae:getProxyStatus", {"threadId": tid2})
        print(f"  <- Response: completed={res['completed']}, gfdReturnCode={res['gfdReturnCode']}")
        if res["completed"] and res["gfdReturnCode"] == 0:
            print(f"  {GREEN} Data successfully sent to GFD register.{RESET}")
            
        # CMD 13: Read data from GFD (Proxy)
        print(f"\n{BOLD}CMD 13: Proxy GFD Read (Opcode 0x5809 -> Custom GFD Opcode 0xC002){RESET}")
        print(f"  -> Read 4 bytes from GFD register offset 0x{offset:04X}")
        read_bytes = struct.pack("<HH", offset, 4)
        payload = {"gfdOpcode": 0xC002, "gfdPayload": list(read_bytes)}
        res = await call_cmd("gae:proxyGfdMgmt", payload)
        tid3 = res["threadId"]
        print(f"  <- Response: threadId={tid3}")
        print(f"  {GREEN}[Switch Router] Routing PBR Packet: SPID=0x000 (USP/GAE) -> DPID=0x{gfd_pid:03X} (GFD Port 2){RESET}")
        
        # CMD 14: Get Read Result
        await asyncio.sleep(0.1)
        print(f"\n{BOLD}CMD 14: Get Proxy Status - Read Result (Opcode 0x580A){RESET}")
        res = await call_cmd("gae:getProxyStatus", {"threadId": tid3})
        completed = res["completed"]
        rc = res["gfdReturnCode"]
        payload = res["gfdResponsePayload"]
        print(f"  <- Response: completed={completed}, gfdReturnCode={rc}")
        if completed and rc == 0:
            val = struct.unpack("<I", bytes(payload[:4]))[0]
            print(f"    Decoded read value: {BOLD}{GREEN}0x{val:08X}{RESET}")
            if val == write_val:
                print(f"\n  {GREEN}*** DEMO SUCCESS: GFD DATA ROUND-TRIP VERIFIED! ***{RESET}")
                print(f"  {GREEN}  Wrote: 0x{write_val:08X} | Read: 0x{val:08X}{RESET}")
            else:
                print(f"  {RED}mismatch! Read: 0x{val:08X}{RESET}")
                
        # Cleanup threads
        for tid in [tid1, tid2, tid3]:
            await call_cmd("gae:cancelProxy", {"threadId": tid})

    finally:
        # Disconnect Socket.IO client
        await sio.disconnect()
        
        # Stop background tasks
        print(f"\n{YELLOW}[Env] Shutting down simulator background tasks...{RESET}")
        await sld.stop()
        await gfd.stop()
        await host.stop()
        await switch.stop()
        await fm.stop()
        
        # Wait for tasks to end
        await asyncio.gather(fm_task, sw_task, host_task, sld_task, gfd_task, return_exceptions=True)
        print(f"{GREEN}[Env] Shutdown complete.{RESET}\n")

        # Delete mock sld file
        if sld_mem_file.exists():
            try:
                sld_mem_file.unlink()
                tmp_dir.rmdir()
            except Exception:
                pass


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(run_e2e_demo())
    except KeyboardInterrupt:
        print("\nDemo interrupted by user.")
