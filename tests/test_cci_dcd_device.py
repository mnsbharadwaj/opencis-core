"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import pytest

from opencis.cxl.features.mailbox import (
    CxlMailbox,
    CxlMailboxContext,
    MailboxCapabilities,
    MailboxCommand,
    MailboxControl,
    MAILBOX_RETURN_CODE,
    MAILBOX_TYPE,
)
from opencis.cxl.component.cxl_memory_device_component import (
    CxlMemoryDeviceComponent,
    MemoryDeviceIdentity,
)
from opencis.cxl.device.config.dynamic_capacity_device import (
    RegionConfigStruct,
    DynamicCapacityExtentStruct,
)
from opencis.cxl.cci.memory_device.dynamic_capacity import (
    GetDynamicCapacityConfigInput,
    GetDynamicCapacityConfigOutput,
    GetDynamicCapacityExtentListInput,
    GetDynamicCapacityExtentListOutput,
    AddDynamicCapacityResponseInput,
    ReleaseDynamicCapacityInput,
    UpdatedExtentStruct,
)


def test_dcd_mailbox_commands():
    # 1. Initialize Memory Device Component with 1GB capacity
    identity = MemoryDeviceIdentity()
    identity.set_total_capacity(1024 * 1024 * 1024)  # 1 GB
    
    device_component = CxlMemoryDeviceComponent(
        identity=identity,
        memory_file="",  # Dummy mode, no actual file
    )
    
    mailbox = device_component.get_primary_mailbox()
    assert mailbox is not None

    # Verify mailbox has registered DCD commands
    assert 0x4800 in mailbox.commands
    assert 0x4801 in mailbox.commands
    assert 0x4802 in mailbox.commands
    assert 0x4803 in mailbox.commands

    # 2. Test GetDynamicCapacityConfig (0x4800)
    req_input = GetDynamicCapacityConfigInput()
    req_input.region_count = 1
    req_input.starting_region_index = 0
    
    mailbox.set_command(MailboxCommand(command_opcode=0x4800, payload_length=len(req_input)))
    mailbox.payloads.copy_from(bytes(req_input))
    
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS

    # Parse response
    resp_len = mailbox.command["payload_length"]
    resp_bytes = bytes(mailbox.payloads.create_shared(resp_len))
    
    # We expect 1 region config returned, plus total_available_regions = 1
    # Check total size of the response payload:
    # 8 bytes header + 1 region config struct (0x28 = 40 bytes) + 16 bytes footer = 64 bytes
    assert resp_len == 64
    
    dummy_region_list = [RegionConfigStruct()]
    resp_payload = GetDynamicCapacityConfigOutput(dummy_region_list)
    resp_payload.reset(resp_bytes)

    assert resp_payload.num_available_regions == 1
    assert resp_payload.regions_returned == 1
    assert resp_payload.region_config0.region_base == 0
    assert resp_payload.region_config0.region_decode_len == 4  # 1GB / 256MB = 4
    assert resp_payload.region_config0.region_len == 1024 * 1024 * 1024
    assert resp_payload.region_config0.region_block_size == 256 * 1024 * 1024

    # Test invalid index
    req_input_invalid = GetDynamicCapacityConfigInput()
    req_input_invalid.region_count = 1
    req_input_invalid.starting_region_index = 5
    mailbox.set_command(MailboxCommand(command_opcode=0x4800, payload_length=len(req_input_invalid)))
    mailbox.payloads.copy_from(bytes(req_input_invalid))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.INVALID_INPUT

    # 3. Test GetDynamicCapacityExtentList (0x4801) - initially empty
    extent_req = GetDynamicCapacityExtentListInput()
    extent_req.extent_count = 5
    extent_req.starting_extent_index = 0
    
    mailbox.set_command(MailboxCommand(command_opcode=0x4801, payload_length=len(extent_req)))
    mailbox.payloads.copy_from(bytes(extent_req))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS
    
    resp_len = mailbox.command["payload_length"]
    # 0 returned entries -> 16 bytes header/footer size
    assert resp_len == 16
    resp_bytes = bytes(mailbox.payloads.create_shared(resp_len))
    resp_ext_payload = GetDynamicCapacityExtentListOutput([])
    resp_ext_payload.reset(resp_bytes)
    assert resp_ext_payload.returned_extent_count == 0
    assert resp_ext_payload.total_extent_count == 0

    # 4. Test AddDynamicCapacityResponse (0x4802)
    # Host confirms adding 2 extents:
    # Extent 1: DPA 0x0, Len 0x10000000 (256MB)
    # Extent 2: DPA 0x10000000, Len 0x10000000
    ext1 = UpdatedExtentStruct()
    ext1.starting_dpa = 0x0
    ext1.length = 0x10000000
    
    ext2 = UpdatedExtentStruct()
    ext2.starting_dpa = 0x10000000
    ext2.length = 0x10000000

    add_input = AddDynamicCapacityResponseInput([ext1, ext2])
    add_input.updated_extent_list_size = 2
    add_input.flags = 0
    
    mailbox.set_command(MailboxCommand(command_opcode=0x4802, payload_length=len(add_input)))
    mailbox.payloads.copy_from(bytes(add_input))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS

    # Verify extents are now in active list
    mailbox.set_command(MailboxCommand(command_opcode=0x4801, payload_length=len(extent_req)))
    mailbox.payloads.copy_from(bytes(extent_req))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS
    
    resp_len = mailbox.command["payload_length"]
    # Header/Footer (16 bytes) + 2 entries (each 0x28 = 40 bytes) = 96 bytes
    assert resp_len == 96
    resp_bytes = bytes(mailbox.payloads.create_shared(resp_len))
    dummy_exts = [DynamicCapacityExtentStruct(), DynamicCapacityExtentStruct()]
    resp_ext_payload = GetDynamicCapacityExtentListOutput(dummy_exts)
    resp_ext_payload.reset(resp_bytes)
    assert resp_ext_payload.returned_extent_count == 2
    assert resp_ext_payload.total_extent_count == 2
    assert resp_ext_payload.dc_extent0.start_dpa == 0x0
    assert resp_ext_payload.dc_extent0.length == 0x10000000
    assert resp_ext_payload.dc_extent1.start_dpa == 0x10000000
    assert resp_ext_payload.dc_extent1.length == 0x10000000

    # 5. Test ReleaseDynamicCapacity (0x4803)
    # Host releases Extent 1 (DPA 0x0)
    release_input = ReleaseDynamicCapacityInput([ext1])
    release_input.updated_extent_list_size = 1
    release_input.flags = 0
    
    mailbox.set_command(MailboxCommand(command_opcode=0x4803, payload_length=len(release_input)))
    mailbox.payloads.copy_from(bytes(release_input))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS

    # Verify only Extent 2 remains
    mailbox.set_command(MailboxCommand(command_opcode=0x4801, payload_length=len(extent_req)))
    mailbox.payloads.copy_from(bytes(extent_req))
    mailbox.set_control(
        MailboxControl(doorbell=1, mb_doorbell_interrupt=0, background_command_complete_interrupt=0)
    )
    assert mailbox.status["return_code"] == MAILBOX_RETURN_CODE.SUCCESS
    
    resp_len = mailbox.command["payload_length"]
    # Header/Footer (16) + 1 entry (40) = 56 bytes
    assert resp_len == 56
    resp_bytes = bytes(mailbox.payloads.create_shared(resp_len))
    dummy_exts = [DynamicCapacityExtentStruct()]
    resp_ext_payload = GetDynamicCapacityExtentListOutput(dummy_exts)
    resp_ext_payload.reset(resp_bytes)
    assert resp_ext_payload.returned_extent_count == 1
    assert resp_ext_payload.total_extent_count == 1
    assert resp_ext_payload.dc_extent0.start_dpa == 0x10000000
    assert resp_ext_payload.dc_extent0.length == 0x10000000
