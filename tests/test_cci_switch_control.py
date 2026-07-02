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
