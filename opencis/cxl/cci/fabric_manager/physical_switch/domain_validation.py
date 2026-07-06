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
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.util.logger import logger

# Module-level storage to simulate secret values (UUIDs) for VCSs
_vcs_secret_values = {}


@dataclass
class GetDomainValidationSvStateResponsePayload:
    secret_value_state: int  # 00h: Not set, 01h: Set

    def dump(self) -> bytes:
        return pack("<B", self.secret_value_state)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 1:
            raise ValueError("Data too short")
        return cls(data[0])


@dataclass
class SetDomainValidationSvRequestPayload:
    secret_value: bytes  # 16 bytes UUID

    def dump(self) -> bytes:
        return self.secret_value

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 16:
            raise ValueError("UUID must be 16 bytes")
        return cls(data[:16])


@dataclass
class GetVcsDomainValidationSvStateRequestPayload:
    vcs_id: int

    def dump(self) -> bytes:
        return pack("<B", self.vcs_id)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 1:
            raise ValueError("Data too short")
        return cls(data[0])


@dataclass
class GetVcsDomainValidationSvStateResponsePayload:
    secret_value_state: int  # 00h: Not set, 01h: Set

    def dump(self) -> bytes:
        return pack("<B", self.secret_value_state)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 1:
            raise ValueError("Data too short")
        return cls(data[0])


@dataclass
class GetDomainValidationSvRequestPayload:
    vcs_id: int

    def dump(self) -> bytes:
        return pack("<B", self.vcs_id)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 1:
            raise ValueError("Data too short")
        return cls(data[0])


@dataclass
class GetDomainValidationSvResponsePayload:
    secret_value: bytes  # 16 bytes UUID

    def dump(self) -> bytes:
        return self.secret_value

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 16:
            raise ValueError("UUID must be 16 bytes")
        return cls(data[:16])


class GetDomainValidationSvStateCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_DOMAIN_VALIDATION_SV_STATE

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        # Default global validation state is Set (0x01) if at least one VCS has it, or 0x00
        has_any = 0x01 if len(_vcs_secret_values) > 0 else 0x00
        response = GetDomainValidationSvStateResponsePayload(secret_value_state=has_any)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class SetDomainValidationSvCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_DOMAIN_VALIDATION_SV

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = SetDomainValidationSvRequestPayload.parse(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # In a real switch, this sets the SV of the active VCS of the host.
        # We'll associate it with VCS 0 for simulation.
        vcs_id = 0
        if vcs_id in _vcs_secret_values:
            # Spec: "This command will fail with Invalid Input if it is called more than once."
            logger.warning(self._create_message(f"Secret Value already set for VCS {vcs_id}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        _vcs_secret_values[vcs_id] = request_payload.secret_value
        logger.info(self._create_message(f"Set Secret Value for VCS {vcs_id}: {UUID(bytes=request_payload.secret_value)}"))
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)


class GetVcsDomainValidationSvStateCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_VCS_DOMAIN_VALIDATION_SV_STATE

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = GetVcsDomainValidationSvStateRequestPayload.parse(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vcs_id = request_payload.vcs_id
        vcs_count = self._virtual_switch_manager.get_virtual_switch_counts()
        if vcs_id >= vcs_count:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        state = 0x01 if vcs_id in _vcs_secret_values else 0x00
        response = GetVcsDomainValidationSvStateResponsePayload(secret_value_state=state)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class GetDomainValidationSvCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_DOMAIN_VALIDATION_SV

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = GetDomainValidationSvRequestPayload.parse(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vcs_id = request_payload.vcs_id
        vcs_count = self._virtual_switch_manager.get_virtual_switch_counts()
        if vcs_id >= vcs_count:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        if vcs_id not in _vcs_secret_values:
            # SV not set yet
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        response = GetDomainValidationSvResponsePayload(secret_value=_vcs_secret_values[vcs_id])
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())
