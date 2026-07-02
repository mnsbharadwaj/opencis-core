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
from opencis.cxl.component.physical_port_manager import PhysicalPortManager
from opencis.util.logger import logger


@dataclass
class SendPpbCxlIoConfigRequestPayload:
    port_id: int = 0
    register_number: int = 0
    ext_register_number: int = 0
    first_dword_byte_enable: int = 0
    last_dword_byte_enable: int = 0
    request_type: int = 0
    data: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "SendPpbCxlIoConfigRequestPayload":
        if len(data) < 16:
            raise ValueError("Data is too short to parse")
        port_id = data[0]
        register_number = int.from_bytes(data[2:4], "little")
        ext_register_number = data[4]
        first_dword_byte_enable = data[8]
        last_dword_byte_enable = data[9]
        request_type = data[10]
        write_data = int.from_bytes(data[12:16], "little")
        return cls(
            port_id=port_id,
            register_number=register_number,
            ext_register_number=ext_register_number,
            first_dword_byte_enable=first_dword_byte_enable,
            last_dword_byte_enable=last_dword_byte_enable,
            request_type=request_type,
            data=write_data,
        )

    def dump(self) -> bytes:
        data = bytearray(16)
        data[0] = self.port_id
        data[2:4] = self.register_number.to_bytes(2, "little")
        data[4] = self.ext_register_number
        data[8] = self.first_dword_byte_enable
        data[9] = self.last_dword_byte_enable
        data[10] = self.request_type
        data[12:16] = self.data.to_bytes(4, "little")
        return bytes(data)


@dataclass
class SendPpbCxlIoConfigResponsePayload:
    completion_status: int = 0
    data: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "SendPpbCxlIoConfigResponsePayload":
        if len(data) < 8:
            raise ValueError("Data is too short to parse")
        completion_status = data[0]
        read_data = int.from_bytes(data[4:8], "little")
        return cls(completion_status=completion_status, data=read_data)

    def dump(self) -> bytes:
        data = bytearray(8)
        data[0] = self.completion_status
        data[4:8] = self.data.to_bytes(4, "little")
        return bytes(data)


class SendPpbCxlIoConfigurationRequestCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SEND_PPB_CXL_IO_CONFIGURATION_REQUEST

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        label: Optional[str] = None,
    ):
        super().__init__(self.OPCODE, label=label)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = SendPpbCxlIoConfigRequestPayload.parse(request.payload)
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        port_id = request_payload.port_id
        if port_id >= self._physical_port_manager.get_port_counts():
            logger.warning(f"SendPpbCxlIoConfigurationRequest: Invalid port ID {port_id}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        port_device = self._physical_port_manager.get_port_device(port_id)
        
        # Check if port_device has pci_registers
        if not hasattr(port_device, "_pci_registers") or 0 not in port_device._pci_registers:
            resp = SendPpbCxlIoConfigResponsePayload(completion_status=0, data=0)
            return CciResponse(payload=resp.dump())
            
        pci_registers = port_device._pci_registers[0]

        # Calculate offset and size
        cfg_addr = request_payload.register_number | (request_payload.ext_register_number << 8)
        
        # Align/shift based on byte enables
        be = request_payload.first_dword_byte_enable & 0xF
        if be == 0:
            size = 4
            offset_shift = 0
        else:
            first_bit = 0
            for i in range(4):
                if (be >> i) & 1:
                    first_bit = i
                    break
            last_bit = 3
            for i in range(3, -1, -1):
                if (be >> i) & 1:
                    last_bit = i
                    break
            size = last_bit - first_bit + 1
            offset_shift = first_bit
        
        cfg_addr = cfg_addr + offset_shift

        if request_payload.request_type == 0:  # Read
            try:
                val = pci_registers.read_bytes(cfg_addr, cfg_addr + size - 1)
            except Exception:
                val = 0
            resp_payload = SendPpbCxlIoConfigResponsePayload(completion_status=0, data=val)
        else:  # Write
            try:
                pci_registers.write_bytes(cfg_addr, cfg_addr + size - 1, request_payload.data)
            except Exception:
                pass
            resp_payload = SendPpbCxlIoConfigResponsePayload(completion_status=0, data=0)

        return CciResponse(payload=resp_payload.dump())

    @classmethod
    def create_cci_request(cls, request: SendPpbCxlIoConfigRequestPayload) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        cci_request.payload = request.dump()
        return cci_request

    @staticmethod
    def parse_request_payload(payload: bytes) -> SendPpbCxlIoConfigRequestPayload:
        return SendPpbCxlIoConfigRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(payload: bytes) -> SendPpbCxlIoConfigResponsePayload:
        return SendPpbCxlIoConfigResponsePayload.parse(payload)
