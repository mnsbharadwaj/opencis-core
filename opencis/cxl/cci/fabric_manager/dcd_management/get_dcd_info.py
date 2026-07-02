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
from opencis.cxl.device.config.logical_device import LogicalDeviceConfig


@dataclass
class GetDcdInfoRequestPayload:
    port_id: int = 0
    ld_id: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "GetDcdInfoRequestPayload":
        if len(data) < 2:
            raise ValueError("Data is too short to parse")
        port_id = data[0]
        ld_id = data[1]
        return cls(port_id=port_id, ld_id=ld_id)

    def dump(self) -> bytes:
        return bytes([self.port_id, self.ld_id])

    def get_pretty_print(self) -> str:
        return f"- Port ID: {self.port_id}\n- LD ID: {self.ld_id}"


@dataclass
class GetDcdInfoResponsePayload:
    num_hosts: int = 0
    num_supported_dc_regions: int = 0
    reserved1: int = 0
    add_capacity_selection_policies: int = 0
    reserved2: int = 0
    release_capacity_removal_policies: int = 0
    reserved3: int = 0
    sanitize: int = 0
    reserved4: int = 0
    total_dynamic_capacity: int = 0
    reserved5: int = 0
    region0_block_size_mask: int = 0
    region1_block_size_mask: int = 0
    region2_block_size_mask: int = 0
    region3_block_size_mask: int = 0
    region4_block_size_mask: int = 0
    region5_block_size_mask: int = 0
    region6_block_size_mask: int = 0
    region7_block_size_mask: int = 0
    pack_mask: str = "<BBHHHHBBQQQQQQQQQQQ"

    @classmethod
    def parse(cls, data: bytes) -> "GetDcdInfoResponsePayload":
        if len(data) != struct.calcsize(cls.pack_mask):
            raise ValueError("Data is too short to parse.")
        (
            num_hosts,
            num_supported_dc_regions,
            reserved1,
            add_capacity_selection_policies,
            reserved2,
            release_capacity_removal_policies,
            reserved3,
            sanitize,
            reserved4,
            total_dynamic_capacity,
            reserved5,
            region0_block_size_mask,
            region1_block_size_mask,
            region2_block_size_mask,
            region3_block_size_mask,
            region4_block_size_mask,
            region5_block_size_mask,
            region6_block_size_mask,
            region7_block_size_mask,
        ) = struct.unpack(cls.pack_mask, data)

        return cls(
            num_hosts=num_hosts,
            num_supported_dc_regions=num_supported_dc_regions,
            reserved1=reserved1,
            add_capacity_selection_policies=add_capacity_selection_policies,
            reserved2=reserved2,
            release_capacity_removal_policies=release_capacity_removal_policies,
            reserved3=reserved3,
            sanitize=sanitize,
            reserved4=reserved4,
            total_dynamic_capacity=total_dynamic_capacity,
            reserved5=reserved5,
            region0_block_size_mask=region0_block_size_mask,
            region1_block_size_mask=region1_block_size_mask,
            region2_block_size_mask=region2_block_size_mask,
            region3_block_size_mask=region3_block_size_mask,
            region4_block_size_mask=region4_block_size_mask,
            region5_block_size_mask=region5_block_size_mask,
            region6_block_size_mask=region6_block_size_mask,
            region7_block_size_mask=region7_block_size_mask,
        )

    def dump(self) -> bytes:
        databytes = struct.pack(
            self.pack_mask,
            self.num_hosts,
            self.num_supported_dc_regions,
            self.reserved1,
            self.add_capacity_selection_policies,
            self.reserved2,
            self.release_capacity_removal_policies,
            self.reserved3,
            self.sanitize,
            self.reserved4,
            self.total_dynamic_capacity,
            self.reserved5,
            self.region0_block_size_mask,
            self.region1_block_size_mask,
            self.region2_block_size_mask,
            self.region3_block_size_mask,
            self.region4_block_size_mask,
            self.region5_block_size_mask,
            self.region6_block_size_mask,
            self.region7_block_size_mask,
        )
        return databytes

    def get_pretty_print(self) -> str:
        return (
            f"- Number of Hosts: {self.num_hosts}\n"
            f"- Number of Supported DC Regions: {self.num_supported_dc_regions}\n"
            f"- Add Capacity Selection Policies: {self.add_capacity_selection_policies}\n"
            f"- Release Capacity Removal Policies: {self.release_capacity_removal_policies}\n"
            f"- Sanitize: {self.sanitize}\n"
            f"- Total Dynamic Capacity: {self.total_dynamic_capacity}\n"
            f"- Region 0 Block Size Mask: {self.region0_block_size_mask}"
        )


class GetDcdInfoCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_DCD_INFO

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
            req_payload = GetDcdInfoRequestPayload.parse(request.payload)
            port_id = req_payload.port_id
        except Exception:
            port_id = 1

        # Validate port ID
        if port_id >= self._physical_port_manager.get_port_counts():
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # Defaults
        num_hosts = 1
        num_supported_dc_regions = 1
        total_dynamic_capacity = 0x40000000  # 1 GB
        region0_block_size_mask = 0x10000000  # 256 MB

        # If device config exists, use its size
        if self._device_configs:
            for cfg in self._device_configs:
                if cfg.port_index == port_id:
                    total_dynamic_capacity = cfg.memory_size
                    break

        response_payload = GetDcdInfoResponsePayload(
            num_hosts=num_hosts,
            num_supported_dc_regions=num_supported_dc_regions,
            total_dynamic_capacity=total_dynamic_capacity,
            region0_block_size_mask=region0_block_size_mask,
        )
        return self.create_cci_response(response_payload)

    @classmethod
    def create_cci_request(cls, request: GetDcdInfoRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def create_cci_response(response: GetDcdInfoResponsePayload) -> CciResponse:
        cci_response = CciResponse()
        cci_response.payload = response.dump()
        return cci_response

    @staticmethod
    def parse_request_payload(payload: bytes) -> GetDcdInfoRequestPayload:
        return GetDcdInfoRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(payload: bytes) -> GetDcdInfoResponsePayload:
        return GetDcdInfoResponsePayload.parse(payload)
