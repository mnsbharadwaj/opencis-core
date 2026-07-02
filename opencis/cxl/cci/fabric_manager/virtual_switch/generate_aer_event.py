"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
from typing import Optional

from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencis.util.logger import logger


@dataclass
class GenerateAerEventRequestPayload:
    vcs_id: int = 0
    vppb_id: int = 0
    error_type: int = 0
    aer_error_status: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "GenerateAerEventRequestPayload":
        if len(data) < 8:
            raise ValueError("Data is too short to parse")
        vcs_id = data[0]
        vppb_id = data[1]
        error_type = data[2]
        aer_error_status = int.from_bytes(data[4:8], "little")
        return cls(
            vcs_id=vcs_id,
            vppb_id=vppb_id,
            error_type=error_type,
            aer_error_status=aer_error_status,
        )

    def dump(self) -> bytes:
        data = bytearray(8)
        data[0] = self.vcs_id
        data[1] = self.vppb_id
        data[2] = self.error_type
        data[4:8] = self.aer_error_status.to_bytes(4, "little")
        return bytes(data)

    def get_pretty_print(self) -> str:
        return (
            f"- VCS ID: {self.vcs_id}\n"
            f"- vPPB ID: {self.vppb_id}\n"
            f"- Error Type: {self.error_type}\n"
            f"- AER Error Status: 0x{self.aer_error_status:x}"
        )


class GenerateAerEventCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GENERATE_AER_EVENT

    def __init__(
        self,
        virtual_switch_manager: VirtualSwitchManager,
        label: Optional[str] = None,
    ):
        super().__init__(self.OPCODE, label=label)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = GenerateAerEventRequestPayload.parse(request.payload)
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vcs_id = request_payload.vcs_id
        vppb_id = request_payload.vppb_id
        error_type = request_payload.error_type
        aer_error_status = request_payload.aer_error_status

        # Validate VCS count
        if vcs_id >= self._virtual_switch_manager.get_virtual_switch_counts():
            logger.warning(f"GenerateAerEvent: Invalid VCS ID {vcs_id}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vcs = self._virtual_switch_manager.get_virtual_switch(vcs_id)
        # Validate vPPB count
        if vppb_id >= vcs.get_vppb_counts():
            logger.warning(f"GenerateAerEvent: Invalid vPPB ID {vppb_id}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # Validate Error Type
        if error_type not in (0, 1, 2):
            logger.warning(f"GenerateAerEvent: Invalid Error Type {error_type}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        logger.info(
            f"GenerateAerEvent: VCS {vcs_id}, vPPB {vppb_id}, Error Type {error_type}, "
            f"Status 0x{aer_error_status:x}"
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)

    @classmethod
    def create_cci_request(cls, request: GenerateAerEventRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> GenerateAerEventRequestPayload:
        return GenerateAerEventRequestPayload.parse(payload)
