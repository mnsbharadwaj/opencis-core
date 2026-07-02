"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
import struct
from typing import Optional, List

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencis.cxl.device.config.dynamic_capacity_device import (
    RegionConfiguration,
    RegionConfigStruct,
    DynamicCapacityExtent,
    DynamicCapacityExtentStruct,
)
from opencis.cxl.device.config.logical_device import LogicalDeviceConfig

# pylint: disable=duplicate-code

SIZE_256MB = 256 * 1024 * 1024


#
#  GetHostDCRegionConfiguration command (Opcode 5601h)
#
@dataclass
class GetHostDCRegionConfigRequestPayload:
    host_id: int = 0
    region_count: int = 0
    starting_region_index: int = 0
    pack_mask: str = "<HBB"

    @classmethod
    def parse(cls, data: bytes) -> "GetHostDCRegionConfigRequestPayload":
        if len(data) < struct.calcsize(cls.pack_mask):
            raise ValueError("Data is too short to parse")
        host_id, region_count, starting_region_index = struct.unpack(cls.pack_mask, data[:struct.calcsize(cls.pack_mask)])
        return cls(
            host_id=host_id, region_count=region_count, starting_region_index=starting_region_index
        )

    def dump(self) -> bytes:
        return struct.pack(
            self.pack_mask, self.host_id, self.region_count, self.starting_region_index
        )

    def get_pretty_print(self) -> str:
        return (
            f"- Host ID: {self.host_id}\n"
            f"- Region Count: {self.region_count}\n"
            f"- Starting Region Index: {self.starting_region_index}\n"
        )


@dataclass
class GetHostDCRegionConfigResponsePayload:
    host_id: int = 0
    num_available_regions: int = 0
    regions_returned: int = 0
    dc_region_configs: list[RegionConfiguration] = None
    pack_mask: str = "<HBB"
    region_config_mask: str = "<QQQQIB3s"

    @classmethod
    def parse(cls, data: bytes) -> "GetHostDCRegionConfigResponsePayload":
        base_size = struct.calcsize(cls.pack_mask)
        remaining_len = len(data) - base_size
        region_config_struct_size = struct.calcsize(cls.region_config_mask)
        if remaining_len % region_config_struct_size != 0:
            raise ValueError("Invalid DC Region Config Structures")
        (
            host_id,
            num_available_regions,
            regions_returned,
        ) = struct.unpack(cls.pack_mask, data[:base_size])

        num_configs = remaining_len // region_config_struct_size
        dc_region_configs = []
        offset = base_size
        for _ in range(num_configs):
            (
                region_base,
                region_decode_len,
                region_len,
                region_block_size,
                dsmad_handle,
                flags,
                _,
            ) = struct.unpack(cls.region_config_mask, data[offset : offset + region_config_struct_size])
            dc_region_configs.append(
                RegionConfiguration(
                    region_base,
                    region_decode_len,
                    region_len,
                    region_block_size,
                    dsmad_handle,
                    flags,
                )
            )
            offset += region_config_struct_size

        return cls(
            host_id=host_id,
            num_available_regions=num_available_regions,
            regions_returned=regions_returned,
            dc_region_configs=dc_region_configs,
        )

    def dump(self) -> bytes:
        data = struct.pack(
            self.pack_mask, self.host_id, self.num_available_regions, self.regions_returned
        )
        if self.dc_region_configs:
            for config in self.dc_region_configs:
                data += struct.pack(
                    self.region_config_mask,
                    config.region_base,
                    config.region_decode_len,
                    config.region_len,
                    config.region_block_size,
                    config.dsmad_handle,
                    config.flags,
                    b"\x00" * 3,
                )
        return data

    def get_pretty_print(self) -> str:
        return (
            f"- Host ID: {self.host_id}\n"
            f"- Number of Available Regions: {self.num_available_regions}\n"
            f"- Regions Returned: {self.regions_returned}\n"
        )


class GetHostDCRegionConfiguration(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_HOST_DC_REGION_CONFIGURATION

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        virtual_switch_manager: VirtualSwitchManager,
        device_configs: Optional[List[LogicalDeviceConfig]] = None,
    ):
        self._physical_port_manager = physical_port_manager
        self._virtual_switch_manager = virtual_switch_manager
        self._device_configs = device_configs
        super().__init__(self.OPCODE)

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = GetHostDCRegionConfigRequestPayload.parse(request.payload)
            host_id = request_payload.host_id
        except Exception:
            host_id = 0

        # Build region config options
        total_dynamic_capacity = 0x40000000  # 1 GB default
        if self._device_configs and len(self._device_configs) > 0:
            cfg = self._device_configs[0]
            if hasattr(cfg, "memory_size"):
                total_dynamic_capacity = cfg.memory_size

        config = RegionConfiguration(
            region_base=0,
            region_decode_len=total_dynamic_capacity // SIZE_256MB,
            region_len=total_dynamic_capacity,
            region_block_size=SIZE_256MB,
            dsmad_handle=0,
            flags=0,
        )

        response_payload = GetHostDCRegionConfigResponsePayload(
            host_id=host_id,
            num_available_regions=1,
            regions_returned=1,
            dc_region_configs=[config],
        )
        return self.create_cci_response(response_payload)

    @classmethod
    def create_cci_request(
        cls,
        request: GetHostDCRegionConfigRequestPayload,
    ) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(
        response: GetHostDCRegionConfigResponsePayload,
    ) -> CciResponse:
        cci_response = CciResponse()
        cci_response.payload = response.dump()
        return cci_response


#
#  SetDCRegionConfiguration command (Opcode 5602h)
#
@dataclass
class SetDCRegionConfigRequestPayload:
    region_id: int = 0
    region_block_size: int = 0
    flags: int = 0
    pack_mask: str = "<B3sQB3s"

    @classmethod
    def parse(cls, data: bytes) -> "SetDCRegionConfigRequestPayload":
        base_size = struct.calcsize(cls.pack_mask)
        if len(data) < base_size:
            raise ValueError("Invalid data size for SetDCRegionConfigRequestPayload")

        region_id, _, region_block_size, flags, _ = struct.unpack(cls.pack_mask, data[:base_size])
        return cls(
            region_id=region_id,
            region_block_size=region_block_size,
            flags=flags,
        )

    def dump(self) -> bytes:
        return struct.pack(
            self.pack_mask,
            self.region_id,
            b"\x00" * 3,
            self.region_block_size,
            self.flags,
            b"\x00" * 3,
        )

    def get_pretty_print(self) -> str:
        return (
            f"- Region ID: {self.region_id}\n"
            f"- Region Block Size: {self.region_block_size}\n"
            f"- Flags: {hex(self.flags)}\n"
        )


@dataclass
class SetDCRegionConfigResponsePayload:
    region_id: int = 0
    region_block_size: int = 0
    flags: int = 0
    pack_mask: str = "<B3sQB3s"

    @classmethod
    def parse(cls, data: bytes) -> "SetDCRegionConfigResponsePayload":
        base_size = struct.calcsize(cls.pack_mask)
        if len(data) < base_size:
            raise ValueError("Invalid data size for SetDCRegionConfigResponsePayload")

        region_id, _, region_block_size, flags, _ = struct.unpack(cls.pack_mask, data[:base_size])
        return cls(
            region_id=region_id,
            region_block_size=region_block_size,
            flags=flags,
        )

    def dump(self) -> bytes:
        data = struct.pack(
            self.pack_mask,
            self.region_id,
            b"\x00" * 3,
            self.region_block_size,
            self.flags,
            b"\x00" * 3,
        )
        return data

    def get_pretty_print(self) -> str:
        return (
            f"- Region ID: {self.region_id}\n"
            f"- Region Block Size: {self.region_block_size}\n"
            f"- Flags: {hex(self.flags)}\n"
        )


class SetDCRegionConfiguration(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_DC_REGION_CONFIGURATION

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
            request_payload = SetDCRegionConfigRequestPayload.parse(request.payload)
            region_id = request_payload.region_id
            region_block_size = request_payload.region_block_size
            flags = request_payload.flags
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        response_payload = SetDCRegionConfigResponsePayload(
            region_id=region_id,
            region_block_size=region_block_size,
            flags=flags,
        )
        return self.create_cci_response(response_payload)

    @classmethod
    def create_cci_request(
        cls,
        request: SetDCRegionConfigRequestPayload,
    ) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(
        response: SetDCRegionConfigResponsePayload,
    ) -> CciResponse:
        cci_response = CciResponse()
        cci_response.payload = response.dump()
        return cci_response


#
#  GetDCRegionExtentLists command (Opcode 5603h)
#
@dataclass
class GetDCRegionExtentListsRequestPayload:
    host_id: int = 0
    starting_extent_index: int = 0
    region_block_size: int = 0
    flags: int = 0
    pack_mask: str = "<H2sIII"

    @classmethod
    def parse(cls, data: bytes) -> "GetDCRegionExtentListsRequestPayload":
        base_size = struct.calcsize(cls.pack_mask)
        if len(data) < base_size:
            raise ValueError("Invalid data size for GetDCRegionExtentListsRequestPayload")

        host_id, _, starting_extent_index, region_block_size, flags = struct.unpack(cls.pack_mask, data[:base_size])
        return cls(
            host_id=host_id,
            starting_extent_index=starting_extent_index,
            region_block_size=region_block_size,
            flags=flags,
        )

    def dump(self) -> bytes:
        data = struct.pack(
            self.pack_mask,
            self.host_id,
            b"\x00" * 2,
            self.starting_extent_index,
            self.region_block_size,
            self.flags,
        )
        return data

    def get_pretty_print(self) -> str:
        return (
            f"- Host ID: {self.host_id}\n"
            f"- Starting Extent Index: {self.starting_extent_index}\n"
            f"- Region Block Size: {self.region_block_size}\n"
            f"- Flags: {hex(self.flags)}\n"
        )


@dataclass
class GetDCRegionExtentListsResponsePayload:
    host_id: int = 0
    starting_extent_index: int = 0
    returned_extent_count: int = 0
    total_extent_count: int = 0
    extent_list_gen_num: int = 0
    dc_extents: list[DynamicCapacityExtent] = None
    pack_mask: str = "<H2sIIII4s"
    extent_mask: str = "<QQQQH6s"

    @classmethod
    def parse(cls, data: bytes) -> "GetDCRegionExtentListsResponsePayload":
        base_size = struct.calcsize(cls.pack_mask)
        remaining_len = len(data) - base_size
        dc_extent_struct_size = struct.calcsize(cls.extent_mask)

        if remaining_len % dc_extent_struct_size != 0:
            raise ValueError("Invalid Extent List Structures")

        (
            host_id,
            _,
            starting_extent_index,
            returned_extent_count,
            total_extent_count,
            extent_list_gen_num,
            _,
        ) = struct.unpack(cls.pack_mask, data[:base_size])
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
            starting_extent_index=starting_extent_index,
            returned_extent_count=returned_extent_count,
            total_extent_count=total_extent_count,
            extent_list_gen_num=extent_list_gen_num,
            dc_extents=dc_extents,
        )

    def dump(self) -> bytes:
        data = struct.pack(
            self.pack_mask,
            self.host_id,
            b"\x00" * 2,
            self.starting_extent_index,
            self.returned_extent_count,
            self.total_extent_count,
            self.extent_list_gen_num,
            b"\x00" * 4,
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
            f"- Starting Extent Index: {self.starting_extent_index}\n"
            f"- Returned Extent Count: {self.returned_extent_count}\n"
            f"- Total Extent Count: {self.total_extent_count}\n"
            f"- Extent List Gen Number: {self.extent_list_gen_num}\n"
        )


class GetDCRegionExtentLists(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_DC_REGION_EXTENT_LISTS

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
            request_payload = GetDCRegionExtentListsRequestPayload.parse(request.payload)
            host_id = request_payload.host_id
        except Exception:
            host_id = 0

        response_payload = GetDCRegionExtentListsResponsePayload(
            host_id=host_id,
            starting_extent_index=0,
            returned_extent_count=0,
            total_extent_count=0,
            extent_list_gen_num=1,
            dc_extents=[],
        )
        return self.create_cci_response(response_payload)

    @classmethod
    def create_cci_request(
        cls,
        request: GetDCRegionExtentListsRequestPayload,
    ) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(
        response: GetDCRegionExtentListsResponsePayload,
    ) -> CciResponse:
        cci_response = CciResponse()
        cci_response.payload = response.dump()
        return cci_response
