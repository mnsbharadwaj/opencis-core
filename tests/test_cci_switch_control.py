"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import pytest
from unittest.mock import MagicMock

from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse
from opencis.cxl.cci.fabric_manager.physical_switch import (
    PhysicalPortControlCommand,
    PhysicalPortControlRequestPayload,
    SendPpbCxlIoConfigurationRequestCommand,
    SendPpbCxlIoConfigRequestPayload,
    SendPpbCxlIoConfigResponsePayload,
)
from opencis.cxl.cci.fabric_manager.virtual_switch import (
    GenerateAerEventCommand,
    GenerateAerEventRequestPayload,
    TunnelManagementCommand,
)


class MockPortDevice:
    def __init__(self):
        self._pci_registers = {
            0: MagicMock()
        }


class MockPhysicalPortManager:
    def __init__(self, port_count=4):
        self._port_count = port_count
        self._ports = [MockPortDevice() for _ in range(port_count)]

    def get_port_counts(self) -> int:
        return self._port_count

    def get_port_device(self, port_id: int):
        return self._ports[port_id]


class MockVirtualSwitch:
    def get_vppb_counts(self) -> int:
        return 4


class MockVirtualSwitchManager:
    def get_virtual_switch_counts(self) -> int:
        return 1

    def get_virtual_switch(self, vcs_id: int):
        return MockVirtualSwitch()


@pytest.mark.asyncio
async def test_physical_port_control_command():
    pm = MockPhysicalPortManager(port_count=4)
    cmd = PhysicalPortControlCommand(pm)

    # 1. Valid request
    req_payload = PhysicalPortControlRequestPayload(port_id=1, port_control=3)
    req = PhysicalPortControlCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # 2. Invalid port ID
    req_payload = PhysicalPortControlRequestPayload(port_id=99, port_control=3)
    req = PhysicalPortControlCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT

    # 3. Invalid port control
    req_payload = PhysicalPortControlRequestPayload(port_id=1, port_control=99)
    req = PhysicalPortControlCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_send_ppb_cxl_io_config_request_command():
    pm = MockPhysicalPortManager(port_count=4)
    cmd = SendPpbCxlIoConfigurationRequestCommand(pm)

    # Setup mock config register read/write
    mock_reg = pm.get_port_device(1)._pci_registers[0]
    mock_reg.read_bytes.return_value = 0x1E98

    # 1. Valid Read Request
    req_payload = SendPpbCxlIoConfigRequestPayload(
        port_id=1,
        register_number=0,
        ext_register_number=0,
        first_dword_byte_enable=0xF,
        last_dword_byte_enable=0,
        request_type=0,  # Read
    )
    req = SendPpbCxlIoConfigurationRequestCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    
    resp_payload = SendPpbCxlIoConfigurationRequestCommand.parse_response_payload(resp.payload)
    assert resp_payload.completion_status == 0
    assert resp_payload.data == 0x1E98
    mock_reg.read_bytes.assert_called_once_with(0, 3)

    # 2. Valid Write Request
    req_payload = SendPpbCxlIoConfigRequestPayload(
        port_id=1,
        register_number=0,
        ext_register_number=0,
        first_dword_byte_enable=0xF,
        last_dword_byte_enable=0,
        request_type=1,  # Write
        data=0x12345678,
    )
    req = SendPpbCxlIoConfigurationRequestCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    
    resp_payload = SendPpbCxlIoConfigurationRequestCommand.parse_response_payload(resp.payload)
    assert resp_payload.completion_status == 0
    mock_reg.write_bytes.assert_called_once_with(0, 3, 0x12345678)

    # 3. Invalid port ID
    req_payload = SendPpbCxlIoConfigRequestPayload(port_id=99)
    req = SendPpbCxlIoConfigurationRequestCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_generate_aer_event_command():
    vsm = MockVirtualSwitchManager()
    cmd = GenerateAerEventCommand(vsm)

    # 1. Valid request
    req_payload = GenerateAerEventRequestPayload(
        vcs_id=0,
        vppb_id=1,
        error_type=1,  # Non-fatal
        aer_error_status=0x00040000,
    )
    req = GenerateAerEventCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # 2. Invalid VCS ID
    req_payload = GenerateAerEventRequestPayload(vcs_id=99)
    req = GenerateAerEventCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT

    # 3. Invalid vPPB ID
    req_payload = GenerateAerEventRequestPayload(vcs_id=0, vppb_id=99)
    req = GenerateAerEventCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT

    # 4. Invalid error type
    req_payload = GenerateAerEventRequestPayload(vcs_id=0, vppb_id=1, error_type=99)
    req = GenerateAerEventCommand.create_cci_request(req_payload)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_dcd_switch_commands():
    from opencis.cxl.cci.fabric_manager.dcd_management import (
        GetDcdInfoCommand,
        GetDcdInfoRequestPayload,
        GetHostDCRegionConfiguration,
        GetHostDCRegionConfigRequestPayload,
        SetDCRegionConfiguration,
        SetDCRegionConfigRequestPayload,
        GetDCRegionExtentLists,
        GetDCRegionExtentListsRequestPayload,
        InitiateDynamicCapacityAdd,
        InitiateDynamicCapacityAddRequestPayload,
        InitiateDynamicCapacityRelease,
        InitiateDynamicCapacityReleaseRequestPayload,
    )
    pm = MockPhysicalPortManager(port_count=4)
    vsm = MockVirtualSwitchManager()
    
    # 1. Get Dcd Info
    cmd_info = GetDcdInfoCommand(pm, vsm)
    req_info = GetDcdInfoCommand.create_cci_request(GetDcdInfoRequestPayload(port_id=1))
    resp_info = await cmd_info._execute(req_info)
    assert resp_info.return_code == CCI_RETURN_CODE.SUCCESS
    res_info = GetDcdInfoCommand.parse_response_payload(resp_info.payload)
    assert res_info.total_dynamic_capacity == 0x40000000

    # 2. Get Host DC Region Config
    cmd_region = GetHostDCRegionConfiguration(pm, vsm)
    req_region = GetHostDCRegionConfiguration.create_cci_request(
        GetHostDCRegionConfigRequestPayload(host_id=0, region_count=1, starting_region_index=0)
    )
    resp_region = await cmd_region._execute(req_region)
    assert resp_region.return_code == CCI_RETURN_CODE.SUCCESS
    # Parse the response payload from the packet
    from opencis.cxl.cci.fabric_manager.dcd_management import GetHostDCRegionConfigResponsePayload
    res_payload = GetHostDCRegionConfigResponsePayload.parse(resp_region.payload)
    assert res_payload.num_available_regions == 1
    assert res_payload.dc_region_configs[0].region_len == 0x40000000

    # 3. Set DC Region Config
    cmd_set_region = SetDCRegionConfiguration(pm, vsm)
    req_set = SetDCRegionConfiguration.create_cci_request(
        SetDCRegionConfigRequestPayload(region_id=0, region_block_size=0x10000000, flags=0)
    )
    resp_set = await cmd_set_region._execute(req_set)
    assert resp_set.return_code == CCI_RETURN_CODE.SUCCESS

    # 4. Get DC Region Extent Lists
    cmd_extents = GetDCRegionExtentLists(pm, vsm)
    req_ext = GetDCRegionExtentLists.create_cci_request(
        GetDCRegionExtentListsRequestPayload(host_id=0, region_block_size=0x10000000, flags=0)
    )
    resp_ext = await cmd_extents._execute(req_ext)
    assert resp_ext.return_code == CCI_RETURN_CODE.SUCCESS

    # 5. Initiate Dynamic Capacity Add
    cmd_add = InitiateDynamicCapacityAdd(pm, vsm)
    req_add = InitiateDynamicCapacityAdd.create_cci_request(
        InitiateDynamicCapacityAddRequestPayload(host_id=0, region_num=0, length=0x10000000, ext_count=0, dc_extents=[])
    )
    resp_add = await cmd_add._execute(req_add)
    assert resp_add.return_code == CCI_RETURN_CODE.SUCCESS

    # 6. Initiate Dynamic Capacity Release
    cmd_rel = InitiateDynamicCapacityRelease(pm, vsm)
    req_rel = InitiateDynamicCapacityRelease.create_cci_request(
        InitiateDynamicCapacityReleaseRequestPayload(host_id=0, extent_count=0, dc_extents=[])
    )
    resp_rel = await cmd_rel._execute(req_rel)
    assert resp_rel.return_code == CCI_RETURN_CODE.SUCCESS

