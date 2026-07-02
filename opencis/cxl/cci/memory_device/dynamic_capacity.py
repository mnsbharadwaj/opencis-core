"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from opencis.cxl.features.mailbox import (
    CxlMailboxContext,
    CxlMailboxCommandBase,
    MAILBOX_RETURN_CODE,
)
from opencis.util.unaligned_bit_structure import UnalignedBitStructure, ByteField, StructureField
from opencis.cxl.device.config.dynamic_capacity_device import (
    RegionConfiguration,
    RegionConfigStruct,
    DynamicCapacityExtentStruct,
)

#
#   GetDynamicCapacityConfig command (Opcode 4800h)
#
class GetDynamicCapacityConfigInput(UnalignedBitStructure):
    region_count: int
    starting_region_index: int

    _fields = [
        ByteField("region_count", 0x00, 0x00),
        ByteField("starting_region_index", 0x01, 0x01),
    ]


class GetDynamicCapacityConfigOutput(UnalignedBitStructure):
    num_available_regions: int
    regions_returned: int
    reserved: int
    region_config0: RegionConfigStruct
    region_config1: RegionConfigStruct
    region_config2: RegionConfigStruct
    region_config3: RegionConfigStruct
    region_config4: RegionConfigStruct
    region_config5: RegionConfigStruct
    region_config6: RegionConfigStruct
    region_config7: RegionConfigStruct
    total_supported_extents: int
    num_available_extents: int
    total_supported_tags: int
    num_available_tags: int

    def __init__(self, region_configs: list[RegionConfigStruct], num_available_regions: int = None):
        self._fields = [
            ByteField("num_available_regions", 0x00, 0x00),
            ByteField("regions_returned", 0x01, 0x01),
            ByteField("reserved", 0x02, 0x07),
        ]
        start_offset = 0x08
        entry_size = RegionConfigStruct.get_size()
        entry_index = 0
        for _ in region_configs:
            end_offset = start_offset + entry_size - 1
            self._fields.append(
                StructureField(
                    f"region_config{entry_index}",
                    start_offset,
                    end_offset,
                    RegionConfigStruct,
                )
            )
            entry_index += 1
            start_offset += entry_size
        self._fields.append(ByteField("total_supported_extents", start_offset, start_offset + 3))
        self._fields.append(ByteField("num_available_extents", start_offset + 4, start_offset + 7))
        self._fields.append(ByteField("total_supported_tags", start_offset + 8, start_offset + 11))
        self._fields.append(ByteField("num_available_tags", start_offset + 12, start_offset + 15))

        super().__init__()
        self.num_available_regions = num_available_regions if num_available_regions is not None else len(region_configs)
        self.regions_returned = len(region_configs)
        for i, config in enumerate(region_configs):
            target_region = getattr(self, f"region_config{i}")
            target_region.region_base = config.region_base
            target_region.region_decode_len = config.region_decode_len
            target_region.region_len = config.region_len
            target_region.region_block_size = config.region_block_size
            
            # Set sub-structures
            target_region.dsmad_handle.nonvolatile = config.dsmad_handle.nonvolatile
            target_region.dsmad_handle.sharable = config.dsmad_handle.sharable
            target_region.dsmad_handle.hw_managed_coherency = config.dsmad_handle.hw_managed_coherency
            target_region.dsmad_handle.interconnect_specific_dc_mgmt = config.dsmad_handle.interconnect_specific_dc_mgmt
            target_region.dsmad_handle.read_only = config.dsmad_handle.read_only
            
            target_region.flags.sanitize_on_release = config.flags.sanitize_on_release

        self.total_supported_extents = 512
        self.num_available_extents = 512
        self.total_supported_tags = 0
        self.num_available_tags = 0

    @staticmethod
    def get_size(region_config_structs: list[RegionConfigStruct]):
        # pylint: disable=arguments-renamed
        return 8 + RegionConfigStruct.get_size() * len(region_config_structs) + 16


class GetDynamicCapacityConfig(CxlMailboxCommandBase):
    def __init__(self, region_config_structs: list[RegionConfigStruct]):
        super().__init__(0x4800)
        self._region_config_structs = region_config_structs

    def process(self, context: CxlMailboxContext) -> bool:
        payload_length = context.command["payload_length"]
        if payload_length != GetDynamicCapacityConfigInput.get_size():
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        input_buffer = context.payloads.create_shared(payload_length)
        input = GetDynamicCapacityConfigInput(input_buffer)

        start = input.starting_region_index
        count = input.region_count
        total_regions = len(self._region_config_structs)

        if total_regions == 0:
            if start != 0:
                context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
                return True
            returned_regions = []
        else:
            if start >= total_regions:
                context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
                return True
            if start + count > total_regions:
                count = total_regions - start
            returned_regions = self._region_config_structs[start : start + count]

        output_bytes = bytes(GetDynamicCapacityConfigOutput(returned_regions, total_regions))
        context.payloads.copy_from(output_bytes)
        context.command["payload_length"] = len(output_bytes)
        return True


#
#   GetDynamicCapacityExtentList command (Opcode 4801h)
#
class GetDynamicCapacityExtentListInput(UnalignedBitStructure):
    extent_count: int
    starting_extent_index: int

    _fields = [
        ByteField("extent_count", 0x00, 0x03),
        ByteField("starting_extent_index", 0x04, 0x07),
    ]


class GetDynamicCapacityExtentListOutput(UnalignedBitStructure):
    returned_extent_count: int
    total_extent_count: int
    extent_list_generation_number: int
    reserved: int
    dc_extent0: DynamicCapacityExtentStruct
    dc_extent1: DynamicCapacityExtentStruct
    dc_extent2: DynamicCapacityExtentStruct
    dc_extent3: DynamicCapacityExtentStruct
    dc_extent4: DynamicCapacityExtentStruct
    dc_extent5: DynamicCapacityExtentStruct
    dc_extent6: DynamicCapacityExtentStruct
    dc_extent7: DynamicCapacityExtentStruct

    def __init__(self, dc_extent_list: list[DynamicCapacityExtentStruct], total_extent_count: int = None):
        self._fields = [
            ByteField("returned_extent_count", 0x00, 0x03),
            ByteField("total_extent_count", 0x04, 0x07),
            ByteField("extent_list_generation_number", 0x08, 0x0B),
            ByteField("reserved", 0x0C, 0x0F),
        ]
        start_offset = 0x10
        entry_size = DynamicCapacityExtentStruct.get_size()
        entry_index = 0
        for _ in dc_extent_list:
            end_offset = start_offset + entry_size - 1
            self._fields.append(
                StructureField(
                    f"dc_extent{entry_index}",
                    start_offset,
                    end_offset,
                    DynamicCapacityExtentStruct,
                )
            )
            entry_index += 1
            start_offset += entry_size

        super().__init__()
        self.returned_extent_count = len(dc_extent_list)
        self.total_extent_count = total_extent_count if total_extent_count is not None else len(dc_extent_list)
        self.extent_list_generation_number = 0

        for i, ext in enumerate(dc_extent_list):
            target_extent = getattr(self, f"dc_extent{i}")
            target_extent.start_dpa = ext.start_dpa
            target_extent.length = ext.length
            target_extent.tag = ext.tag
            target_extent.shared_extent_seq = ext.shared_extent_seq

    @staticmethod
    def get_size(dc_extent_list: list[DynamicCapacityExtentStruct]):
        # pylint: disable=arguments-renamed
        return 0x10 + DynamicCapacityExtentStruct.get_size() * len(dc_extent_list)


class GetDynamicCapacityExtentList(CxlMailboxCommandBase):
    def __init__(self, dc_extent_list: list[DynamicCapacityExtentStruct]):
        super().__init__(0x4801)
        self._dc_extent_list = dc_extent_list

    def process(self, context: CxlMailboxContext) -> bool:
        payload_length = context.command["payload_length"]
        if payload_length != GetDynamicCapacityExtentListInput.get_size():
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        input_buffer = context.payloads.create_shared(payload_length)
        input = GetDynamicCapacityExtentListInput(input_buffer)

        start = input.starting_extent_index
        count = input.extent_count
        total_extents = len(self._dc_extent_list)

        if total_extents == 0:
            if start != 0:
                context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
                return True
            returned_extents = []
        else:
            if start >= total_extents:
                context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
                return True
            if start + count > total_extents:
                count = total_extents - start
            returned_extents = self._dc_extent_list[start : start + count]

        output_bytes = bytes(GetDynamicCapacityExtentListOutput(returned_extents, total_extents))
        context.payloads.copy_from(output_bytes)
        context.command["payload_length"] = len(output_bytes)
        return True


#
#   AddDynamicCapacityResponse command (Opcode 4802h)
#

UPDATED_EXT_STRUCT_SIZE = 0x18


class UpdatedExtentStruct(UnalignedBitStructure):
    starting_dpa: int
    length: int
    reserved: int

    _fields = [
        ByteField("starting_dpa", 0x00, 0x07),
        ByteField("length", 0x08, 0x0F),
        ByteField("reserved", 0x10, 0x17),
    ]


class AddDynamicCapacityResponseInput(UnalignedBitStructure):
    updated_extent_list_size: int
    flags: int
    reserved: int
    updated_extent0: UpdatedExtentStruct
    updated_extent1: UpdatedExtentStruct
    updated_extent2: UpdatedExtentStruct
    updated_extent3: UpdatedExtentStruct
    updated_extent4: UpdatedExtentStruct
    updated_extent5: UpdatedExtentStruct
    updated_extent6: UpdatedExtentStruct
    updated_extent7: UpdatedExtentStruct

    def __init__(self, updated_ext_list: list[UpdatedExtentStruct]):
        self._fields = [
            ByteField("updated_extent_list_size", 0x00, 0x03),
            ByteField("flags", 0x04, 0x05),
            ByteField("reserved", 0x06, 0x07),
        ]
        start_offset = 0x08
        entry_size = UpdatedExtentStruct.get_size()
        entry_index = 0
        for _ in updated_ext_list:
            end_offset = start_offset + entry_size - 1
            self._fields.append(
                StructureField(
                    f"updated_extent{entry_index}",
                    start_offset,
                    end_offset,
                    UpdatedExtentStruct,
                )
            )
            entry_index += 1
            start_offset += entry_size
        super().__init__()
        
        # Copy the extent values into the structured fields
        for i, ext in enumerate(updated_ext_list):
            target = getattr(self, f"updated_extent{i}")
            target.starting_dpa = ext.starting_dpa
            target.length = ext.length
            target.reserved = ext.reserved

    @staticmethod
    def get_size(updated_extent_list: list[UpdatedExtentStruct]):
        # pylint: disable=arguments-renamed
        return 0x8 + UPDATED_EXT_STRUCT_SIZE * len(updated_extent_list)

    @classmethod
    def parse(cls, data: bytes) -> "AddDynamicCapacityResponseInput":
        extent_list_size = int.from_bytes(data[0:4], "little")
        dummy_list = [UpdatedExtentStruct() for _ in range(extent_list_size)]
        obj = cls(dummy_list)
        obj.reset(data)
        return obj


class AddDynamicCapacityResponse(CxlMailboxCommandBase):
    def __init__(self, dc_extent_list: list[DynamicCapacityExtentStruct]):
        super().__init__(0x4802)
        self._dc_extent_list = dc_extent_list

    def process(self, context: CxlMailboxContext) -> bool:
        payload_length = context.command["payload_length"]
        if payload_length < 8:
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        input_data = bytes(context.payloads.create_shared(payload_length))
        try:
            input_payload = AddDynamicCapacityResponseInput.parse(input_data)
        except Exception:
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        for i in range(input_payload.updated_extent_list_size):
            ext_field_name = f"updated_extent{i}"
            if hasattr(input_payload, ext_field_name):
                updated_ext = getattr(input_payload, ext_field_name)
                new_ext = DynamicCapacityExtentStruct()
                new_ext.start_dpa = updated_ext.starting_dpa
                new_ext.length = updated_ext.length
                new_ext.tag = 0
                new_ext.shared_extent_seq = 0
                self._dc_extent_list.append(new_ext)

        context.status["return_code"] = MAILBOX_RETURN_CODE.SUCCESS
        return True


#
#   ReleaseDynamicCapacity command (Opcode 4803h)
#
class ReleaseDynamicCapacityInput(UnalignedBitStructure):
    updated_extent_list_size: int
    flags: int
    reserved: int
    updated_extent0: UpdatedExtentStruct
    updated_extent1: UpdatedExtentStruct
    updated_extent2: UpdatedExtentStruct
    updated_extent3: UpdatedExtentStruct
    updated_extent4: UpdatedExtentStruct
    updated_extent5: UpdatedExtentStruct
    updated_extent6: UpdatedExtentStruct
    updated_extent7: UpdatedExtentStruct

    def __init__(self, updated_ext_list: list[UpdatedExtentStruct]):
        self._fields = [
            ByteField("updated_extent_list_size", 0x00, 0x03),
            ByteField("flags", 0x04, 0x04),
            ByteField("reserved", 0x05, 0x07),
        ]
        start_offset = 0x08
        entry_size = UpdatedExtentStruct.get_size()
        entry_index = 0
        for _ in updated_ext_list:
            end_offset = start_offset + entry_size - 1
            self._fields.append(
                StructureField(
                    f"updated_extent{entry_index}",
                    start_offset,
                    end_offset,
                    UpdatedExtentStruct,
                )
            )
            entry_index += 1
            start_offset += entry_size
        super().__init__()
        
        # Copy the extent values into the structured fields
        for i, ext in enumerate(updated_ext_list):
            target = getattr(self, f"updated_extent{i}")
            target.starting_dpa = ext.starting_dpa
            target.length = ext.length
            target.reserved = ext.reserved

    @staticmethod
    def get_size(updated_extent_list: list[UpdatedExtentStruct]):
        # pylint: disable=arguments-renamed
        return 0x8 + UPDATED_EXT_STRUCT_SIZE * len(updated_extent_list)

    @classmethod
    def parse(cls, data: bytes) -> "ReleaseDynamicCapacityInput":
        extent_list_size = int.from_bytes(data[0:4], "little")
        dummy_list = [UpdatedExtentStruct() for _ in range(extent_list_size)]
        obj = cls(dummy_list)
        obj.reset(data)
        return obj


class ReleaseDynamicCapacity(CxlMailboxCommandBase):
    def __init__(self, dc_extent_list: list[DynamicCapacityExtentStruct]):
        super().__init__(0x4803)
        self._dc_extent_list = dc_extent_list

    def process(self, context: CxlMailboxContext) -> bool:
        payload_length = context.command["payload_length"]
        if payload_length < 8:
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        input_data = bytes(context.payloads.create_shared(payload_length))
        try:
            input_payload = ReleaseDynamicCapacityInput.parse(input_data)
        except Exception:
            context.status["return_code"] = MAILBOX_RETURN_CODE.INVALID_INPUT
            return True

        for i in range(input_payload.updated_extent_list_size):
            ext_field_name = f"updated_extent{i}"
            if hasattr(input_payload, ext_field_name):
                updated_ext = getattr(input_payload, ext_field_name)
                match_index = -1
                for j, active_ext in enumerate(self._dc_extent_list):
                    if active_ext.start_dpa == updated_ext.starting_dpa and active_ext.length == updated_ext.length:
                        match_index = j
                        break
                if match_index != -1:
                    self._dc_extent_list.pop(match_index)

        context.status["return_code"] = MAILBOX_RETURN_CODE.SUCCESS
        return True
