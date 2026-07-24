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
class SendPpbCxlIoConfigurationRequestPayload:
    ppb_id: int
    register_num: int
    ext_register_num: int
    first_dword_byte_enable: int
    transaction_type: int  # 0: Read, 1: Write
    transaction_data: int = 0

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 4:
            raise ValueError("Data too short to parse SendPpbCxlIoConfigurationRequestPayload")
        
        ppb_id = data[0]
        # Bytes 1-3 contain register and transaction metadata:
        # Bits[7:0]: Register Number
        # Bits[11:8]: Extended Register Number
        # Bits[15:12]: First Dword Byte Enable
        # Bits[22:16]: Reserved
        # Bit[23]: Transaction Type (0: Read, 1: Write)
        fields = int.from_bytes(data[1:4], "little")
        register_num = fields & 0xFF
        ext_register_num = (fields >> 8) & 0xF
        first_dword_byte_enable = (fields >> 12) & 0xF
        transaction_type = (fields >> 23) & 1

        transaction_data = 0
        if transaction_type == 1 and len(data) >= 8:
            transaction_data = int.from_bytes(data[4:8], "little")

        return cls(
            ppb_id=ppb_id,
            register_num=register_num,
            ext_register_num=ext_register_num,
            first_dword_byte_enable=first_dword_byte_enable,
            transaction_type=transaction_type,
            transaction_data=transaction_data,
        )

    def dump(self) -> bytes:
        data = bytearray(8)
        data[0] = self.ppb_id
        fields = (
            (self.register_num & 0xFF)
            | ((self.ext_register_num & 0xF) << 8)
            | ((self.first_dword_byte_enable & 0xF) << 12)
            | ((self.transaction_type & 1) << 23)
        )
        data[1:4] = fields.to_bytes(3, "little")
        if self.transaction_type == 1:
            data[4:8] = self.transaction_data.to_bytes(4, "little")
        return bytes(data)


@dataclass
class SendPpbCxlIoConfigurationResponsePayload:
    return_data: int = 0

    def dump(self) -> bytes:
        return self.return_data.to_bytes(4, "little")

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 4:
            raise ValueError("Data too short to parse SendPpbCxlIoConfigurationResponsePayload")
        return cls(int.from_bytes(data[:4], "little"))


class SendPpbCxlIoConfigurationRequestCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SEND_PPB_CXL_IO_CONFIGURATION_REQUEST

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
            logger.error(self._create_message(f"PPB ID {port_id} is out of bounds"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        tx_type = "Write" if request_payload.transaction_type == 1 else "Read"
        reg_addr = (request_payload.ext_register_num << 8) | request_payload.register_num

        logger.info(
            self._create_message(
                f"Simulating PPB Config {tx_type} on PPB {port_id}, Register: {reg_addr:#05x}, "
                f"ByteEnable: {request_payload.first_dword_byte_enable:#x}, "
                f"Data: {request_payload.transaction_data:#010x}"
            )
        )

        response_payload = SendPpbCxlIoConfigurationResponsePayload(return_data=0)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response_payload.dump())

    @classmethod
    def create_cci_request(cls, request: SendPpbCxlIoConfigurationRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> SendPpbCxlIoConfigurationRequestPayload:
        return SendPpbCxlIoConfigurationRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(payload: bytes) -> SendPpbCxlIoConfigurationResponsePayload:
        return SendPpbCxlIoConfigurationResponsePayload.parse(payload)
