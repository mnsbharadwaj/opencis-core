#!/usr/bin/env python3
"""
demo_commissioning.py
=====================
A standalone demo script showcasing the complete 13-command control-plane
commissioning sequence for a PBR Switch topology containing:
  - Port 1: Multi-Logical Device (MLD)
  - Port 2: Generic Fabric Device (GFD)
  - VCS 0 / vPPB 0: Virtual Port-Based Bridge on Host Edge (USP)

This script instantiates the control-plane components locally, communicates
over MCTP-over-TCP localhost sockets, and executes all 13 commands, printing
their output to the terminal.
"""

import asyncio
from typing import List

from opencis.util.logger import logger
from opencis.cxl.component.mctp.mctp_connection_manager import MctpConnectionManager
from opencis.cxl.component.mctp.mctp_connection_client import MctpConnectionClient
from opencis.cxl.component.mctp.mctp_cci_executor import MctpCciExecutor
from opencis.cxl.component.mctp.mctp_cci_api_client import MctpCciApiClient
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencis.cxl.component.physical_port_manager import PortConfig, PORT_TYPE
from opencis.cxl.component.pbr_switch_manager import (
    PbrSwitchManager,
    PidTarget,
    PidTargetType,
    DrtEntry,
    DrtEntryType,
    PID_UNASSIGNED,
)
from opencis.cxl.component.gae_manager import GaeManager
from opencis.cxl.cci.common import CCI_RETURN_CODE, CCI_FM_API_COMMAND_OPCODE
from opencis.cxl.transport.cci_packets import CciMessagePacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY

# Import Command Classes for Registration
from opencis.cxl.cci.fabric_manager.pbr_switch import (
    IdentifyPbrSwitchCommand,
    ConfigurePidAssignmentCommand,
    GetPidBindingCommand,
    ConfigurePidBindingCommand,
    GetDrtCommand,
    SetDrtCommand,
    ConfigurePidAssignmentRequestPayload,
    PidAssignmentEntry,
    GetPidBindingRequestPayload,
    ConfigurePidBindingRequestPayload,
    GetDrtRequestPayload,
    SetDrtRequestPayload,
)
from opencis.cxl.cci.fabric_manager.gae import (
    IdentifyGaeCommand,
    GetPidAccessVectorsCommand,
    ProxyGfdMgmtCommand,
    GetProxyThreadStatusCommand,
    CancelProxyThreadCommand,
)
from opencis.cxl.cci.fabric_manager.gae.proxy_gfd_mgmt import ProxyGfdMgmtRequestPayload
from opencis.cxl.cci.fabric_manager.gae.get_proxy_thread_status import GetProxyThreadStatusRequestPayload

# Import MLD Payloads for parsing/dumping
from opencis.cxl.cci.fabric_manager.mld_components.get_ld_info import GetLdInfoResponsePayload
from opencis.cxl.cci.fabric_manager.mld_components.get_ld_allocations import GetLdAllocationsResponsePayload
from opencis.cxl.cci.fabric_manager.mld_components.set_ld_allocations import (
    SetLdAllocationsRequestPayload,
    SetLdAllocationsResponsePayload,
)

# ---------------------------------------------------------------------------
# Mock MLD responder for Port 1
# ---------------------------------------------------------------------------

async def mock_mld_port_responder(cxl_conn: CxlConnection):
    """
    Listens on Port 1's cci_fifo and responds to GetLdInfo, GetLdAllocations,
    and SetLdAllocations commands to prevent the switch from hanging.
    """
    logger.info("[MLD Mock] Mock responder task started for Port 1 cci_fifo")
    try:
        while True:
            packet = await cxl_conn.cci_fifo.host_to_target.get()
            if packet is None:
                break

            opcode = packet.cci_msg_header.command_opcode
            tag = packet.cci_msg_header.message_tag

            if opcode == CCI_FM_API_COMMAND_OPCODE.GET_LD_INFO:
                logger.info("[MLD Mock] Received GET_LD_INFO")
                payload = GetLdInfoResponsePayload(memory_size=1024, ld_count=16, qos_telemetry_capability=0).dump()
                rc = CCI_RETURN_CODE.SUCCESS
            elif opcode == CCI_FM_API_COMMAND_OPCODE.GET_LD_ALLOCATIONS:
                logger.info("[MLD Mock] Received GET_LD_ALLOCATIONS")
                payload = GetLdAllocationsResponsePayload(number_of_lds=0, ld_allocation_list=[]).dump()
                rc = CCI_RETURN_CODE.SUCCESS
            elif opcode == CCI_FM_API_COMMAND_OPCODE.SET_LD_ALLOCATIONS:
                logger.info("[MLD Mock] Received SET_LD_ALLOCATIONS")
                payload = SetLdAllocationsResponsePayload(number_of_lds=4, start_ld_id=0, ld_allocation_list=[]).dump()
                rc = CCI_RETURN_CODE.SUCCESS
            else:
                payload = b""
                rc = CCI_RETURN_CODE.UNSUPPORTED

            resp_msg = CciMessagePacket.create(
                message_category=CCI_MCTP_MESSAGE_CATEGORY.RESPONSE,
                opcode=opcode,
                data=payload,
                message_tag=tag,
                return_code=int(rc),
            )
            await cxl_conn.cci_fifo.target_to_host.put(resp_msg)
    except Exception as e:
        logger.error(f"[MLD Mock] Exception: {e}")

# ---------------------------------------------------------------------------
# Standalone GFD Executor Mock
# ---------------------------------------------------------------------------

class MockGfdExecutor:
    """Mock GFD CciExecutor that returns a GFD Identify payload."""
    async def run(self):
        pass
    async def stop(self):
        pass
    async def wait_for_ready(self):
        pass
    async def execute_command(self, request):
        from opencis.cxl.cci.generic.information_and_status.identify import IdentifyResponsePayload, IdentifyComponentType
        from opencis.pci.component.pci import EEUM_VID, SW_GFD_DID
        resp = CciResponse()
        resp.return_code = CCI_RETURN_CODE.SUCCESS
        identity = IdentifyResponsePayload(
            vendor_id=EEUM_VID,
            device_id=SW_GFD_DID,
            sub_system_vendor_id=EEUM_VID,
            sub_system_id=0,
            serial_number=0x123456,
            max_supported_msg_size=10,
            component_type=IdentifyComponentType.GFD,
        )
        resp.payload = identity.dump()
        return resp

# ---------------------------------------------------------------------------
# Helper Targets for PbrSwitchManager
# ---------------------------------------------------------------------------

def make_targets() -> List[PidTarget]:
    return [
        PidTarget(
            target_id=1,
            target_type=PidTargetType.DOWNSTREAM_EDGE_PORT,
            instance_id=0,
            vcs_id=0,
            physical_port_id=1,
        ),
        PidTarget(
            target_id=2,
            target_type=PidTargetType.DOWNSTREAM_EDGE_PORT,
            instance_id=0,
            vcs_id=0,
            physical_port_id=2,
        ),
    ]

# ---------------------------------------------------------------------------
# Main Demo Flow
# ---------------------------------------------------------------------------

async def main():
    # Set logging level to INFO to see the flow clearly
    logger.set_stdout_levels(loglevel="INFO")
    print("\n" + "="*80)
    print(" CXL 4.0 CONTROL PLANE COMMISSIONING DEMO (1-MLD & 1-GFD)")
    print("="*80 + "\n")

    # 1. FM side MCTP Server (ephemeral port)
    mctp_mgr = MctpConnectionManager(host="127.0.0.1", port=0)
    await mctp_mgr.run_wait_ready()
    mctp_port = mctp_mgr._server_component.get_port()
    print(f"[FM] Started MCTP-over-TCP Server on port {mctp_port}")

    # 2. Switch side MCTP Client
    mctp_client = MctpConnectionClient(host="127.0.0.1", port=mctp_port, auto_reconnect=False)
    await mctp_client.run_wait_ready()
    sw_mctp_conn = mctp_client.get_mctp_connection()
    print("[Switch] Connected to FM MCTP Server")

    # 3. Setup port configs for the Switch
    # Port 0: USP, Port 1: DSP (MLD), Port 2: DSP (GFD)
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP)
    ]
    sw_conn_mgr = SwitchConnectionManager(port_configs=port_configs, host="127.0.0.1", port=0)
    
    # 4. Instantiate PbrSwitchManager & GaeManager
    pbr_mgr = PbrSwitchManager(pid_targets=make_targets())
    gae_mgr = GaeManager(vppbs=[], label="GAE:VCS0")
    
    # Setup Mock GFD Executor for Proxy commands
    mock_gfd_exec = MockGfdExecutor()
    gae_mgr.set_gfd_executor(mock_gfd_exec)

    # 5. Start MctpCciExecutor on the Switch
    executor = MctpCciExecutor(
        mctp_connection=sw_mctp_conn,
        switch_connection_manager=sw_conn_mgr,
        port_configs=port_configs,
    )
    
    # Register all PBR & GAE Commands
    executor.register_cci_commands([
        IdentifyPbrSwitchCommand(pbr_mgr),
        ConfigurePidAssignmentCommand(pbr_mgr),
        GetPidBindingCommand(pbr_mgr),
        ConfigurePidBindingCommand(pbr_mgr),
        GetDrtCommand(pbr_mgr),
        SetDrtCommand(pbr_mgr),
        IdentifyGaeCommand(gae_mgr),
        GetPidAccessVectorsCommand(gae_mgr),
        ProxyGfdMgmtCommand(gae_mgr),
        GetProxyThreadStatusCommand(gae_mgr),
        CancelProxyThreadCommand(gae_mgr),
    ])
    await executor.run_wait_ready()
    print("[Switch] MctpCciExecutor active (commands registered)")

    # 6. Start the Mock MLD responder for Port 1
    mld_conn = sw_conn_mgr.get_cxl_connection(1)
    mld_responder_task = asyncio.create_task(mock_mld_port_responder(mld_conn))

    # 7. Start the FM Client
    api = MctpCciApiClient(mctp_mgr.get_mctp_connection())
    await api.run_wait_ready()
    print("[FM] MctpCciApiClient active")
    print("-"*80)

    # =======================================================================
    # 13 COMMISSIONING COMMANDS EXECUTION
    # =======================================================================

    # Command 1: Identify PBR Switch (0x5700)
    print("\n--- Command 1: Identify PBR Switch ---")
    rc, resp = await api.identify_pbr_switch()
    print(f"Status: {rc.name}")
    print(f"Response:\n{resp.get_pretty_print()}")

    # Command 2: Identify GAE (0x5800)
    print("\n--- Command 2: Identify GAE ---")
    rc, resp = await api.identify_gae()
    print(f"Status: {rc.name}")
    print(f"Response:\n{resp.get_pretty_print()}")

    # Command 3: Get LD Info (0x0101) - targeted to Port 1
    print("\n--- Command 3: Get LD Info (Port 1 MLD) ---")
    # send_raw_cci is used to issue downstream MLD commands
    rc, resp_bytes, _ = await api.send_raw_cci(CCI_FM_API_COMMAND_OPCODE.GET_LD_INFO, port_index=1)
    print(f"Status: {rc.name}")
    ld_info = GetLdInfoResponsePayload.parse(resp_bytes)
    print(f"Response:\n{ld_info.get_pretty_print()}")

    # Command 4: Get LD Allocations (0x0102) - targeted to Port 1
    print("\n--- Command 4: Get LD Allocations (Port 1 MLD) ---")
    rc, resp_bytes, _ = await api.send_raw_cci(CCI_FM_API_COMMAND_OPCODE.GET_LD_ALLOCATIONS, port_index=1)
    print(f"Status: {rc.name}")
    ld_alloc = GetLdAllocationsResponsePayload.parse(resp_bytes)
    print(f"Response: num_allocated_lds={ld_alloc.number_of_lds}")

    # Command 5: Set LD Allocations (0x0103) - targeted to Port 1
    print("\n--- Command 5: Set LD Allocations (Port 1 MLD) ---")
    req_payload = SetLdAllocationsRequestPayload(
        start_ld_id=0,
        number_of_lds=4,
        ld_allocation_list=[(1, 0), (1, 0), (1, 0), (1, 0)]
    ).dump()
    rc, resp_bytes, _ = await api.send_raw_cci(CCI_FM_API_COMMAND_OPCODE.SET_LD_ALLOCATIONS, req_payload, port_index=1)
    print(f"Status: {rc.name}")
    ld_alloc_resp = SetLdAllocationsResponsePayload.parse(resp_bytes)
    print(f"Response: allocated_count={ld_alloc_resp.number_of_lds}")

    # Command 6: Configure PID Assignment (0x5704)
    print("\n--- Command 6: Configure PID Assignment (Port 2 GFD) ---")
    req = ConfigurePidAssignmentRequestPayload(
        operation=0,  # ASSIGN
        entries=[PidAssignmentEntry(pid=0x010, target_id=2, instance_id=0)]
    )
    rc, _ = await api.configure_pid_assignment(req)
    print(f"Status: {rc.name}")

    # Command 7: Set DRT (0x5709)
    print("\n--- Command 7: Set DRT (PID 0x010 -> Port 2) ---")
    req = SetDrtRequestPayload(
        drt_index=0,
        start_entry=0x010,
        entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=2)]
    )
    rc, _ = await api.set_drt(req)
    print(f"Status: {rc.name}")

    # Command 8: Get DRT (0x5708)
    print("\n--- Command 8: Get DRT (PID 0x010) ---")
    req = GetDrtRequestPayload(drt_index=0, start_entry=0x010, num_entries=1)
    rc, resp = await api.get_drt(req)
    print(f"Status: {rc.name}")
    print(f"Response: entry_type={resp.entries[0].entry_type.name}, target={resp.entries[0].routing_target}")

    # Command 9: Get PID Binding (0x5705) - Before BIND
    print("\n--- Command 9: Get PID Binding (VCS 0 / vPPB 0 - Pre-Bind) ---")
    req = GetPidBindingRequestPayload(target_vcs=0, target_vppb=0)
    rc, resp = await api.get_pid_binding(req)
    print(f"Status: {rc.name}")
    print(f"Response: bound_pid={hex(resp.pid)}")

    # Command 10: Configure PID Binding (0x5706) - BIND
    print("\n--- Command 10: Configure PID Binding (VCS 0 / vPPB 0 -> PID 0x010) ---")
    req = ConfigurePidBindingRequestPayload(
        operation=0,  # BIND
        target_vcs=0,
        target_vppb=0,
        pid=0x010
    )
    rc, _ = await api.configure_pid_binding(req)
    print(f"Status: {rc.name}")

    # Command 11: Get PID Access Vectors (0x5802)
    print("\n--- Command 11: Get PID Access Vectors (PID 0x010) ---")
    rc, resp = await api.get_pid_access_vectors(pid=0x010)
    print(f"Status: {rc.name}")
    print(f"Response: pid={hex(resp.pid)}, gmv={resp.gmv}, vtv={resp.vtv}")

    # Command 12: Proxy GFD Management Command (0x5809)
    print("\n--- Command 12: Proxy GFD Identify Command ---")
    rc, resp = await api.proxy_gfd_mgmt(gfd_opcode=0x0001)
    print(f"Status: {rc.name}")
    thread_id = resp.thread_id
    print(f"Response: thread_id={thread_id}")

    # Command 13: Get Proxy Thread Status (0x580A)
    print("\n--- Command 13: Get Proxy Thread Status (Thread 1) ---")
    await asyncio.sleep(0.05)  # Allow small delay for background task
    rc, resp = await api.get_proxy_thread_status(thread_id=thread_id)
    print(f"Status: {rc.name}")
    print(f"Response: completed={resp.completed}, gfd_rc={resp.gfd_return_code}")
    from opencis.cxl.cci.generic.information_and_status.identify import IdentifyResponsePayload
    gfd_identity = IdentifyResponsePayload.parse(resp.gfd_response_payload)
    print(f"GFD Component Type: {gfd_identity.component_type.name} ({hex(gfd_identity.component_type)})")

    # =======================================================================
    # TEARDOWN
    # =======================================================================
    print("\n" + "="*80)
    print(" SHUTTING DOWN COMPONENTS")
    print("="*80)
    
    # Stop mock MLD responder task
    await mld_conn.cci_fifo.host_to_target.put(None)
    await mld_responder_task

    # Stop FM/Switch components
    await api.stop()
    await executor.stop()
    await mctp_client.stop()
    await mctp_mgr.stop()
    print("[FM/Switch] Clean shutdown complete.\n")

if __name__ == "__main__":
    asyncio.run(main())
