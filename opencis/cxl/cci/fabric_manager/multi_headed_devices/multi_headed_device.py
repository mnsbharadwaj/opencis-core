"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
from struct import pack, unpack

from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.util.logger import logger

# Simulated Multi-Headed Device state
_mhd_num_lds = 16
_mhd_num_heads = 4
_mhd_ld_map = [0, 0, 1, 1, 2, 2, 3, 3, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]


@dataclass
class GetMultiHeadedInfoRequestPayload:
    start_ld_id: int
    ld_map_list_limit: int

    def dump(self) -> bytes:
        return pack("<BB", self.start_ld_id, self.ld_map_list_limit)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 2:
            raise ValueError("Data too short")
        return cls(data[0], data[1])


@dataclass
class GetMultiHeadedInfoResponsePayload:
    num_lds: int
    num_heads: int
    start_ld_id: int
    ld_map_length: int
    ld_map: bytes  # 1 byte per LD in list

    def dump(self) -> bytes:
        data = bytearray(8 + len(self.ld_map))
        data[0] = self.num_lds
        data[1] = self.num_heads
        # bytes 2-3 are reserved
        data[4] = self.start_ld_id
        data[5] = self.ld_map_length
        # bytes 6-7 are reserved
        data[8:] = self.ld_map
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 8:
            raise ValueError("Data too short")
        num_lds = data[0]
        num_heads = data[1]
        start_ld_id = data[4]
        ld_map_length = data[5]
        ld_map = data[8 : 8 + ld_map_length]
        return cls(num_lds, num_heads, start_ld_id, ld_map_length, ld_map)


@dataclass
class GetHeadInfoRequestPayload:
    start_head: int
    num_heads: int

    def dump(self) -> bytes:
        return pack("<BB", self.start_head, self.num_heads)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 2:
            raise ValueError("Data too short")
        return cls(data[0], data[1])


@dataclass
class HeadInfoBlock:
    port_number: int
    max_link_width: int
    negotiated_link_width: int
    supported_link_speeds: int
    max_link_speed: int
    current_link_speed: int
    ltssm_state: int
    first_negotiated_lane: int
    link_state_flags: int

    def dump(self) -> bytes:
        data = bytearray(12)
        data[0] = self.port_number
        data[1] = self.max_link_width & 0x3F
        data[2] = self.negotiated_link_width & 0x3F
        data[3] = self.supported_link_speeds & 0x3F
        data[4] = self.max_link_speed & 0x3F
        data[5] = self.current_link_speed & 0x3F
        data[6] = self.ltssm_state
        data[7] = self.first_negotiated_lane
        data[8] = self.link_state_flags & 0x03
        # bytes 9-11 reserved
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 12:
            raise ValueError("Data too short")
        port_number = data[0]
        max_link_width = data[1] & 0x3F
        negotiated_link_width = data[2] & 0x3F
        supported_link_speeds = data[3] & 0x3F
        max_link_speed = data[4] & 0x3F
        current_link_speed = data[5] & 0x3F
        ltssm_state = data[6]
        first_negotiated_lane = data[7]
        link_state_flags = data[8] & 0x03
        return cls(
            port_number,
            max_link_width,
            negotiated_link_width,
            supported_link_speeds,
            max_link_speed,
            current_link_speed,
            ltssm_state,
            first_negotiated_lane,
            link_state_flags,
        )


@dataclass
class GetHeadInfoResponsePayload:
    num_heads: int
    heads: list[HeadInfoBlock]

    def dump(self) -> bytes:
        data = bytearray(4)
        data[0] = self.num_heads
        # bytes 1-3 reserved
        payload = bytes(data)
        for head in self.heads:
            payload += head.dump()
        return payload

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 4:
            raise ValueError("Data too short")
        num_heads = data[0]
        heads = []
        offset = 4
        for _ in range(num_heads):
            if len(data) < offset + 12:
                break
            heads.append(HeadInfoBlock.parse(data[offset : offset + 12]))
            offset += 12
        return cls(num_heads, heads)


class GetMultiHeadedInfoCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_MULTI_HEADED_INFO

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = GetMultiHeadedInfoRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        if req.start_ld_id >= _mhd_num_lds:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        length = min(req.ld_map_list_limit, _mhd_num_lds - req.start_ld_id)
        ld_map = bytes(_mhd_ld_map[req.start_ld_id : req.start_ld_id + length])

        response = GetMultiHeadedInfoResponsePayload(
            num_lds=_mhd_num_lds,
            num_heads=_mhd_num_heads,
            start_ld_id=req.start_ld_id,
            ld_map_length=length,
            ld_map=ld_map,
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class GetHeadInfoCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_HEAD_INFO

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = GetHeadInfoRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        if req.start_head >= _mhd_num_heads or req.start_head + req.num_heads > _mhd_num_heads:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        heads = []
        for i in range(req.num_heads):
            head_idx = req.start_head + i
            # Simulate generic active link state
            block = HeadInfoBlock(
                port_number=head_idx,
                max_link_width=16,          # x16
                negotiated_link_width=16,   # x16
                supported_link_speeds=0x0F, # Gen 1-4
                max_link_speed=4,           # Gen 4 (16GT/s)
                current_link_speed=4,       # Gen 4
                ltssm_state=4,              # L0
                first_negotiated_lane=0,
                link_state_flags=0,         # normal lane, no reset
            )
            heads.append(block)

        response = GetHeadInfoResponsePayload(num_heads=req.num_heads, heads=heads)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())
