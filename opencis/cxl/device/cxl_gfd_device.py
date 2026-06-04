"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

CXL Generic Fabric Device (GFD) — Spec-Correct Implementation
--------------------------------------------------------------
CXL 4.0 §7.7.13: A GFD is a FABRIC device that attaches to a PBR switch
DSP port. It is NOT a PCIe endpoint and is NOT enumerated by the host.

Key properties (per spec):
  - NO BAR visible to the host.
  - NO PCIe config space enumerable by the host.
  - Has a CCI mailbox reachable via cci_fifo (DSP port on the switch).
  - FM sends CCI to GFD via FabricCrawlOut (0x5701) → DspCciTunnel → cci_fifo.
  - Host sends CCI to GFD via GAE Proxy (0x5809) → GaeManager → DspCciTunnel → cci_fifo.
  - GFD responds to CCI commands (Identify 0x0001, vendor-specific, etc.).

What was removed vs the previous (incorrect) implementation:
  - CxlIoManager       — drove PCIe config-space + MMIO BAR (wrong for GFD)
  - CxlMemManager      — idle stub for CXL.mem (GFD is IO-only, never used)
  - GfdMmioRegisters   — BAR-0 register file (host-visible; spec says NO BAR)
  - CxlType3SldConfigSpace — PCIe config space (host can't enumerate GFD)
  - _init_device callback  — set up BAR and config space
  - PciComponent, BarEntry — PCIe-specific constructs

What the GFD now has:
  - CciExecutor: dispatches received CCI commands to registered handlers.
  - _run_cci_mailbox(): reads CciMessagePacket from cci_fifo.host_to_target,
    dispatches to CciExecutor, writes response to cci_fifo.target_to_host.
  - IdentifyCommand registered at startup (component_type = GFD = 0x04).

Lifecycle:
  1. GenericFabricDevice opens TCP to switch (SwitchConnectionClient).
  2. Switch assigns DSP port; CxlPacketProcessor routes packets to CxlConnection FIFOs.
  3. FM issues FabricCrawlOut(0x5701) → DspCciTunnel → cci_fifo.host_to_target.
  4. _run_cci_mailbox() reads packet → CciExecutor → response → cci_fifo.target_to_host.
  5. DspCciTunnel._drain_responses() picks up response → returns to FM.
"""

import asyncio
from asyncio import create_task, gather
from typing import Optional

from opencis.util.logger import logger
from opencis.util.component import RunnableComponent
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.cci_executor import CciExecutor, CciRequest, CciResponse
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.cci.generic.information_and_status.identify import (
    IdentifyCommand,
    IdentifyComponentType,
    IdentifyResponsePayload,
)
from opencis.cxl.transport.cci_packets import CciMessagePacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.pci.component.pci import EEUM_VID, SW_GFD_DID


class CxlGfdDevice(RunnableComponent):
    """
    CXL Generic Fabric Device (GFD) — spec-correct implementation.

    The GFD has NO BAR and NO PCIe config space. It is a pure CCI-mailbox
    fabric device. All management is done via cci_fifo (FabricCrawlOut or
    GAE proxy path).

    Parameters
    ----------
    transport_connection:
        ``CxlConnection`` provided by ``SwitchConnectionClient`` or injected
        in test mode. Only ``cci_fifo`` is used; mmio_fifo and cfg_fifo are
        intentionally ignored.
    port_index:
        PBR switch DSP port number this device is attached to.
    serial_number:
        16-hex-digit string, e.g. ``"0000000000000001"``.
    label:
        Optional log label.
    """

    def __init__(
        self,
        transport_connection: CxlConnection,
        port_index: int = 0,
        serial_number: str = "0000000000000001",
        label: Optional[str] = None,
    ):
        label = label or f"GFD:Port{port_index}"
        super().__init__(label)

        self._port_index = port_index
        self._serial_number = serial_number
        # Only cci_fifo is used — mmio_fifo and cfg_fifo are not wired
        self._cci_fifo = transport_connection.cci_fifo

        # CCI executor dispatches incoming commands to registered handlers
        self._cci_executor = CciExecutor(label=label)

        # Register CCI Identify — identifies this device as a GFD (0x04)
        self._register_cci_commands()

    # ── CCI command registration ───────────────────────────────────────────────

    def _register_cci_commands(self) -> None:
        """Register all CCI commands supported by this GFD."""
        serial_int = int(self._serial_number, 16) if self._serial_number else 1
        identity = IdentifyResponsePayload(
            vendor_id=EEUM_VID,
            device_id=SW_GFD_DID,
            sub_system_vendor_id=EEUM_VID,
            sub_system_id=0,
            serial_number=serial_int,
            max_supported_msg_size=10,
            component_type=IdentifyComponentType.GFD,
        )
        self._cci_executor.register_command(
            IdentifyCommand.OPCODE,
            IdentifyCommand(identity, label=self._label),
        )
        logger.debug(self._create_message(
            "Registered CCI Identify (component_type=GFD=0x04)"
        ))

    # ── CCI mailbox loop ───────────────────────────────────────────────────────

    async def _run_cci_mailbox(self) -> None:
        """
        CCI mailbox dispatch loop.

        Reads CCI request packets from cci_fifo.host_to_target (put there by
        DspCciTunnel.send_and_wait() on the switch side), dispatches to
        CciExecutor, and writes the response back to cci_fifo.target_to_host.

        Packet format on the wire (cci_fifo.host_to_target):
          - DspCciTunnel puts a raw CciMessagePacket.
          - MctpCciExecutor (for MLD opcodes) puts a CciRequestPacket subclass
            which also has .cci_msg_header and .get_cci_message().

        We handle both by extracting CciMessagePacket before processing.
        """
        logger.debug(self._create_message("CCI mailbox started"))
        while True:
            packet = await self._cci_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("CCI mailbox: sentinel received, stopping"))
                break

            # ── Extract CciMessagePacket ──────────────────────────────────────
            try:
                if isinstance(packet, CciMessagePacket):
                    cci_msg = packet
                elif hasattr(packet, "get_cci_message"):
                    cci_msg = packet.get_cci_message()
                else:
                    cci_msg = CciMessagePacket(bytearray(bytes(packet)))
            except Exception as exc:
                logger.error(self._create_message(
                    f"CCI mailbox: failed to parse incoming packet: {exc}"
                ))
                continue

            opcode = cci_msg.cci_msg_header.command_opcode
            tag = cci_msg.cci_msg_header.message_tag
            payload = cci_msg.get_payload()

            logger.debug(self._create_message(
                f"CCI mailbox: received opcode={opcode:#06x} tag={tag} "
                f"payload_len={len(payload)}"
            ))

            # ── Dispatch to CciExecutor ──────────────────────────────────────
            request = CciRequest(opcode=opcode, payload=payload)
            try:
                response: CciResponse = await self._cci_executor.execute_command(request)
            except Exception as exc:
                logger.error(self._create_message(
                    f"CCI mailbox: CciExecutor raised for opcode={opcode:#06x}: {exc}"
                ))
                response = CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)

            # ── Build response CciMessagePacket ──────────────────────────────
            resp_msg = CciMessagePacket.create(
                data=response.payload or b"",
                message_category=CCI_MCTP_MESSAGE_CATEGORY.RESPONSE,
                opcode=opcode,
                message_tag=tag,
                return_code=int(response.return_code),
            )

            await self._cci_fifo.target_to_host.put(resp_msg)
            logger.debug(self._create_message(
                f"CCI mailbox: sent response opcode={opcode:#06x} tag={tag} "
                f"rc={response.return_code.name if hasattr(response.return_code, 'name') else response.return_code}"
            ))

    # ── Public helpers ─────────────────────────────────────────────────────────

    def get_cci_executor(self) -> CciExecutor:
        """Return the CCI executor (useful for test introspection or extra command registration)."""
        return self._cci_executor

    # ── RunnableComponent lifecycle ────────────────────────────────────────────

    async def _run(self):
        logger.info(self._create_message(
            "Starting (spec-correct: NO BAR, NO PCIe config space, CCI-mailbox only)"
        ))
        run_tasks = [
            create_task(self._cci_executor.run()),
            create_task(self._run_cci_mailbox()),
        ]
        wait_tasks = [
            create_task(self._cci_executor.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        logger.info(self._create_message("Ready — CCI mailbox active on cci_fifo"))
        await gather(*run_tasks)
        logger.info(self._create_message("Stopped"))

    async def _stop(self):
        logger.info(self._create_message("Stopping"))
        # Signal the mailbox loop to exit
        await self._cci_fifo.host_to_target.put(None)
        await self._cci_executor.stop()
