"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Proxy GFD Management Command — Opcode 5809h
Section 7.7.14.10, CXL Specification Rev 4.0 Version 1.0

This is the key mechanism for a host to issue CCI commands to the GFD.
Because the GFD is attached to a PBR-switch DSP (not directly to a host),
the host cannot reach the GFD's CCI mailbox.  Instead the host sends this
command to the GAE; the GAE starts an async proxy thread that forwards the
embedded CCI command to the GFD's CciExecutor and collects the response.

The FM/host then polls Get Proxy Thread Status (580Ah) or cancels via
Cancel Proxy Thread (580Bh).

Command flow
------------
1. Host → GAE:  Proxy GFD Mgmt Cmd(thread_id=0, gfd_opcode, gfd_payload)
2. GAE starts asyncio task → calls gfd_executor.execute_command()
3. GAE returns: {thread_id=N, bo_flag=True} (background command started)
4. Host → GAE:  Get Proxy Thread Status(thread_id=N)
5. GAE returns status (completed/in-progress, gfd return code)
6. When completed, host reads the GFD response from the status payload.

Input Payload (Table 7-167 equivalent):
  Byte 0x00  len=2   GFD Command Opcode (the CCI opcode to forward)
  Byte 0x02  len=2   GFD Command Payload Length
  Byte 0x04  varies  GFD Command Payload (the payload for the forwarded CCI cmd)

Output Payload:
  Byte 0x00  len=2   Proxy Thread ID (assigned by GAE, used in subsequent calls)

Return codes: Success, Unsupported, Invalid Input, Internal Error
"""

from dataclasses import dataclass
from struct import pack, unpack_from

from opencis.cxl.cci.common import CCI_GAE_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse, CciForegroundCommand
from opencis.cxl.component.gae_manager import GaeManager
from opencis.util.logger import logger


# ---------------------------------------------------------------------------
# Wire-format structures
# ---------------------------------------------------------------------------


@dataclass
class ProxyGfdMgmtRequestPayload:
    """Input payload for Proxy GFD Management Command."""
    gfd_opcode: int = 0
    gfd_payload: bytes = b""

    def dump(self) -> bytes:
        """Serialize to the Proxy GFD Management request wire format.

        Wire layout (little-endian):
            [0x00..0x01] GFD Command Opcode (uint16)
            [0x02..0x03] GFD Command Payload Length (uint16)
            [0x04..]     GFD Command Payload (variable-length)

        Returns:
            Variable-length ``bytes`` (4 + len(gfd_payload)).
        """
        header = bytearray(4)
        header[0:2] = pack("<H", self.gfd_opcode)
        header[2:4] = pack("<H", len(self.gfd_payload))
        return bytes(header) + self.gfd_payload

    @classmethod
    def parse(cls, data: bytes) -> "ProxyGfdMgmtRequestPayload":
        """Deserialize raw bytes into a ProxyGfdMgmtRequestPayload.

        Args:
            data: Raw bytes (>= 4-byte header + variable payload).

        Returns:
            A populated ``ProxyGfdMgmtRequestPayload``.

        Raises:
            ValueError: If ``data`` is shorter than 4 bytes.
        """
        if len(data) < 4:
            raise ValueError("ProxyGfdMgmtRequestPayload: need at least 4 bytes")
        gfd_opcode = unpack_from("<H", data, 0)[0]
        gfd_payload_len = unpack_from("<H", data, 2)[0]
        gfd_payload = bytes(data[4: 4 + gfd_payload_len])
        return cls(gfd_opcode=gfd_opcode, gfd_payload=gfd_payload)


@dataclass
class ProxyGfdMgmtResponsePayload:
    """Output payload for Proxy GFD Management Command."""
    thread_id: int = 0

    PAYLOAD_SIZE = 2

    def dump(self) -> bytes:
        """Serialize to the Proxy GFD Management response wire format.

        Returns:
            A 2-byte ``bytes`` object containing the thread_id as
            a little-endian uint16.
        """
        return pack("<H", self.thread_id)

    @classmethod
    def parse(cls, data: bytes) -> "ProxyGfdMgmtResponsePayload":
        """Deserialize raw bytes into a ProxyGfdMgmtResponsePayload.

        Args:
            data: Raw bytes (>= 2 bytes).

        Returns:
            A populated ``ProxyGfdMgmtResponsePayload``.

        Raises:
            ValueError: If ``data`` is shorter than PAYLOAD_SIZE (2).
        """
        if len(data) < cls.PAYLOAD_SIZE:
            raise ValueError("ProxyGfdMgmtResponsePayload: need 2 bytes")
        return cls(thread_id=unpack_from("<H", data, 0)[0])

    def get_pretty_print(self) -> str:
        """Return a human-readable summary of this response payload.

        Returns:
            A formatted string showing the proxy thread ID.
        """
        return f"- Proxy Thread ID: {self.thread_id}"


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------


class ProxyGfdMgmtCommand(CciForegroundCommand):
    """
    CCI foreground command for Proxy GFD Management Command (Opcode 5809h).

    Starts an asyncio proxy task that forwards the embedded CCI command to
    the GFD's CciExecutor.  Returns a thread_id the caller uses to poll
    for completion via Get Proxy Thread Status (580Ah).
    """

    OPCODE = CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD

    def __init__(self, gae_manager: GaeManager):
        """Initialize the Proxy GFD Management command handler.

        Args:
            gae_manager: GAE manager that maintains the proxy thread
                pool and GFD tunnel/executor bindings.
        """
        super().__init__(self.OPCODE)
        self._gae_manager = gae_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        """Execute the Proxy GFD Management Command (Opcode 5809h).

        Parses the embedded GFD opcode and payload from the request,
        verifies a GFD binding (tunnel or executor) exists, then starts
        an async proxy task via GaeManager.start_proxy(). Returns the
        assigned thread_id for subsequent polling.

        Args:
            request: CCI request containing the serialized
                ProxyGfdMgmtRequestPayload.

        Returns:
            A CciResponse whose payload contains a 2-byte thread_id
            on success, INVALID_INPUT if the payload is malformed,
            or INTERNAL_ERROR if no GFD binding or proxy start fails.
        """
        if not request.payload or len(request.payload) < 4:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        try:
            req_payload = ProxyGfdMgmtRequestPayload.parse(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"parse error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # Check for either tunnel (production) or executor (test mode)
        if not self._gae_manager.has_gfd_binding():
            logger.error(self._create_message(
                "no GFD tunnel or executor bound — cannot proxy. "
                "Call GaeManager.set_gfd_tunnel() (production) or "
                "GaeManager.set_gfd_executor() (tests) first."
            ))
            return CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)

        thread_id = await self._gae_manager.start_proxy(
            gfd_opcode=req_payload.gfd_opcode,
            gfd_request_payload=req_payload.gfd_payload,
        )

        if thread_id == 0:
            return CciResponse(return_code=CCI_RETURN_CODE.INTERNAL_ERROR)

        resp_payload = ProxyGfdMgmtResponsePayload(thread_id=thread_id)
        response = CciResponse()
        response.payload = resp_payload.dump()
        return response

    @staticmethod
    def create_cci_request(
        gfd_opcode: int, gfd_payload: bytes = b""
    ) -> CciRequest:
        """Build a CCI request for Proxy GFD Management Command.

        Args:
            gfd_opcode: The CCI opcode to forward to the GFD.
            gfd_payload: Optional payload for the forwarded CCI command.

        Returns:
            A CciRequest with opcode 5809h and the serialized payload.
        """
        req = CciRequest()
        req.opcode = ProxyGfdMgmtCommand.OPCODE
        req.payload = ProxyGfdMgmtRequestPayload(
            gfd_opcode=gfd_opcode, gfd_payload=gfd_payload
        ).dump()
        return req

    @staticmethod
    def parse_response_payload(data: bytes) -> ProxyGfdMgmtResponsePayload:
        """Parse raw response bytes into a structured payload.

        Args:
            data: Raw response bytes (>= 2 bytes).

        Returns:
            A populated ``ProxyGfdMgmtResponsePayload``.
        """
        return ProxyGfdMgmtResponsePayload.parse(data)
