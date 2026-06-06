"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Identify PBR Switch — Opcode 5700h
Section 7.7.13.1, CXL Specification Rev 4.0 Version 1.0

This command provides information to the FM about a PBR switch's fabric
capabilities, including the number of DRT and RGT tables and the dynamic
routing modes supported.

Input Payload : None
Output Payload: Table 7-114
  Byte 0x00  len=8   GAE Support Map (bitmask, bit pos = VCS ID)
  Byte 0x08  len=1   Number of DRTs (must be > 0)
  Byte 0x09  len=1   Number of RGTs
  Byte 0x0A  len=1   Reserved
  Byte 0x0B  len=1   Dynamic Routing Mode Capabilities:
                       Bit 0: Random Supported
                       Bit 1: Congestion Avoidance Supported
                       Bit 2: Advanced CA Supported
                       Bits 5:3: Reserved
                       Bit 6: Vendor Routing Mode 1 Supported
                       Bit 7: Vendor Routing Mode 2 Supported
"""

from dataclasses import dataclass

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse, CciForegroundCommand
from opencis.cxl.component.pbr_switch_manager import PbrSwitchManager


@dataclass
class IdentifyPbrSwitchResponsePayload:
    """Response payload for Identify PBR Switch (Table 7-114).

    Attributes:
        gae_support_map: 8-byte bitmask where each bit position corresponds to
            a VCS ID that has GAE support.
        num_drts: Number of DPID Routing Tables in the switch (must be > 0).
        num_rgts: Number of Routing Group Tables in the switch.
        routing_caps: Dynamic Routing Mode Capabilities bitmask:
            Bit 0 = Random, Bit 1 = Congestion Avoidance, Bit 2 = Advanced CA,
            Bit 6 = Vendor Mode 1, Bit 7 = Vendor Mode 2.
    """

    gae_support_map: int = 0       # 8 bytes
    num_drts: int = 1              # 1 byte
    num_rgts: int = 0              # 1 byte
    routing_caps: int = 0          # 1 byte (dynamic routing mode cap bits)

    PAYLOAD_SIZE = 12  # 0x00..0x0B

    def dump(self) -> bytes:
        """Serialize this payload to its 12-byte wire format.

        Wire layout (little-endian):
            [0x00..0x07] gae_support_map (8 bytes)
            [0x08]       num_drts (1 byte)
            [0x09]       num_rgts (1 byte)
            [0x0A]       Reserved
            [0x0B]       routing_caps (1 byte)

        Returns:
            A 12-byte ``bytes`` object in the Table 7-114 wire format.
        """
        data = bytearray(self.PAYLOAD_SIZE)
        data[0x00:0x08] = self.gae_support_map.to_bytes(8, "little")
        data[0x08] = self.num_drts & 0xFF
        data[0x09] = self.num_rgts & 0xFF
        data[0x0A] = 0  # Reserved
        data[0x0B] = self.routing_caps & 0xFF
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes) -> "IdentifyPbrSwitchResponsePayload":
        """Deserialize a 12-byte wire payload into this dataclass.

        Args:
            data: Raw bytes (>= 12 bytes) in the Table 7-114 wire format.

        Returns:
            A populated ``IdentifyPbrSwitchResponsePayload`` instance.

        Raises:
            ValueError: If ``data`` is shorter than PAYLOAD_SIZE (12 bytes).
        """
        if len(data) < cls.PAYLOAD_SIZE:
            raise ValueError(
                f"IdentifyPbrSwitchResponsePayload requires {cls.PAYLOAD_SIZE} bytes, "
                f"got {len(data)}"
            )
        return cls(
            gae_support_map=int.from_bytes(data[0x00:0x08], "little"),
            num_drts=data[0x08],
            num_rgts=data[0x09],
            routing_caps=data[0x0B],
        )

    def get_pretty_print(self) -> str:
        """Return a human-readable multiline summary of this payload.

        Returns:
            A formatted string listing GAE support map, DRT/RGT counts,
            and individual dynamic routing mode capability bits.
        """
        return (
            f"- GAE Support Map:    {self.gae_support_map:#018x}\n"
            f"- Num DRTs:           {self.num_drts}\n"
            f"- Num RGTs:           {self.num_rgts}\n"
            f"- Routing Caps byte:  {self.routing_caps:#04x}\n"
            f"  Random:             {bool(self.routing_caps & 0x01)}\n"
            f"  Congestion Avoid:   {bool(self.routing_caps & 0x02)}\n"
            f"  Advanced CA:        {bool(self.routing_caps & 0x04)}\n"
            f"  Vendor Mode 1:      {bool(self.routing_caps & 0x40)}\n"
            f"  Vendor Mode 2:      {bool(self.routing_caps & 0x80)}"
        )


class IdentifyPbrSwitchCommand(CciForegroundCommand):
    """
    CCI foreground command for Identify PBR Switch (Opcode 5700h).

    Reads switch capability info from PbrSwitchManager and returns it
    as a structured response payload.
    """

    OPCODE = CCI_FM_API_COMMAND_OPCODE.IDENTIFY_PBR_SWITCH

    def __init__(self, pbr_switch_manager: PbrSwitchManager):
        """Initialize the Identify PBR Switch command handler.

        Args:
            pbr_switch_manager: The PBR switch manager that holds
                switch capability information.
        """
        super().__init__(self.OPCODE)
        self._pbr_switch_manager = pbr_switch_manager

    async def _execute(self, _: CciRequest) -> CciResponse:
        """Execute the Identify PBR Switch command (Opcode 5700h).

        Reads switch identity/capability info from PbrSwitchManager
        and serializes it into the response payload.

        This command has no input payload. The request argument is ignored.

        Args:
            _: The incoming CCI request (unused — no input payload).

        Returns:
            A CciResponse whose payload contains the serialized
            IdentifyPbrSwitchResponsePayload (12 bytes, Table 7-114).
        """
        info = self._pbr_switch_manager.get_identify_info()
        payload = IdentifyPbrSwitchResponsePayload(
            gae_support_map=info.gae_support_map,
            num_drts=info.num_drts,
            num_rgts=info.num_rgts,
            routing_caps=info.routing_caps_byte(),
        )
        response = CciResponse()
        response.payload = payload.dump()
        return response

    @staticmethod
    def create_cci_request() -> CciRequest:
        """Build a CCI request for the Identify PBR Switch command.

        This command requires no input payload.

        Returns:
            A CciRequest with opcode 5700h and an empty payload.
        """
        req = CciRequest()
        req.opcode = IdentifyPbrSwitchCommand.OPCODE
        return req

    @staticmethod
    def parse_response_payload(data: bytes) -> IdentifyPbrSwitchResponsePayload:
        """Parse the raw response bytes into a structured payload.

        Args:
            data: Raw response bytes (>= 12 bytes).

        Returns:
            A populated ``IdentifyPbrSwitchResponsePayload``.
        """
        return IdentifyPbrSwitchResponsePayload.parse(data)
