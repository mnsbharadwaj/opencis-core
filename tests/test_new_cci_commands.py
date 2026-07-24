"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio
import pytest
from unittest.mock import MagicMock
from uuid import uuid4
from struct import pack, unpack

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import CciRequest, CciResponse
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager

# Import Physical Switch Commands
from opencis.cxl.cci.fabric_manager.physical_switch import (
    PhysicalPortControlCommand,
    PhysicalPortControlRequestPayload,
    SendPpbCxlIoConfigurationRequestCommand,
    SendPpbCxlIoConfigurationRequestPayload,
    SendPpbCxlIoConfigurationResponsePayload,
    GetDomainValidationSvStateCommand,
    SetDomainValidationSvCommand,
    GetVcsDomainValidationSvStateCommand,
    GetDomainValidationSvCommand,
    SetDomainValidationSvRequestPayload,
)
# Import Virtual Switch Commands
from opencis.cxl.cci.fabric_manager.virtual_switch import (
    GenerateAerEventCommand,
    GenerateAerEventRequestPayload,
)
# Import MLD Port Commands
from opencis.cxl.cci.fabric_manager.mld_port import (
    SendLdCxlIoConfigurationRequestCommand,
    SendLdCxlIoConfigurationRequestPayload,
    SendLdCxlIoMemoryRequestCommand,
    SendLdCxlIoMemoryRequestPayload,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def physical_port_manager():
    ppm = MagicMock(spec=PhysicalPortManager)
    ppm.get_port_counts.return_return = 4
    ppm.get_port_counts.return_value = 4
    return ppm


@pytest.fixture
def virtual_switch_manager():
    vsm = MagicMock(spec=VirtualSwitchManager)
    vsm.get_virtual_switch_counts.return_value = 2
    return vsm


# ===========================================================================
# Physical Switch Command Set Tests
# ===========================================================================

def test_physical_port_control_payload():
    payload = PhysicalPortControlRequestPayload(ppb_id=2, port_opcode=1)
    dumped = payload.dump()
    parsed = PhysicalPortControlRequestPayload.parse(dumped)
    assert parsed.ppb_id == 2
    assert parsed.port_opcode == 1


def test_physical_port_control_execute(physical_port_manager):
    cmd = PhysicalPortControlCommand(physical_port_manager)
    
    # Valid call
    req = CciRequest(opcode=cmd.OPCODE, payload=PhysicalPortControlRequestPayload(ppb_id=1, port_opcode=2).dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # Out of bounds port
    req = CciRequest(opcode=cmd.OPCODE, payload=PhysicalPortControlRequestPayload(ppb_id=5, port_opcode=2).dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT

    # Invalid opcode
    req = CciRequest(opcode=cmd.OPCODE, payload=PhysicalPortControlRequestPayload(ppb_id=1, port_opcode=9).dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


def test_send_ppb_cxl_io_config_payload():
    payload = SendPpbCxlIoConfigurationRequestPayload(
        ppb_id=1, register_num=0x10, ext_register_num=2, first_dword_byte_enable=0xF, transaction_type=1, transaction_data=0xDEADBEEF
    )
    dumped = payload.dump()
    parsed = SendPpbCxlIoConfigurationRequestPayload.parse(dumped)
    assert parsed.ppb_id == 1
    assert parsed.register_num == 0x10
    assert parsed.ext_register_num == 2
    assert parsed.first_dword_byte_enable == 0xF
    assert parsed.transaction_type == 1
    assert parsed.transaction_data == 0xDEADBEEF


def test_send_ppb_cxl_io_config_execute(physical_port_manager):
    cmd = SendPpbCxlIoConfigurationRequestCommand(physical_port_manager)
    payload = SendPpbCxlIoConfigurationRequestPayload(
        ppb_id=1, register_num=0x10, ext_register_num=0, first_dword_byte_enable=0xF, transaction_type=0
    )
    req = CciRequest(opcode=cmd.OPCODE, payload=payload.dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    resp_payload = SendPpbCxlIoConfigurationResponsePayload.parse(resp.payload)
    assert resp_payload.return_data == 0


def test_domain_validation_execute(virtual_switch_manager):
    cmd_set = SetDomainValidationSvCommand(virtual_switch_manager)
    cmd_get_state = GetDomainValidationSvStateCommand(virtual_switch_manager)
    cmd_get_vcs_state = GetVcsDomainValidationSvStateCommand(virtual_switch_manager)
    cmd_get_sv = GetDomainValidationSvCommand(virtual_switch_manager)

    # Initial state should be Not Set (0x00)
    req = CciRequest(opcode=cmd_get_state.OPCODE)
    resp = run(cmd_get_state._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    assert resp.payload[0] == 0x00

    # Set secret value
    uuid_bytes = uuid4().bytes
    req_set = CciRequest(opcode=cmd_set.OPCODE, payload=SetDomainValidationSvRequestPayload(uuid_bytes).dump())
    resp_set = run(cmd_set._execute(req_set))
    assert resp_set.return_code == CCI_RETURN_CODE.SUCCESS

    # Should not allow setting it again (spec constraint)
    resp_set_again = run(cmd_set._execute(req_set))
    assert resp_set_again.return_code == CCI_RETURN_CODE.INVALID_INPUT

    # Now state should be Set (0x01)
    resp = run(cmd_get_state._execute(req))
    assert resp.payload[0] == 0x01

    # VCS state query
    req_vcs = CciRequest(opcode=cmd_get_vcs_state.OPCODE, payload=pack("<B", 0))
    resp_vcs = run(cmd_get_vcs_state._execute(req_vcs))
    assert resp_vcs.payload[0] == 0x01

    # Get secret value
    req_get = CciRequest(opcode=cmd_get_sv.OPCODE, payload=pack("<B", 0))
    resp_get = run(cmd_get_sv._execute(req_get))
    assert resp_get.return_code == CCI_RETURN_CODE.SUCCESS
    assert resp_get.payload == uuid_bytes


# ===========================================================================
# Virtual Switch Command Set Tests
# ===========================================================================

def test_generate_aer_event_payload():
    payload = GenerateAerEventRequestPayload(vcs_id=1, vppb_instance=2, aer_error=0x80000005, aer_header=b"\x11" * 32)
    dumped = payload.dump()
    parsed = GenerateAerEventRequestPayload.parse(dumped)
    assert parsed.vcs_id == 1
    assert parsed.vppb_instance == 2
    assert parsed.aer_error == 0x80000005
    assert parsed.aer_header == b"\x11" * 32


def test_generate_aer_event_execute(virtual_switch_manager):
    cmd = GenerateAerEventCommand(virtual_switch_manager)
    req_payload = GenerateAerEventRequestPayload(vcs_id=0, vppb_instance=1, aer_error=0x80000005, aer_header=b"\x00" * 32)
    req = CciRequest(opcode=cmd.OPCODE, payload=req_payload.dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS


# ===========================================================================
# MLD Port Command Set Tests
# ===========================================================================

def test_send_ld_cxl_io_config_execute(physical_port_manager):
    cmd = SendLdCxlIoConfigurationRequestCommand(physical_port_manager)
    payload = SendLdCxlIoConfigurationRequestPayload(
        ppb_id=1, register_num=0x08, ext_register_num=0, first_dword_byte_enable=0xF, transaction_type=0, ld_id=3
    )
    req = CciRequest(opcode=cmd.OPCODE, payload=payload.dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    assert resp.payload == pack("<I", 0)


def test_send_ld_cxl_io_memory_execute(physical_port_manager):
    cmd = SendLdCxlIoMemoryRequestCommand(physical_port_manager)
    payload = SendLdCxlIoMemoryRequestPayload(
        port_id=1, first_dword_byte_enable=0xF, last_dword_byte_enable=0, transaction_type=0, ld_id=2, transaction_length=8, transaction_address=0x10000
    )
    req = CciRequest(opcode=cmd.OPCODE, payload=payload.dump())
    resp = run(cmd._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    assert len(resp.payload) == 12  # 4 bytes header + 8 bytes data
    assert resp.payload[:2] == pack("<H", 8)


