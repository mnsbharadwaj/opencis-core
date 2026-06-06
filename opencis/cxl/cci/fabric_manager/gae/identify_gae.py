"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Identify GAE — Opcode 5800h
Section 7.7.14.1, CXL Specification Rev 4.0 Version 1.0

Reports the GAE's capabilities to the FM.  For a simple-device GFD
(no G-FAM, no FAST decoders) the vPPB list is empty and all support
flags are zero.

Input Payload (Table 7-158):
  None

Output Payload (Table 7-159):
  Byte 0x00  len=2   Number of vPPBs with Global Memory Support
  Byte 0x02  len=2   Reserved
  Byte 0x04  varies  vPPB Global Memory Support Info list (Table 7-160)

vPPB Global Memory Support Info (Table 7-160) — 4 bytes each:
  Byte 0x00  Bits[7:0]  vPPB ID
  Byte 0x01  Bit[0]     Global Memory Support (1 = supports G-FAM)
             Bits[7:1]  Reserved
  Byte 0x02  len=2      Reserved

Return codes: Success, Unsupported, Invalid Input
"""

from dataclasses import dataclass, field
from struct import pack, unpack_from
from typing import List

from opencis.cxl.cci.common import CCI_GAE_COMMAND_OPCODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse, CciForegroundCommand
from opencis.cxl.component.gae_manager import GaeManager, GaeVppbInfo


# ---------------------------------------------------------------------------
# Wire-format structures
# ---------------------------------------------------------------------------


@dataclass
class VppbGlobalMemorySupportInfo:
    """
    One entry in the vPPB Global Memory Support Info list (Table 7-160).
    Wire size: 4 bytes.
    """
    vppb_id: int = 0
    global_memory_support: bool = False

    ENTRY_SIZE = 4

    def dump(self) -> bytes:
        """Serialize to 4-byte wire format (Table 7-160).

        Wire layout:
            [0x00] vPPB ID (1 byte)
            [0x01] Bit 0 = Global Memory Support flag
            [0x02..0x03] Reserved

        Returns:
            A 4-byte ``bytes`` object.
        """
        data = bytearray(self.ENTRY_SIZE)
        data[0] = self.vppb_id & 0xFF
        data[1] = 0x01 if self.global_memory_support else 0x00
        # bytes 2-3: Reserved
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes, offset: int = 0) -> "VppbGlobalMemorySupportInfo":
        """Deserialize a 4-byte entry from the buffer.

        Args:
            data: Buffer containing the serialized entry.
            offset: Byte offset into ``data`` where this entry starts.

        Returns:
            A populated ``VppbGlobalMemorySupportInfo``.
        """
        vppb_id = data[offset]
        global_memory_support = bool(data[offset + 1] & 0x01)  # Bit 0 only
        return cls(vppb_id=vppb_id, global_memory_support=global_memory_support)


@dataclass
class IdentifyGaeResponsePayload:
    """
    Identify GAE Response Payload (Table 7-159).

    For a simple-device GFD the vppb_entries list is empty and
    num_vppbs_with_gm_support is 0.
    """
    num_vppbs_with_gm_support: int = 0
    vppb_entries: List[VppbGlobalMemorySupportInfo] = field(default_factory=list)

    HEADER_SIZE = 4

    def dump(self) -> bytes:
        """Serialize to the Table 7-159 wire format.

        Wire layout (little-endian):
            [0x00..0x01] Number of vPPBs with Global Memory Support (uint16)
            [0x02..0x03] Reserved
            [0x04..]     vPPB entries (4 bytes each, Table 7-160)

        Returns:
            Variable-length ``bytes`` (4 + 4 × len(vppb_entries)).
        """
        header = bytearray(self.HEADER_SIZE)
        header[0x00:0x02] = pack("<H", len(self.vppb_entries))
        # 0x02-0x03 reserved
        entry_bytes = b"".join(e.dump() for e in self.vppb_entries)
        return bytes(header) + entry_bytes

    @classmethod
    def parse(cls, data: bytes) -> "IdentifyGaeResponsePayload":
        """Deserialize a Table 7-159 wire payload.

        Args:
            data: Raw bytes (>= 4-byte header + 4 × N entry bytes).

        Returns:
            A populated ``IdentifyGaeResponsePayload``.

        Raises:
            ValueError: If the buffer is too short or entries are truncated.
        """
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(f"IdentifyGaeResponsePayload: need {cls.HEADER_SIZE} bytes")
        num = unpack_from("<H", data, 0x00)[0]
        entries = []
        offset = cls.HEADER_SIZE
        for _ in range(num):
            if offset + VppbGlobalMemorySupportInfo.ENTRY_SIZE > len(data):
                raise ValueError("IdentifyGaeResponsePayload: truncated entry list")
            entries.append(VppbGlobalMemorySupportInfo.parse(data, offset))
            offset += VppbGlobalMemorySupportInfo.ENTRY_SIZE
        return cls(num_vppbs_with_gm_support=num, vppb_entries=entries)

    def get_pretty_print(self) -> str:
        """Return a human-readable multiline summary.

        Lists the count of vPPBs with G-FAM support and each entry's
        vPPB ID and support flag.

        Returns:
            A formatted string.
        """
        lines = [f"- Num vPPBs with G-FAM: {self.num_vppbs_with_gm_support}"]
        for e in self.vppb_entries:
            lines.append(
                f"  vPPB {e.vppb_id}: G-FAM={e.global_memory_support}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------


class IdentifyGaeCommand(CciForegroundCommand):
    """
    CCI foreground command for Identify GAE (Opcode 5800h).

    For a simple-device GFD the GaeManager reports zero vPPBs with
    G-FAM support, so the response is a 4-byte header with count=0.
    """

    OPCODE = CCI_GAE_COMMAND_OPCODE.IDENTIFY_GAE

    def __init__(self, gae_manager: GaeManager):
        """Initialize the Identify GAE command handler.

        Args:
            gae_manager: GAE manager that reports vPPB capabilities.
        """
        super().__init__(self.OPCODE)
        self._gae_manager = gae_manager

    async def _execute(self, _: CciRequest) -> CciResponse:
        """Execute the Identify GAE command (Opcode 5800h).

        Queries GaeManager.get_vppbs() for the list of vPPBs and
        their Global Memory (G-FAM) support flags, then serializes
        the result into the response payload.

        This command has no input payload; the request is ignored.

        Args:
            _: The incoming CCI request (unused — no input payload).

        Returns:
            A CciResponse whose payload contains the serialized
            IdentifyGaeResponsePayload (Table 7-159).
        """
        vppbs = self._gae_manager.get_vppbs()
        entries = [
            VppbGlobalMemorySupportInfo(
                vppb_id=v.vppb_id,
                global_memory_support=v.global_memory_support,
            )
            for v in vppbs
        ]
        payload = IdentifyGaeResponsePayload(
            num_vppbs_with_gm_support=len(entries),
            vppb_entries=entries,
        )
        response = CciResponse()
        response.payload = payload.dump()
        return response

    @staticmethod
    def create_cci_request() -> CciRequest:
        """Build a CCI request for Identify GAE.

        This command requires no input payload.

        Returns:
            A CciRequest with opcode 5800h and an empty payload.
        """
        req = CciRequest()
        req.opcode = IdentifyGaeCommand.OPCODE
        return req

    @staticmethod
    def parse_response_payload(data: bytes) -> IdentifyGaeResponsePayload:
        """Parse raw response bytes into a structured payload.

        Args:
            data: Raw response bytes (>= 4 bytes).

        Returns:
            A populated ``IdentifyGaeResponsePayload``.
        """
        return IdentifyGaeResponsePayload.parse(data)
