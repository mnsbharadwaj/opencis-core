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
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.util.logger import logger


@dataclass
class PhysicalPortControlRequestPayload:
    ppb_id: int
    port_opcode: int

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 2:
            raise ValueError("Data is too short to parse PhysicalPortControlRequestPayload")
        ppb_id, port_opcode = unpack("<BB", data[:2])
        return cls(ppb_id, port_opcode)

    def dump(self) -> bytes:
        return pack("<BB", self.ppb_id, self.port_opcode)


class PhysicalPortControlCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.PHYSICAL_PORT_CONTROL

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = self.parse_request_payload(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        port_id = request_payload.ppb_id
        port_opcode = request_payload.port_opcode

        # Check port boundaries
        port_count = self._physical_port_manager.get_port_counts()
        if port_id >= port_count:
            logger.error(self._create_message(f"PPB ID {port_id} is out of bounds"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        if port_opcode not in (0x00, 0x01, 0x02):
            logger.error(self._create_message(f"Invalid Port Opcode {port_opcode}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # Log simulated control operation
        op_names = {0x00: "Assert PERST#", 0x01: "Deassert PERST#", 0x02: "Reset PPB"}
        logger.info(self._create_message(f"Simulating Port Opcode: {op_names[port_opcode]} on Port {port_id}"))

        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS)

    @classmethod
    def create_cci_request(cls, request: PhysicalPortControlRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> PhysicalPortControlRequestPayload:
        return PhysicalPortControlRequestPayload.parse(payload)
