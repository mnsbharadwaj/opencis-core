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
class SendLdCxlIoMemoryRequestPayload:
    port_id: int
    first_dword_byte_enable: int
    last_dword_byte_enable: int
    transaction_type: int  # 0: Read, 1: Write
    ld_id: int
    transaction_length: int
    transaction_address: int
    transaction_data: bytes = b""

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 16:
            raise ValueError("Data too short to parse SendLdCxlIoMemoryRequestPayload")
        
        port_id = data[0]
        fields = int.from_bytes(data[1:4], "little")
        first_dword_byte_enable = (fields >> 12) & 0xF
        last_dword_byte_enable = (fields >> 16) & 0xF
        transaction_type = (fields >> 23) & 1

        ld_id = unpack("<H", data[4:6])[0]
        transaction_length = unpack("<H", data[6:8])[0]
        transaction_address = unpack("<Q", data[8:16])[0]

        transaction_data = b""
        if transaction_type == 1:
            transaction_data = data[16 : 16 + transaction_length]

        return cls(
            port_id=port_id,
            first_dword_byte_enable=first_dword_byte_enable,
            last_dword_byte_enable=last_dword_byte_enable,
            transaction_type=transaction_type,
            ld_id=ld_id,
            transaction_length=transaction_length,
            transaction_address=transaction_address,
            transaction_data=transaction_data,
        )

    def dump(self) -> bytes:
        header_len = 16
        if self.transaction_type == 1:
            data = bytearray(header_len + len(self.transaction_data))
            data[16:] = self.transaction_data
        else:
            data = bytearray(header_len)

        data[0] = self.port_id
        fields = (
            ((self.first_dword_byte_enable & 0xF) << 12)
            | ((self.last_dword_byte_enable & 0xF) << 16)
            | ((self.transaction_type & 1) << 23)
        )
        data[1:4] = fields.to_bytes(3, "little")
        data[4:6] = pack("<H", self.ld_id)
        data[6:8] = pack("<H", self.transaction_length)
        data[8:16] = pack("<Q", self.transaction_address)
        return bytes(data)


@dataclass
class SendLdCxlIoMemoryResponsePayload:
    return_size: int
    return_data: bytes = b""

    def dump(self) -> bytes:
        data = bytearray(4 + len(self.return_data))
        data[0:2] = pack("<H", self.return_size)
        # bytes 2-3 are reserved
        data[4:] = self.return_data
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 4:
            raise ValueError("Data too short to parse SendLdCxlIoMemoryResponsePayload")
        return_size = unpack("<H", data[:2])[0]
        return_data = data[4 : 4 + return_size]
        return cls(return_size, return_data)


class SendLdCxlIoMemoryRequestCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SEND_LD_CXL_IO_MEMORY_REQUEST

    def __init__(self, physical_port_manager: PhysicalPortManager):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = self.parse_request_payload(request.payload)
        except ValueError as e:
            logger.error(self._create_message(f"Payload parsing error: {e}"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        port_id = request_payload.port_id
        
        # Check port boundaries
        port_count = self._physical_port_manager.get_port_counts()
        if port_id >= port_count:
            logger.error(self._create_message(f"Port ID {port_id} is out of bounds"))
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        tx_type = "Write" if request_payload.transaction_type == 1 else "Read"

        logger.info(
            self._create_message(
                f"Simulating LD Memory {tx_type} on Port {port_id}, LD {request_payload.ld_id}, "
                f"Address: {request_payload.transaction_address:#018x}, "
                f"Length: {request_payload.transaction_length} bytes"
            )
        )

        # Simulate read response with zeroed bytes
        return_data = b""
        if request_payload.transaction_type == 0:
            return_data = bytes(request_payload.transaction_length)

        response_payload = SendLdCxlIoMemoryResponsePayload(
            return_size=request_payload.transaction_length, return_data=return_data
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response_payload.dump())

    @classmethod
    def create_cci_request(cls, request: SendLdCxlIoMemoryRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> SendLdCxlIoMemoryRequestPayload:
        return SendLdCxlIoMemoryRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(payload: bytes) -> SendLdCxlIoMemoryResponsePayload:
        return SendLdCxlIoMemoryResponsePayload.parse(payload)
