"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass
from struct import unpack, pack

from opencis.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.util.logger import logger


@dataclass
class GenerateAerEventRequestPayload:
    vcs_id: int
    vppb_instance: int
    aer_error: int
    aer_header: bytes  # 32 bytes

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 40:
            raise ValueError("Data too short to parse GenerateAerEventRequestPayload")
        vcs_id, vppb_instance = unpack("<BB", data[:2])
        # bytes 2-3 are reserved
        aer_error = unpack("<I", data[4:8])[0]
        aer_header = data[8:40]
        return cls(vcs_id, vppb_instance, aer_error, aer_header)

    def dump(self) -> bytes:
        data = bytearray(40)
        data[0] = self.vcs_id
        data[1] = self.vppb_instance
        data[4:8] = pack("<I", self.aer_error)
        data[8:40] = self.aer_header
        return bytes(data)


class GenerateAerEventCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GENERATE_AER_EVENT

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = self.parse_request_payload(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vcs_id = request_payload.vcs_id
        vcs_count = self._virtual_switch_manager.get_virtual_switch_counts()
        if vcs_id >= vcs_count:
            logger.error(self._create_message(f"VCS ID {vcs_id} is out of bounds"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        vppb_instance = request_payload.vppb_instance
        severity = "Uncorrectable" if (request_payload.aer_error & 0x80000000) != 0 else "Correctable"
        err_bit = request_payload.aer_error & 0x1F

        logger.info(
            self._create_message(
                f"Simulating AER Event on VCS {vcs_id}, vPPB Instance {vppb_instance}, "
                f"Severity: {severity}, Error Status Bit: {err_bit}, Header: {request_payload.aer_header.hex()}"
            )
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
