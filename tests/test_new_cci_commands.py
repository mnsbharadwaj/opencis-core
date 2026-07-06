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

# Import MLD Component QoS Commands
from opencis.cxl.cci.fabric_manager.mld_components import (
    GetQosControlCommand,
    SetQosControlCommand,
    GetQosStatusCommand,
    GetQosAllocatedBwCommand,
    SetQosAllocatedBwCommand,
    GetQosBwLimitCommand,
    SetQosBwLimitCommand,
    QosControlPayload,
    QosFractionRequestPayload,
    QosFractionResponsePayload,
)

# Import Multi-Headed Device Commands
from opencis.cxl.cci.fabric_manager.multi_headed_devices import (
    GetMultiHeadedInfoCommand,
    GetMultiHeadedInfoRequestPayload,
    GetHeadInfoCommand,
    GetHeadInfoRequestPayload,
)

# Import DCD Management Commands
from opencis.cxl.cci.fabric_manager.dcd_management import (
    GetDcRegionExtentListsCommand,
    GetDcRegionExtentListsRequestPayload,
    DynamicCapacityAddReferenceCommand,
    DynamicCapacityReferenceRequestPayload,
    DynamicCapacityRemoveReferenceCommand,
    DynamicCapacityListTagsCommand,
    DynamicCapacityListTagsRequestPayload,
    helper_inject_extent,
)
from opencis.cxl.device.config.dynamic_capacity_device import DynamicCapacityExtent


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


# ===========================================================================
# MLD Component (QoS) Command Set Tests
# ===========================================================================

def test_qos_control_execute(virtual_switch_manager):
    cmd_get = GetQosControlCommand(virtual_switch_manager)
    cmd_set = SetQosControlCommand(virtual_switch_manager)
    cmd_status = GetQosStatusCommand(virtual_switch_manager)

    # Set config
    req_set_payload = QosControlPayload(
        qos_telemetry_control=1,
        egress_moderate_pct=15,
        egress_severe_pct=30,
        backpressure_sample_interval=10,
        req_cmp_basis=100,
        completion_collection_interval=128
    )
    req_set = CciRequest(opcode=cmd_set.OPCODE, payload=req_set_payload.dump())
    resp_set = run(cmd_set._execute(req_set))
    assert resp_set.return_code == CCI_RETURN_CODE.SUCCESS

    # Get config back
    req_get = CciRequest(opcode=cmd_get.OPCODE)
    resp_get = run(cmd_get._execute(req_get))
    parsed = QosControlPayload.parse(resp_get.payload)
    assert parsed.qos_telemetry_control == 1
    assert parsed.egress_moderate_pct == 15
    assert parsed.egress_severe_pct == 30
    assert parsed.req_cmp_basis == 100

    # Get Status
    req_status = CciRequest(opcode=cmd_status.OPCODE)
    resp_status = run(cmd_status._execute(req_status))
    assert resp_status.return_code == CCI_RETURN_CODE.SUCCESS
    assert resp_status.payload[0] == 5


def test_qos_fraction_execute(virtual_switch_manager):
    cmd_set_bw = SetQosAllocatedBwCommand(virtual_switch_manager)
    cmd_get_bw = GetQosAllocatedBwCommand(virtual_switch_manager)
    cmd_set_limit = SetQosBwLimitCommand(virtual_switch_manager)
    cmd_get_limit = GetQosBwLimitCommand(virtual_switch_manager)

    # Set allocated BW fractions for LDs 2 and 3
    set_payload = QosFractionResponsePayload(num_lds=2, start_ld_id=2, fractions=bytes([64, 128]))
    req = CciRequest(opcode=cmd_set_bw.OPCODE, payload=set_payload.dump())
    resp = run(cmd_set_bw._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # Get allocated BW fractions back
    get_req_payload = QosFractionRequestPayload(num_lds=2, start_ld_id=2)
    req = CciRequest(opcode=cmd_get_bw.OPCODE, payload=get_req_payload.dump())
    resp = run(cmd_get_bw._execute(req))
    parsed = QosFractionResponsePayload.parse(resp.payload)
    assert list(parsed.fractions) == [64, 128]

    # Set and Get BW limits
    set_limit_payload = QosFractionResponsePayload(num_lds=2, start_ld_id=2, fractions=bytes([200, 250]))
    req = CciRequest(opcode=cmd_set_limit.OPCODE, payload=set_limit_payload.dump())
    resp = run(cmd_set_limit._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    req = CciRequest(opcode=cmd_get_limit.OPCODE, payload=get_req_payload.dump())
    resp = run(cmd_get_limit._execute(req))
    parsed = QosFractionResponsePayload.parse(resp.payload)
    assert list(parsed.fractions) == [200, 250]


# ===========================================================================
# Multi-Headed Device Command Set Tests
# ===========================================================================

def test_multi_headed_device_execute(physical_port_manager):
    cmd_info = GetMultiHeadedInfoCommand(physical_port_manager)
    cmd_head = GetHeadInfoCommand(physical_port_manager)

    # Get Multi-Headed Info
    req_payload = GetMultiHeadedInfoRequestPayload(start_ld_id=2, ld_map_list_limit=6)
    req = CciRequest(opcode=cmd_info.OPCODE, payload=req_payload.dump())
    resp = run(cmd_info._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    
    # Get Head Info
    req_head_payload = GetHeadInfoRequestPayload(start_head=1, num_heads=2)
    req = CciRequest(opcode=cmd_head.OPCODE, payload=req_head_payload.dump())
    resp = run(cmd_head._execute(req))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS


# ===========================================================================
# DCD Management Command Set Tests
# ===========================================================================

def test_dc_region_reference_execute(physical_port_manager):
    cmd_add = DynamicCapacityAddReferenceCommand(physical_port_manager)
    cmd_remove = DynamicCapacityRemoveReferenceCommand(physical_port_manager)
    cmd_list = DynamicCapacityListTagsCommand(physical_port_manager)
    cmd_extents = GetDcRegionExtentListsCommand(physical_port_manager)

    tag = uuid4().bytes

    # Add reference
    req_add = CciRequest(opcode=cmd_add.OPCODE, payload=DynamicCapacityReferenceRequestPayload(tag).dump())
    resp = run(cmd_add._execute(req_add))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # List Tags
    req_list = CciRequest(opcode=cmd_list.OPCODE, payload=DynamicCapacityListTagsRequestPayload(starting_index=0, max_tags=10).dump())
    resp = run(cmd_list._execute(req_list))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # Inject mock extent for Host 0
    ext = DynamicCapacityExtent(start_dpa=0x10000000, length=0x1000000, tag=int.from_bytes(tag, "big"), shared_extent_seq=1)
    helper_inject_extent(host_id=0, extent=ext)

    # Get DC Region Extent List
    req_ext = CciRequest(opcode=cmd_extents.OPCODE, payload=GetDcRegionExtentListsRequestPayload(host_id=0, extent_count=10, starting_extent_index=0).dump())
    resp = run(cmd_extents._execute(req_ext))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS

    # Remove reference
    req_remove = CciRequest(opcode=cmd_remove.OPCODE, payload=DynamicCapacityReferenceRequestPayload(tag).dump())
    resp = run(cmd_remove._execute(req_remove))
    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
