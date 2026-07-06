"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
from struct import pack, unpack
from uuid import UUID

from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.device.config.dynamic_capacity_device import DynamicCapacityExtent
from opencis.util.logger import logger

# Simulated DCD tags and extents database
_dcd_tags = {}  # tag_bytes: {"fm_ref": bool, "ref_bitmap": bytes(32), "pending_bitmap": bytes(32)}
_dcd_extents = {}  # host_id: list of DynamicCapacityExtent


@dataclass
class GetDcRegionExtentListsRequestPayload:
    host_id: int
    extent_count: int
    starting_extent_index: int

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 12:
            raise ValueError("Data too short")
        host_id = unpack("<H", data[0:2])[0]
        # bytes 2-3 are reserved
        extent_count = unpack("<I", data[4:8])[0]
        starting_extent_index = unpack("<I", data[8:12])[0]
        return cls(host_id, extent_count, starting_extent_index)

    def dump(self) -> bytes:
        data = bytearray(12)
        data[0:2] = pack("<H", self.host_id)
        data[4:8] = pack("<I", self.extent_count)
        data[8:12] = pack("<I", self.starting_extent_index)
        return bytes(data)


@dataclass
class GetDcRegionExtentListsResponsePayload:
    host_id: int
    starting_extent_index: int
    returned_extent_count: int
    total_extent_count: int
    extent_list_generation_number: int
    extents: list[DynamicCapacityExtent]

    def dump(self) -> bytes:
        header = bytearray(28)
        header[0:2] = pack("<H", self.host_id)
        # bytes 2-3 reserved
        header[4:8] = pack("<I", self.starting_extent_index)
        header[8:12] = pack("<I", self.returned_extent_count)
        header[12:16] = pack("<I", self.total_extent_count)
        header[16:20] = pack("<I", self.extent_list_generation_number)
        # bytes 20-27 reserved
        payload = bytes(header)
        for extent in self.extents:
            # DynamicCapacityExtent format:
            # start_dpa (8B), length (8B), tag_upper (8B), tag_lower (8B), shared_seq (2B), reserved (6B)
            tag_upper = (extent.tag >> 64) & 0xFFFFFFFFFFFFFFFF
            tag_lower = extent.tag & 0xFFFFFFFFFFFFFFFF
            payload += pack(
                "<QQQQH6s",
                extent.start_dpa,
                extent.length,
                tag_upper,
                tag_lower,
                extent.shared_extent_seq,
                b"\x00" * 6,
            )
        return payload

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 28:
            raise ValueError("Data too short")
        host_id = unpack("<H", data[0:2])[0]
        starting_extent_index = unpack("<I", data[4:8])[0]
        returned_extent_count = unpack("<I", data[8:12])[0]
        total_extent_count = unpack("<I", data[12:16])[0]
        extent_list_generation_number = unpack("<I", data[16:20])[0]

        extents = []
        offset = 28
        extent_struct_size = 40  # 8+8+8+8+2+6
        for _ in range(returned_extent_count):
            if len(data) < offset + extent_struct_size:
                break
            (
                start_dpa,
                length,
                tag_upper,
                tag_lower,
                shared_extent_seq,
                _,
            ) = unpack("<QQQQH6s", data[offset : offset + extent_struct_size])
            tag = tag_upper << 64 | tag_lower
            extents.append(DynamicCapacityExtent(start_dpa, length, tag, shared_extent_seq))
            offset += extent_struct_size
        return cls(
            host_id,
            starting_extent_index,
            returned_extent_count,
            total_extent_count,
            extent_list_generation_number,
            extents,
        )


@dataclass
class DynamicCapacityReferenceRequestPayload:
    tag: bytes  # 16 bytes

    def dump(self) -> bytes:
        return self.tag

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 16:
            raise ValueError("Tag must be 16 bytes")
        return cls(data[:16])


@dataclass
class DynamicCapacityListTagsRequestPayload:
    starting_index: int
    max_tags: int

    def dump(self) -> bytes:
        return pack("<II", self.starting_index, self.max_tags)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 8:
            raise ValueError("Data too short")
        return cls(*unpack("<II", data[:8]))


@dataclass
class DynamicCapacityTagInfoBlock:
    tag: bytes  # 16 bytes
    flags: int
    reference_bitmap: bytes  # 32 bytes
    pending_reference_bitmap: bytes  # 32 bytes

    def dump(self) -> bytes:
        data = bytearray(20)
        data[:16] = self.tag
        data[16] = self.flags
        # bytes 17-19 reserved
        return bytes(data) + self.reference_bitmap + self.pending_reference_bitmap

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 84:
            raise ValueError("Data too short")
        tag = data[:16]
        flags = data[16]
        reference_bitmap = data[20:52]
        pending_reference_bitmap = data[52:84]
        return cls(tag, flags, reference_bitmap, pending_reference_bitmap)


@dataclass
class DynamicCapacityListTagsResponsePayload:
    generation_number: int
    total_number_of_tags: int
    number_of_tags_returned: int
    validity_bitmap: int
    tag_info_list: list[DynamicCapacityTagInfoBlock]

    def dump(self) -> bytes:
        data = bytearray(16)
        data[0:4] = pack("<I", self.generation_number)
        data[4:8] = pack("<I", self.total_number_of_tags)
        data[8:12] = pack("<I", self.number_of_tags_returned)
        data[12] = self.validity_bitmap
        # bytes 13-15 reserved
        payload = bytes(data)
        for tag_info in self.tag_info_list:
            payload += tag_info.dump()
        return payload

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 16:
            raise ValueError("Data too short")
        generation_number = unpack("<I", data[0:4])[0]
        total_number_of_tags = unpack("<I", data[4:8])[0]
        number_of_tags_returned = unpack("<I", data[8:12])[0]
        validity_bitmap = data[12]

        tag_info_list = []
        offset = 16
        block_size = 84  # 20 + 32 + 32
        for _ in range(number_of_tags_returned):
            if len(data) < offset + block_size:
                break
            tag_info_list.append(DynamicCapacityTagInfoBlock.parse(data[offset : offset + block_size]))
            offset += block_size
        return cls(
            generation_number,
            total_number_of_tags,
            number_of_tags_returned,
            validity_bitmap,
            tag_info_list,
        )


class GetDcRegionExtentListsCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_DC_REGION_EXTENT_LISTS

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = GetDcRegionExtentListsRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        host_extents = _dcd_extents.get(req.host_id, [])
        total_count = len(host_extents)

        if req.starting_extent_index > total_count:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        returned_count = min(req.extent_count, total_count - req.starting_extent_index)
        extents_to_return = host_extents[
            req.starting_extent_index : req.starting_extent_index + returned_count
        ]

        response = GetDcRegionExtentListsResponsePayload(
            host_id=req.host_id,
            starting_extent_index=req.starting_extent_index,
            returned_extent_count=returned_count,
            total_extent_count=total_count,
            extent_list_generation_number=1,
            extents=extents_to_return,
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class DynamicCapacityAddReferenceCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.DYNAMIC_CAPACITY_ADD_REFERENCE

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = DynamicCapacityReferenceRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        tag = req.tag
        if tag not in _dcd_tags:
            _dcd_tags[tag] = {
                "fm_ref": True,
                "ref_bitmap": b"\x00" * 32,
                "pending_bitmap": b"\x00" * 32,
            }
        else:
            _dcd_tags[tag]["fm_ref"] = True

        logger.info(self._create_message(f"Added Dynamic Capacity reference for Tag {tag.hex()}"))
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)


class DynamicCapacityRemoveReferenceCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.DYNAMIC_CAPACITY_REMOVE_REFERENCE

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = DynamicCapacityReferenceRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        tag = req.tag
        if tag not in _dcd_tags:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        _dcd_tags[tag]["fm_ref"] = False
        logger.info(self._create_message(f"Removed Dynamic Capacity reference for Tag {tag.hex()}"))
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)


class DynamicCapacityListTagsCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.DYNAMIC_CAPACITY_LIST_TAGS

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = DynamicCapacityListTagsRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        all_tags = list(_dcd_tags.keys())
        total_tags = len(all_tags)

        if req.starting_index > total_tags:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        returned_count = min(req.max_tags, total_tags - req.starting_index)
        tags_to_return = all_tags[req.starting_index : req.starting_index + returned_count]

        tag_info_list = []
        for tag in tags_to_return:
            info = _dcd_tags[tag]
            block = DynamicCapacityTagInfoBlock(
                tag=tag,
                flags=1 if info["fm_ref"] else 0,
                reference_bitmap=info["ref_bitmap"],
                pending_reference_bitmap=info["pending_bitmap"],
            )
            tag_info_list.append(block)

        response = DynamicCapacityListTagsResponsePayload(
            generation_number=1,
            total_number_of_tags=total_tags,
            number_of_tags_returned=returned_count,
            validity_bitmap=0x03,  # Ref and Pending bitmaps valid
            tag_info_list=tag_info_list,
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


# Helper for testing to inject simulated extents
def helper_inject_extent(host_id: int, extent: DynamicCapacityExtent):
    if host_id not in _dcd_extents:
        _dcd_extents[host_id] = []
    _dcd_extents[host_id].append(extent)
