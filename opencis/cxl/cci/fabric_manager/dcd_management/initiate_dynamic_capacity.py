"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
import struct

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencis.cxl.device.config.dynamic_capacity_device import DynamicCapacityExtent


#
#   InitiateDynamicCapacityAdd command (Opcode 5604h)
#
@dataclass
class InitiateDynamicCapacityAddRequestPayload:
    host_id: int = 0
    selection_policy: int = 0
    region_num: int = 0
    length: int = 0
    tag: int = 0
    ext_count: int = 0
    pack_mask: str = "<HBBQ16sI"
    extent_mask: str = "<QQQQH6s"
    dc_extents: list[DynamicCapacityExtent] = None

    @classmethod
    def parse(cls, data: bytes) -> "InitiateDynamicCapacityAddRequestPayload":
        base_size = struct.calcsize(cls.pack_mask)
        remaining_len = len(data) - base_size
        dc_extent_struct_size = struct.calcsize(cls.extent_mask)
        if remaining_len % dc_extent_struct_size != 0:
            raise ValueError("Invalid Extent List Structures")
        (
            host_id,
            selection_policy,
            region_num,
            length,
            tag_bytes,
            ext_count,
        ) = struct.unpack(cls.pack_mask, data[:base_size])
        tag = int.from_bytes(tag_bytes, "little")

        num_extents = remaining_len // dc_extent_struct_size
        dc_extents = []
        offset = base_size
        for _ in range(num_extents):
            (
                start_dpa,
                extent_length,
                tag_upper,
                tag_lower,
                shared_extent_seq,
                _,
            ) = struct.unpack(cls.extent_mask, data[offset : offset + dc_extent_struct_size])
            extent_tag = tag_upper << 64 | tag_lower
            dc_extents.append(DynamicCapacityExtent(start_dpa, extent_length, extent_tag, shared_extent_seq))
            offset += dc_extent_struct_size

        return cls(
            host_id=host_id,
            selection_policy=selection_policy,
            region_num=region_num,
            length=length,
            tag=tag,
            ext_count=ext_count,
            dc_extents=dc_extents,
        )

    def dump(self) -> bytes:
        tag_bytes = self.tag.to_bytes(16, "little")
        data = struct.pack(
            self.pack_mask,
            self.host_id,
            self.selection_policy,
            self.region_num,
            self.length,
            tag_bytes,
            self.ext_count,
        )
        if self.dc_extents:
            for extent in self.dc_extents:
                data += struct.pack(
                    self.extent_mask,
                    extent.start_dpa,
                    extent.length,
                    (extent.tag >> 64) & 0xFFFFFFFFFFFFFFFF,
                    extent.tag & 0xFFFFFFFFFFFFFFFF,
                    extent.shared_extent_seq,
                    b"\x00" * 6,
                )
        return data

    def get_pretty_print(self) -> str:
        return (
            f"- Host ID: {self.host_id}\n"
            f"- Selection Policy: {self.selection_policy}\n"
            f"- Region Number: {self.region_num}\n"
            f"- Length: {self.length}\n"
            f"- Tag: {self.tag}\n"
            f"- Extension Count: {self.ext_count}"
        )


class InitiateDynamicCapacityAdd(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.INITIATE_DYNAMIC_CAPACITY_ADD

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        virtual_switch_manager: VirtualSwitchManager,
    ):
        self._physical_port_manager = physical_port_manager
        self._virtual_switch_manager = virtual_switch_manager
        super().__init__(self.OPCODE)

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = InitiateDynamicCapacityAddRequestPayload.parse(request.payload)
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        return self.create_cci_response(CCI_RETURN_CODE.SUCCESS)

    @classmethod
    def create_cci_request(
        cls,
        request: InitiateDynamicCapacityAddRequestPayload,
    ) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(
        ret: CCI_RETURN_CODE,
    ) -> CciResponse:
        cci_response = CciResponse()
        cci_response.return_code = ret
        return cci_response


#
#   InitiateDynamicCapacityRelease command (Opcode 5605h)
#
@dataclass
class InitiateDynamicCapacityReleaseRequestPayload:
    host_id: int = 0
    flags: int = 0
    reserved0: int = 0
    extent_count: int = 0
    tag: int = 0
    dc_extents: list[DynamicCapacityExtent] = None
    pack_mask: str = "<HHHI16s"
    extent_mask: str = "<QQQQH6s"

    @classmethod
    def parse(cls, data: bytes) -> "InitiateDynamicCapacityReleaseRequestPayload":
        base_size = struct.calcsize(cls.pack_mask)
        remaining_len = len(data) - base_size
        dc_extent_struct_size = struct.calcsize(cls.extent_mask)

        if remaining_len % dc_extent_struct_size != 0:
            raise ValueError("Invalid Extent List Structures")

        (
            host_id,
            flags,
            reserved0,
            extent_count,
            tag_bytes,
        ) = struct.unpack(cls.pack_mask, data[:base_size])
        tag = int.from_bytes(tag_bytes, "little")

        num_extents = remaining_len // dc_extent_struct_size
        dc_extents = []
        offset = base_size
        for _ in range(num_extents):
            (
                start_dpa,
                extent_length,
                extent_tag_upper,
                extent_tag_lower,
                shared_extent_seq,
                _,
            ) = struct.unpack(cls.extent_mask, data[offset : offset + dc_extent_struct_size])
            extent_tag = extent_tag_upper << 64 | extent_tag_lower
            dc_extents.append(
                DynamicCapacityExtent(start_dpa, extent_length, extent_tag, shared_extent_seq)
            )
            offset += dc_extent_struct_size

        return cls(
            host_id=host_id,
            flags=flags,
            reserved0=reserved0,
            extent_count=extent_count,
            tag=tag,
            dc_extents=dc_extents,
        )

    def dump(self) -> bytes:
        tag_bytes = self.tag.to_bytes(16, "little")
        data = struct.pack(
            self.pack_mask,
            self.host_id,
            self.flags,
            self.reserved0,
            self.extent_count,
            tag_bytes,
        )

        if self.dc_extents:
            for extent in self.dc_extents:
                data += struct.pack(
                    self.extent_mask,
                    extent.start_dpa,
                    extent.length,
                    (extent.tag >> 64) & 0xFFFFFFFFFFFFFFFF,
                    extent.tag & 0xFFFFFFFFFFFFFFFF,
                    extent.shared_extent_seq,
                    b"\x00" * 6,
                )

        return data

    def get_pretty_print(self) -> str:
        return (
            f"- Host ID: {self.host_id}\n"
            f"- Flags: {self.flags}\n"
            f"- Reserved0: {self.reserved0}\n"
            f"- Extent Count: {self.extent_count}"
        )


class InitiateDynamicCapacityRelease(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.INITIATE_DYNAMIC_CAPACITY_RELEASE

    def __init__(
        self,
        physical_port_manager,
        virtual_switch_manager,
    ):
        self._physical_port_manager = physical_port_manager
        self._virtual_switch_manager = virtual_switch_manager
        super().__init__(self.OPCODE)

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = InitiateDynamicCapacityReleaseRequestPayload.parse(request.payload)
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        return self.create_cci_response(CCI_RETURN_CODE.SUCCESS)

    @classmethod
    def create_cci_request(
        cls,
        request: InitiateDynamicCapacityReleaseRequestPayload,
    ) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(
        ret: CCI_RETURN_CODE,
    ) -> CciResponse:
        cci_response = CciResponse()
        cci_response.return_code = ret
        return cci_response
