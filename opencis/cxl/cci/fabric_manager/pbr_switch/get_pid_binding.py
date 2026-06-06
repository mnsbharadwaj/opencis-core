"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Get PID Binding — Opcode 5705h
Section 7.7.13.6, CXL Specification Rev 4.0 Version 1.0

Reads the current binding of a Downstream ES PID to an Upstream ES vDSP,
or an Upstream ES USP PID to a Downstream ES vUSP. Also returns HMAT latency
and BW values for generating CDAT information.

Input Payload (Table 7-125):
  Byte 0x00  len=1   Target VCS ID
  Byte 0x01  len=1   Target vPPB index (reserved if binding target is Host ES VCS)

Output Payload (Table 7-126):
  Byte 0x00  len=2   PID (Bits[11:0]); FFFh if unbound
  Byte 0x02  len=2   Reserved
  Byte 0x04  len=8   Latency Entry Base Unit (HMAT)
  Byte 0x0C  len=2   Latency Entry (HMAT)
  Byte 0x0E  len=8   BW Entry Base Unit (HMAT)
  Byte 0x16  len=2   BW Entry (HMAT)
  Total: 0x18 = 24 bytes

Return codes: Success, Unsupported, Invalid Input, Internal Error, Retry Required, Busy
"""

from dataclasses import dataclass
from struct import pack, unpack_from

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse, CciForegroundCommand
from opencis.cxl.component.pbr_switch_manager import PbrSwitchManager, PID_UNASSIGNED
from opencis.util.logger import logger


@dataclass
class GetPidBindingRequestPayload:
    """Request payload for Get PID Binding (Table 7-125).

    Attributes:
        target_vcs: Target VCS ID to query.
        target_vppb: Target vPPB index (reserved if target is Host ES VCS).
    """

    target_vcs: int = 0
    target_vppb: int = 0

    def dump(self) -> bytes:
        """Serialize to the Table 7-125 wire format (2 bytes).

        Wire layout:
            [0x00] Target VCS ID (1 byte)
            [0x01] Target vPPB index (1 byte)

        Returns:
            A 2-byte ``bytes`` object.
        """
        return bytes([self.target_vcs & 0xFF, self.target_vppb & 0xFF])

    @classmethod
    def parse(cls, data: bytes) -> "GetPidBindingRequestPayload":
        """Deserialize a 2-byte Table 7-125 wire payload.

        Args:
            data: Raw bytes (>= 2 bytes).

        Returns:
            A populated ``GetPidBindingRequestPayload``.

        Raises:
            ValueError: If ``data`` is shorter than 2 bytes.
        """
        if len(data) < 2:
            raise ValueError("GetPidBindingRequestPayload: need 2 bytes")
        return cls(target_vcs=data[0], target_vppb=data[1])


@dataclass
class GetPidBindingResponsePayload:
    """Response payload for Get PID Binding (Table 7-126).

    Attributes:
        pid: 12-bit PID of the bound target. FFFh (PID_UNASSIGNED)
            indicates no binding exists.
        latency_entry_base_unit: HMAT latency base unit (8 bytes).
        latency_entry: HMAT latency entry value (2 bytes).
        bw_entry_base_unit: HMAT bandwidth base unit (8 bytes).
        bw_entry: HMAT bandwidth entry value (2 bytes).
    """

    pid: int = PID_UNASSIGNED          # 12-bit; FFFh = unbound
    latency_entry_base_unit: int = 0   # 8 bytes
    latency_entry: int = 0             # 2 bytes
    bw_entry_base_unit: int = 0        # 8 bytes
    bw_entry: int = 0                  # 2 bytes

    PAYLOAD_SIZE = 0x18  # 24 bytes

    def dump(self) -> bytes:
        """Serialize to the Table 7-126 wire format (24 bytes).

        Wire layout (little-endian):
            [0x00..0x01] PID (lower 12 bits, mask 0x0FFF)
            [0x02..0x03] Reserved
            [0x04..0x0B] Latency Entry Base Unit (8 bytes)
            [0x0C..0x0D] Latency Entry (uint16)
            [0x0E..0x15] BW Entry Base Unit (8 bytes)
            [0x16..0x17] BW Entry (uint16)

        Returns:
            A 24-byte ``bytes`` object.
        """
        data = bytearray(self.PAYLOAD_SIZE)
        data[0x00:0x02] = pack("<H", self.pid & 0x0FFF)  # Mask to 12-bit PID
        # 0x02..0x03 reserved
        data[0x04:0x0C] = self.latency_entry_base_unit.to_bytes(8, "little")
        data[0x0C:0x0E] = pack("<H", self.latency_entry)
        data[0x0E:0x16] = self.bw_entry_base_unit.to_bytes(8, "little")
        data[0x16:0x18] = pack("<H", self.bw_entry)
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes) -> "GetPidBindingResponsePayload":
        """Deserialize a 24-byte Table 7-126 wire payload.

        Args:
            data: Raw bytes (>= 24 bytes).

        Returns:
            A populated ``GetPidBindingResponsePayload``.

        Raises:
            ValueError: If ``data`` is shorter than PAYLOAD_SIZE (24).
        """
        if len(data) < cls.PAYLOAD_SIZE:
            raise ValueError(
                f"GetPidBindingResponsePayload: need {cls.PAYLOAD_SIZE} bytes, got {len(data)}"
            )
        pid = unpack_from("<H", data, 0x00)[0] & 0x0FFF
        latency_base = int.from_bytes(data[0x04:0x0C], "little")
        latency_entry = unpack_from("<H", data, 0x0C)[0]
        bw_base = int.from_bytes(data[0x0E:0x16], "little")
        bw_entry = unpack_from("<H", data, 0x16)[0]
        return cls(
            pid=pid,
            latency_entry_base_unit=latency_base,
            latency_entry=latency_entry,
            bw_entry_base_unit=bw_base,
            bw_entry=bw_entry,
        )

    def get_pretty_print(self) -> str:
        """Return a human-readable multiline summary of the PID binding.

        Shows whether the binding exists (PID != FFFh), the PID value,
        and the HMAT latency/bandwidth entries.

        Returns:
            A formatted string.
        """
        bound = self.pid != PID_UNASSIGNED
        return (
            f"- Bound: {bound}\n"
            f"- PID: {self.pid:#05x}\n"
            f"- Latency Base Unit: {self.latency_entry_base_unit}\n"
            f"- Latency Entry:     {self.latency_entry}\n"
            f"- BW Base Unit:      {self.bw_entry_base_unit}\n"
            f"- BW Entry:          {self.bw_entry}"
        )


class GetPidBindingCommand(CciForegroundCommand):
    """
    CCI foreground command for Get PID Binding (Opcode 5705h).

    Returns the current PID binding for a (vcs_id, vppb_id) pair including
    HMAT latency and BW values. Returns FFFh as PID when not bound.
    """

    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING

    def __init__(self, pbr_switch_manager: PbrSwitchManager):
        """Initialize the Get PID Binding command handler.

        Args:
            pbr_switch_manager: Manager that tracks PID bindings
                on the PBR switch.
        """
        super().__init__(self.OPCODE)
        self._pbr_switch_manager = pbr_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        """Execute the Get PID Binding command (Opcode 5705h).

        Reads the (target_vcs, target_vppb) pair from the request and
        queries PbrSwitchManager for the current PID binding. If no
        binding exists, returns PID=FFFh with zeroed HMAT values.

        Args:
            request: CCI request containing the serialized
                GetPidBindingRequestPayload (2 bytes).

        Returns:
            A CciResponse whose payload contains the serialized
            GetPidBindingResponsePayload (24 bytes), or INVALID_INPUT
            on parse error.
        """
        try:
            req_payload = GetPidBindingRequestPayload.parse(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"parse error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        binding = self._pbr_switch_manager.get_pid_binding(
            req_payload.target_vcs, req_payload.target_vppb
        )

        if binding is None:
            # Not yet bound — return PID=FFFh, zeroed HMAT
            resp_payload = GetPidBindingResponsePayload(pid=PID_UNASSIGNED)
        else:
            resp_payload = GetPidBindingResponsePayload(
                pid=binding.pid,
                latency_entry_base_unit=binding.hmat.latency_entry_base_unit,
                latency_entry=binding.hmat.latency_entry,
                bw_entry_base_unit=binding.hmat.bw_entry_base_unit,
                bw_entry=binding.hmat.bw_entry,
            )

        response = CciResponse()
        response.payload = resp_payload.dump()
        return response

    @staticmethod
    def create_cci_request(request: GetPidBindingRequestPayload) -> CciRequest:
        """Build a CCI request for Get PID Binding.

        Args:
            request: Populated request payload to serialize.

        Returns:
            A CciRequest with opcode 5705h and the serialized payload.
        """
        req = CciRequest()
        req.opcode = GetPidBindingCommand.OPCODE
        req.payload = request.dump()
        return req

    @staticmethod
    def parse_response_payload(data: bytes) -> GetPidBindingResponsePayload:
        """Parse raw response bytes into a structured payload.

        Args:
            data: Raw response bytes (>= 24 bytes).

        Returns:
            A populated ``GetPidBindingResponsePayload``.
        """
        return GetPidBindingResponsePayload.parse(data)
