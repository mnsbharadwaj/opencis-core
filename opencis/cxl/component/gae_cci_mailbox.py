"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

GaeCciMailbox — Host-to-GAE CCI mailbox on the switch USP
==========================================================
CXL 4.0 §7.7.14: The GAE (Generic Access Endpoint) lives on the switch USP.
The host can send CCI commands (0x5800–0x580B) directly to the GAE via the
CXL connection's cci_fifo channel.

This component:
 1. Reads CciMessagePacket from  cci_fifo.host_to_target  (from the host)
 2. Dispatches to a shared CciExecutor (same executor used by MctpCciExecutor)
 3. Writes CciMessagePacket response to  cci_fifo.target_to_host  (to the host)

Wire path (port 8000 TCP — same connection as CXL.mem / PCIe cfg):
    CxlSimpleHost.gae_command(opcode, payload)
      → CxlRootPortDevice.send_cci()
      → cci_fifo.host_to_target  (R-side → across TCP → USP-side)
      → GaeCciMailbox._run_mailbox()
      → CciExecutor.execute_command()
      → cci_fifo.target_to_host  (USP-side → across TCP → R-side)
      → CxlRootPortDevice.recv_cci()
      → CxlSimpleHost.gae_command() returns (return_code, payload)

This is the same pattern as CxlGfdDevice._run_cci_mailbox(), but lives on
the switch side rather than the device side.
"""

import asyncio
from asyncio import create_task, gather
from typing import Optional

from opencis.util.logger import logger
from opencis.util.component import RunnableComponent
from opencis.cxl.component.cci_executor import CciExecutor, CciRequest, CciResponse
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciRequestPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY


class GaeCciMailbox(RunnableComponent):
    """
    Host-to-GAE CCI mailbox.

    Reads CCI requests that the host sends over the USP's cci_fifo channel and
    dispatches them to the shared CciExecutor (which has all GAE commands
    registered).  Responses are written back over the same cci_fifo so the
    host receives them via CxlRootPortDevice.recv_cci().

    Parameters
    ----------
    usp_connection:
        The ``CxlConnection`` for the USP port (port index 0 on the switch).
        We read from  cci_fifo.host_to_target  and write to
        cci_fifo.target_to_host.
    cci_executor:
        Shared ``CciExecutor`` that has the GAE commands registered.
        This is the same executor used by ``MctpCciExecutor`` so that both
        the FM-MCTP path and the host-direct path share state (GaeManager, etc.).
    label:
        Optional logging label.
    """

    def __init__(
        self,
        usp_connection: CxlConnection,
        cci_executor: CciExecutor,
        label: Optional[str] = None,
    ):
        super().__init__(label or "GaeCciMailbox")
        self._cci_fifo = usp_connection.cci_fifo
        self._cci_executor = cci_executor

    # ── Internal mailbox loop ──────────────────────────────────────────────────

    async def _run_mailbox(self) -> None:
        """
        CCI mailbox dispatch loop.

        Reads CciMessagePacket from cci_fifo.host_to_target, dispatches to
        the CciExecutor, writes the response to cci_fifo.target_to_host.

        A sentinel ``None`` on the fifo signals shutdown.
        """
        logger.debug(self._create_message("Mailbox started — waiting for host CCI requests"))
        while True:
            packet = await self._cci_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Sentinel received — stopping mailbox"))
                break

            # ── Normalise to (opcode, tag, payload) ──────────────────────────
            # In-process Queue path: CciMessagePacket (from CxlRootPortDevice.gae_command)
            # Real TCP path: CciRequestPacket (from PacketReader._get_cci_packet)
            try:
                if isinstance(packet, CciMessagePacket):
                    opcode  = packet.cci_msg_header.command_opcode
                    tag     = packet.cci_msg_header.message_tag
                    payload = packet.get_payload()
                elif isinstance(packet, CciRequestPacket):
                    # CciRequestPacket arrives over TCP after PacketReader decodes it
                    opcode  = packet.get_command_opcode()
                    tag     = getattr(packet, "message_tag", 0)
                    payload = packet.get_payload() if hasattr(packet, "get_payload") else b""
                elif hasattr(packet, "get_cci_message"):
                    cci_msg = packet.get_cci_message()
                    opcode  = cci_msg.cci_msg_header.command_opcode
                    tag     = cci_msg.cci_msg_header.message_tag
                    payload = cci_msg.get_payload()
                else:
                    cci_msg = CciMessagePacket(bytearray(bytes(packet)))
                    opcode  = cci_msg.cci_msg_header.command_opcode
                    tag     = cci_msg.cci_msg_header.message_tag
                    payload = cci_msg.get_payload()
            except Exception as exc:
                logger.error(self._create_message(
                    f"Failed to parse incoming CCI packet: {exc}"
                ))
                continue

            logger.debug(self._create_message(
                f"Host→GAE CCI: opcode={opcode:#06x} tag={tag} payload_len={len(payload)}"
            ))

            # ── Dispatch ────────────────────────────────────────────────────────────
            request = CciRequest(opcode=opcode, payload=payload)
            try:
                response: CciResponse = await self._cci_executor.execute_command(request)
            except Exception as exc:
                logger.error(self._create_message(
                    f"CciExecutor raised for opcode={opcode:#06x}: {exc}"
                ))
                response = CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)

            # ── Send response back to host ───────────────────────────────────────────
            # Put a plain CciMessagePacket on the Queue. For in-process tests
            # (no TCP) consumers read this directly. The CxlPacketProcessor(USP)
            # outgoing path wraps it in CciPayloadPacket before writing to TCP.
            resp_msg = CciMessagePacket.create(
                data=response.payload or b"",
                message_category=CCI_MCTP_MESSAGE_CATEGORY.RESPONSE,
                opcode=opcode,
                message_tag=tag,
                return_code=int(response.return_code),
            )
            await self._cci_fifo.target_to_host.put(resp_msg)

            rc_name = (
                response.return_code.name
                if hasattr(response.return_code, "name")
                else str(response.return_code)
            )
            logger.debug(self._create_message(
                f"GAE→Host CCI: opcode={opcode:#06x} tag={tag} rc={rc_name}"
            ))

    # ── RunnableComponent lifecycle ────────────────────────────────────────────

    async def _run(self) -> None:
        logger.info(self._create_message("Starting — host-direct GAE CCI mailbox active"))
        await self._change_status_to_running()
        await self._run_mailbox()
        logger.info(self._create_message("Stopped"))

    async def _stop(self) -> None:
        logger.info(self._create_message("Stopping"))
        await self._cci_fifo.host_to_target.put(None)
