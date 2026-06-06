"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio
from asyncio import gather, create_task
from typing import List, cast, Optional

from opencis.util.logger import logger
from opencis.util.component import RunnableComponent
from opencis.util.async_gatherer import AsyncGatherer
from opencis.pci.component.fifo_pair import FifoPair
from opencis.cxl.component.pbr_switch_manager import PbrSwitchManager, DrtEntryType
from opencis.cxl.component.hdm_decoder import PbrHdmDecoderManager
from opencis.cxl.transport.packet_constants import SYSTEM_PAYLOAD_TYPE
from opencis.cxl.transport.common import BasePacket
from opencis.cxl.transport.pbr_packets import PbrBasePacket
from opencis.cxl.transport.cxl_mem_packets import CxlMemBasePacket
from opencis.cxl.transport.cxl_io_packets import CxlIoMemReqPacket


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
        port_types: Optional[List[bool]] = None,  # True = USP, False/None = DSP
    ):
        """Initialize the PBR switch data-plane router.

        Sets up the per-port FIFO references and routing infrastructure.
        No tasks are started until ``_run()`` is called.

        Args:
            switch_id: Numeric identifier for this switch (used in log messages).
            pbr_switch_manager: The ``PbrSwitchManager`` that owns the DRT tables
                and PID assignments used for DPID-based routing lookups.
            port_fifos: List of ``FifoPair`` objects, one per switch port.
                Index 0 is typically the USP; indices 1+ are DSPs.
            hdm_decoder_manager: Optional ``PbrHdmDecoderManager`` for
                HBR-to-PBR address-based encapsulation.  If ``None``, any
                HBR packets that arrive will be dropped.
            port_types: Per-port boolean flags where ``True`` marks a USP
                and ``False`` (or ``None``) marks a DSP.  Defaults to all-DSP
                if not provided.
        """
        super().__init__()
        self._switch_id = switch_id
        self._pbr_switch_manager = pbr_switch_manager
        self._port_fifos = port_fifos
        self._hdm_decoder_manager = hdm_decoder_manager
        # port_types[i] = True means port i is a USP (host writes to host_to_target).
        # If None, all ports treated as DSP (device writes to target_to_host).
        self._port_types = port_types or [False] * len(port_fifos)
        self._routing_tasks = AsyncGatherer()
        self._is_running = False

    def _create_message(self, message):
        """Format a log message with the switch-specific prefix.

        Args:
            message: The message body to format.

        Returns:
            A string prefixed with ``[PbrSwitchRouter:Switch{id}]``.
        """
        return f"[PbrSwitchRouter:Switch{self._switch_id}] {message}"

    async def _process_port_ingress(self, ingress_port_id: int, fifo: "FifoPair"):
        """
        Listens on target_to_host (device → switch direction, e.g. DSP GFD → switch).
        Routes the packet and writes decapsulated output to egress port's host_to_target.
        """
        while True:
            packet = await fifo.target_to_host.get()
            if packet is None:
                break
            await self._route_packet(ingress_port_id, packet, egress_direction="host_to_target")

    async def _process_port_host_ingress(self, ingress_port_id: int, fifo: "FifoPair"):
        """
        Listens on host_to_target (USP/host → switch direction).

        The host writes commands to host_to_target on the USP port.
        The router reads them, resolves via DRT, and delivers to the egress DSP
        device port's host_to_target (the device reads from host_to_target).
        Both directions write to host_to_target at egress so that the DSP's
        target_to_host listener does not create a routing loop.
        """
        while True:
            packet = await fifo.host_to_target.get()
            if packet is None:
                break
            await self._route_packet(ingress_port_id, packet, egress_direction="host_to_target")

    async def _route_packet(
        self,
        ingress_port_id: int,
        packet: BasePacket,
        egress_direction: str = "host_to_target",
    ):
        """Route a single packet through the PBR switch.

        Handles two packet categories:
          1. **PBR packets** — already have a DPID in the PBR header.
             The DRT is looked up to find the egress physical port, the
             PBR header is stripped (decapsulation), and the inner payload
             is forwarded to the egress FIFO.
          2. **HBR packets** (CXL.mem / CXL.io) — have no DPID.  The
             address is extracted and looked up via the HDM decoder to
             obtain a DPID, then the packet is PBR-encapsulated and
             re-routed through this same method (recursive call hits the
             PBR branch).

        Args:
            ingress_port_id: Index of the port that received this packet.
            packet: The raw packet read from the ingress FIFO.
            egress_direction: Which half of the egress FifoPair to write to.
                Either ``"host_to_target"`` or ``"target_to_host"``.
        """
        base_packet = cast(BasePacket, packet)
        logger.debug(self._create_message(
            f"Checking packet type: is_pbr={base_packet.is_pbr()} "
            f"payload_type={base_packet.system_header.payload_type}"
        ))

        # ── PBR packet: DRT lookup → decapsulate → forward ──────────────────
        if base_packet.is_pbr():
            pbr_packet = cast(PbrBasePacket, packet)
            dpid = pbr_packet.pbr_header.dpid
            spid = pbr_packet.pbr_header.spid

            logger.debug(self._create_message(
                f"Received PBR Packet: SPID=0x{spid:x}, DPID=0x{dpid:x}"
            ))

            drt_result = self._pbr_switch_manager.get_drt(0, dpid, 1)
            if drt_result is None or not drt_result[0]:
                logger.warning(self._create_message(
                    f"Drop: DPID 0x{dpid:x} not found in DRT"
                ))
                return

            drt_entry = drt_result[0][0]
            if drt_entry.entry_type != DrtEntryType.PHYSICAL_PORT:
                logger.warning(self._create_message(
                    f"Drop: DPID 0x{dpid:x} DRT entry is not PHYSICAL_PORT"
                ))
                return

            egress_port = drt_entry.routing_target
            if egress_port >= len(self._port_fifos):
                logger.warning(self._create_message(
                    f"Drop: Invalid egress port {egress_port}"
                ))
                return

            logger.debug(self._create_message(
                f"Routing DPID=0x{dpid:x} to port {egress_port} [{egress_direction}]"
            ))

            # Decapsulate
            inner_packet = getattr(pbr_packet, "_inner_packet", None)
            if inner_packet is None:
                payload_offset = pbr_packet.get_payload_offset()
                raw_bytes = bytes(pbr_packet)
                if len(raw_bytes) > payload_offset:
                    try:
                        inner_packet = BasePacket(raw_bytes[payload_offset:])
                    except Exception as e:
                        logger.warning(self._create_message(
                            f"Failed to reconstruct inner packet: {e}"
                        ))

            target_fifo = self._port_fifos[egress_port]
            out_packet = inner_packet if inner_packet else pbr_packet
            if egress_direction == "host_to_target":
                await target_fifo.host_to_target.put(out_packet)
            else:
                await target_fifo.target_to_host.put(out_packet)

        # ── HBR packet: look up DPID via HDM decoder → encapsulate → recurse ─
        else:
            logger.debug(self._create_message(
                f"Received HBR packet on port {ingress_port_id}"
            ))

            if not self._hdm_decoder_manager:
                logger.warning(self._create_message(
                    "HBR-to-PBR encapsulation requires HDM Address Decoders. Dropping."
                ))
                return

            address = None
            if base_packet.is_cxl_mem():
                cxl_mem_packet = cast(CxlMemBasePacket, packet)
                if hasattr(cxl_mem_packet, "get_address"):
                    address = cxl_mem_packet.get_address()
            elif base_packet.is_cxl_io():
                if isinstance(packet, CxlIoMemReqPacket) or hasattr(packet, "get_address"):
                    address = cast(CxlIoMemReqPacket, packet).get_address()

            if address is None:
                logger.warning(self._create_message(
                    f"Could not extract address from HBR packet {base_packet.get_type()}. Dropping."
                ))
                return

            dpid = self._hdm_decoder_manager.get_dpid(address)
            if dpid is None:
                logger.warning(self._create_message(
                    f"No DPID mapping for address 0x{address:x}. Dropping."
                ))
                return

            spid = ingress_port_id
            logger.debug(self._create_message(
                f"Encapsulating HBR to DPID=0x{dpid:x}"
            ))
            pbr_packet = PbrBasePacket.encapsulate(spid=spid, dpid=dpid, inner_packet=packet)
            # Route the now-PBR packet through the same logic (will hit the PBR branch)
            await self._route_packet(ingress_port_id, pbr_packet, egress_direction)

    async def _run(self):
        """Start per-port ingress listener tasks and await completion.

        For each port, spawns an appropriate ingress listener based on
        whether the port is a USP (host-facing) or DSP (device-facing).
        Once all listeners are registered, transitions to RUNNING and
        blocks until all tasks complete (i.e. until sentinels arrive).
        """
        self._is_running = True
        for i, fifo in enumerate(self._port_fifos):
            is_usp = self._port_types[i] if i < len(self._port_types) else False
            if is_usp:
                # USP: host writes to host_to_target → router reads and routes to DSP
                # Output goes to egress DSP port's target_to_host (toward device)
                self._routing_tasks.add_task(self._process_port_host_ingress(i, fifo))
            else:
                # DSP: device writes to target_to_host → router reads and routes to USP
                # Output goes to egress USP port's host_to_target (toward host)
                self._routing_tasks.add_task(self._process_port_ingress(i, fifo))
        await self._change_status_to_running()
        await self._routing_tasks.wait_for_completion()

    async def _stop(self):
        """Stop all ingress listener tasks by injecting sentinels.

        Sends ``None`` on both directions of every port's FIFO so that
        both ``_process_port_ingress`` and ``_process_port_host_ingress``
        break out of their infinite loops.
        """
        for fifo in self._port_fifos:
            await fifo.host_to_target.put(None)
            await fifo.target_to_host.put(None)

