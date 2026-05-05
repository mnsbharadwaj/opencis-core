"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from asyncio import gather, create_task
from typing import List, cast, Optional

from opencis.util.logger import logger
from opencis.util.component import RunnableComponent
from opencis.util.async_gatherer import AsyncGatherer
from opencis.cxl.component.cxl_connection import FifoPair
from opencis.cxl.component.pbr_switch_manager import PbrSwitchManager
from opencis.cxl.component.hdm_decoder import PbrHdmDecoderManager
from opencis.cxl.transport.transaction import (
    BasePacket, PbrBasePacket, CxlMemBasePacket, CxlIoMemReqPacket
)


class PbrSwitchRouter(RunnableComponent):
    """
    Data Plane router for a PBR Switch.
    Listens on all incoming FIFOs, decodes PBR packets, and routes them
    using the Destination PID (DPID) and the switch's DRT.
    """
    def __init__(
        self,
        switch_id: int,
        pbr_switch_manager: PbrSwitchManager,
        port_fifos: List[FifoPair],
        hdm_decoder_manager: Optional[PbrHdmDecoderManager] = None,
    ):
        super().__init__()
        self._switch_id = switch_id
        self._pbr_switch_manager = pbr_switch_manager
        self._port_fifos = port_fifos
        self._hdm_decoder_manager = hdm_decoder_manager
        self._routing_tasks = AsyncGatherer()
        self._is_running = False

    def _create_message(self, message):
        return f"[PbrSwitchRouter:Switch{self._switch_id}] {message}"

    async def _process_port_ingress(self, ingress_port_id: int, fifo: FifoPair):
        """
        Listen to incoming packets on a specific port.
        Since physical ports can be upstream or downstream, we might receive packets
        on `host_to_target` or `target_to_host`. 
        For simplicity, we listen to `host_to_target` from DSPs and `target_to_host` from USPs,
        or we assume the caller provides a unified incoming queue.
        Assuming `fifo.host_to_target` represents packets entering the switch from this port.
        """
        while True:
            # We assume packets entering the switch from this port are put into `host_to_target`
            # Wait, in CXLConnection, host writes to host_to_target, target reads from it.
            # If the switch port is a DSP, the connected device is a target. The target writes to `target_to_host`.
            # We need to listen to the correct direction based on the port role.
            # For now, let's just listen to target_to_host since we act as the Host to the DSPs.
            # Actually, we should be passed an Ingress Queue and Egress Queue by the connection manager.
            packet = await fifo.target_to_host.get()
            if packet is None:
                break
            
            await self._route_packet(ingress_port_id, packet)



    async def _route_packet(self, ingress_port_id: int, packet: BasePacket):
        base_packet = cast(BasePacket, packet)
        logger.debug(self._create_message(f"Checking packet type: is_pbr={base_packet.is_pbr()} payload_type={base_packet.system_header.payload_type}"))
        
        # 1. PBR Decapsulation / Routing
        if base_packet.is_pbr():
            pbr_packet = cast(PbrBasePacket, packet)
            dpid = pbr_packet.pbr_header.dpid
            spid = pbr_packet.pbr_header.spid
            
            logger.debug(self._create_message(f"Received PBR Packet: SPID=0x{spid:x}, DPID=0x{dpid:x}"))
            
            # 2. DRT Lookup (drt_index=0, start_entry=dpid, num_entries=1)
            drt_result = self._pbr_switch_manager.get_drt(0, dpid, 1)
            if drt_result is None or not drt_result[0]:
                logger.warning(self._create_message(f"Drop: DPID 0x{dpid:x} not found in DRT"))
                return
            
            drt_entry = drt_result[0][0]
            if drt_entry.entry_type != 1:  # 1 = PHYSICAL_PORT
                logger.warning(self._create_message(f"Drop: DPID 0x{dpid:x} not valid in DRT"))
                return
                
            egress_port = drt_entry.routing_target
            if egress_port >= len(self._port_fifos):
                logger.warning(self._create_message(f"Drop: Invalid egress port {egress_port}"))
                return
                
            logger.debug(self._create_message(f"Routing DPID=0x{dpid:x} to port {egress_port}"))
            
            target_fifo = self._port_fifos[egress_port]
            
            # Egress Decapsulation
            # Strip the PBR header here and send the inner standard CXL packet
            # to the downstream port, as the connected device expects standard TLPs.
            inner_packet = pbr_packet.get_inner_packet()
            if inner_packet:
                logger.debug(self._create_message(f"Decapsulated PBR packet, forwarding {inner_packet.get_type()} to port {egress_port}"))
                await target_fifo.host_to_target.put(inner_packet)
            else:
                logger.warning(self._create_message("Inner packet not available, forwarding raw PBR packet"))
                await target_fifo.host_to_target.put(pbr_packet)
            
        else:
            # HBR Packet Ingress: Needs Encapsulation
            # The packet arrived without a PBR header. We must be an Ingress Edge Port.
            logger.debug(self._create_message(f"Received HBR packet on port {ingress_port_id}"))
            
            if not self._hdm_decoder_manager:
                logger.warning(self._create_message("HBR-to-PBR encapsulation requires HDM Address Decoders. Dropping HBR packet."))
                return

            address = None
            if base_packet.is_cxl_mem():
                cxl_mem_packet = cast(CxlMemBasePacket, packet)
                if hasattr(cxl_mem_packet, "get_address"):
                    address = cxl_mem_packet.get_address()
            elif base_packet.is_cxl_io():
                # For testing and some MMIO
                if isinstance(packet, CxlIoMemReqPacket) or hasattr(packet, "get_address"):
                    cxl_io_packet = cast(CxlIoMemReqPacket, packet)
                    address = cxl_io_packet.get_address()

            if address is None:
                logger.warning(self._create_message(f"Could not extract address from HBR packet {base_packet.get_type()}. Dropping."))
                return
            
            dpid = self._hdm_decoder_manager.get_dpid(address)
            if dpid is None:
                logger.warning(self._create_message(f"No DPID mapping found for address 0x{address:x}. Dropping."))
                return

            # Find SPID for this port (mocking it as ingress_port_id for this standalone logic, 
            # or looking it up in PbrSwitchManager)
            # In opencis-core, bindings are to (vcs_id, vppb_id). 
            # We'll use the port ID as a simplified SPID for now, or fetch from bindings.
            spid = ingress_port_id 

            logger.debug(self._create_message(f"Encapsulating HBR packet to DPID=0x{dpid:x}"))
            pbr_packet = PbrBasePacket.create(spid=spid, dpid=dpid, inner_packet=packet)
            
            # Feed it back into the routing logic as a PBR packet
            await self._route_packet(ingress_port_id, pbr_packet)

    async def _run(self):
        self._is_running = True
        for i, fifo in enumerate(self._port_fifos):
            self._routing_tasks.add_task(self._process_port_ingress(i, fifo))
        await self._change_status_to_running()
        await self._routing_tasks.wait_for_completion()

    async def _stop(self):
        for fifo in self._port_fifos:
            await fifo.host_to_target.put(None)
            await fifo.target_to_host.put(None)
