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
class SendLdCxlIoConfigurationRequestPayload:
    ppb_id: int
    register_num: int
    ext_register_num: int
    first_dword_byte_enable: int
    transaction_type: int  # 0: Read, 1: Write
    ld_id: int
    transaction_data: int = 0

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 8:
            raise ValueError("Data too short to parse SendLdCxlIoConfigurationRequestPayload")
        
        ppb_id = data[0]
        fields = int.from_bytes(data[1:4], "little")
        register_num = fields & 0xFF
        ext_register_num = (fields >> 8) & 0xF
        first_dword_byte_enable = (fields >> 12) & 0xF
        transaction_type = (fields >> 23) & 1

        ld_id = unpack("<H", data[4:6])[0]
        # bytes 6-7 are reserved

        transaction_data = 0
        if transaction_type == 1 and len(data) >= 12:
            transaction_data = int.from_bytes(data[8:12], "little")

        return cls(
            ppb_id=ppb_id,
            register_num=register_num,
            ext_register_num=ext_register_num,
            first_dword_byte_enable=first_dword_byte_enable,
            transaction_type=transaction_type,
            ld_id=ld_id,
            transaction_data=transaction_data,
        )

    def dump(self) -> bytes:
        data = bytearray(12)
        data[0] = self.ppb_id
        fields = (
            (self.register_num & 0xFF)
            | ((self.ext_register_num & 0xF) << 8)
            | ((self.first_dword_byte_enable & 0xF) << 12)
            | ((self.transaction_type & 1) << 23)
        )
        data[1:4] = fields.to_bytes(3, "little")
        data[4:6] = pack("<H", self.ld_id)
        if self.transaction_type == 1:
            data[8:12] = self.transaction_data.to_bytes(4, "little")
        return bytes(data)


@dataclass
class SendLdCxlIoConfigurationResponsePayload:
    return_data: int = 0

    def dump(self) -> bytes:
        return self.return_data.to_bytes(4, "little")

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 4:
            raise ValueError("Data too short to parse SendLdCxlIoConfigurationResponsePayload")
        return cls(int.from_bytes(data[:4], "little"))


class SendLdCxlIoConfigurationRequestCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SEND_LD_CXL_IO_CONFIGURATION_REQUEST

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
        
        # Check port boundaries
        port_count = self._physical_port_manager.get_port_counts()
        if port_id >= port_count:
            logger.error(self._create_message(f"Port ID {port_id} is out of bounds"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        tx_type = "Write" if request_payload.transaction_type == 1 else "Read"
        reg_addr = (request_payload.ext_register_num << 8) | request_payload.register_num

        logger.info(
            self._create_message(
                f"Simulating LD Config {tx_type} on Port {port_id}, LD {request_payload.ld_id}, "
                f"Register: {reg_addr:#05x}, ByteEnable: {request_payload.first_dword_byte_enable:#x}, "
                f"Data: {request_payload.transaction_data:#010x}"
            )
        )

        response_payload = SendLdCxlIoConfigurationResponsePayload(return_data=0)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response_payload.dump())

    @classmethod
    def create_cci_request(cls, request: SendLdCxlIoConfigurationRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> SendLdCxlIoConfigurationRequestPayload:
        return SendLdCxlIoConfigurationRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(payload: bytes) -> SendLdCxlIoConfigurationResponsePayload:
        return SendLdCxlIoConfigurationResponsePayload.parse(payload)
