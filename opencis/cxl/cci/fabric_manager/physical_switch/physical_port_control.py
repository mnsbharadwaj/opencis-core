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
class PhysicalPortControlRequestPayload:
    port_id: int = 0
    port_control: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "PhysicalPortControlRequestPayload":
        if len(data) < 2:
            raise ValueError("Data is too short to parse")
        port_id = data[0]
        port_control = data[1]
        return cls(port_id=port_id, port_control=port_control)

    def dump(self) -> bytes:
        return bytes([self.port_id, self.port_control])

    def get_pretty_print(self) -> str:
        return f"- Port ID: {self.port_id}\n- Port Control: {self.port_control}"


class PhysicalPortControlCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.PHYSICAL_PORT_CONTROL

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        label: Optional[str] = None,
    ):
        super().__init__(self.OPCODE, label=label)
        self._physical_port_manager = physical_port_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            request_payload = PhysicalPortControlRequestPayload.parse(request.payload)
        except Exception:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        port_id = request_payload.port_id
        port_control = request_payload.port_control

        # Validate port ID
        if port_id >= self._physical_port_manager.get_port_counts():
            logger.warning(f"PhysicalPortControl: Invalid port ID {port_id}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        # Validate port control action
        # 0 = Assert PERST, 1 = Deassert PERST, 2 = Reset PPB, 3 = Disable, 4 = Enable
        if port_control not in (0, 1, 2, 3, 4):
            logger.warning(f"PhysicalPortControl: Invalid port control {port_control}")
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        logger.info(f"PhysicalPortControl: Port {port_id} control action {port_control}")
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
