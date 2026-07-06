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

# Module-level storage to simulate QoS settings
_qos_telemetry_control = 0x00
_qos_egress_moderate_pct = 10
_qos_egress_severe_pct = 25
_qos_backpressure_sample_interval = 8
_qos_req_cmp_basis = 0
_qos_completion_collection_interval = 64

_qos_allocated_bw = {}  # ld_id: allocation_fraction
_qos_bw_limit = {}      # ld_id: limit_fraction


@dataclass
class QosControlPayload:
    qos_telemetry_control: int
    egress_moderate_pct: int
    egress_severe_pct: int
    backpressure_sample_interval: int
    req_cmp_basis: int
    completion_collection_interval: int

    def dump(self) -> bytes:
        data = bytearray(7)
        data[0] = self.qos_telemetry_control
        data[1] = self.egress_moderate_pct
        data[2] = self.egress_severe_pct
        data[3] = self.backpressure_sample_interval
        data[4:6] = pack("<H", self.req_cmp_basis)
        data[6] = self.completion_collection_interval
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 7:
            raise ValueError("Data too short to parse QosControlPayload")
        qos_telemetry_control = data[0]
        egress_moderate_pct = data[1]
        egress_severe_pct = data[2]
        backpressure_sample_interval = data[3]
        req_cmp_basis = unpack("<H", data[4:6])[0]
        completion_collection_interval = data[6]
        return cls(
            qos_telemetry_control,
            egress_moderate_pct,
            egress_severe_pct,
            backpressure_sample_interval,
            req_cmp_basis,
            completion_collection_interval,
        )


@dataclass
class GetQosStatusResponsePayload:
    backpressure_avg_pct: int

    def dump(self) -> bytes:
        return pack("<B", self.backpressure_avg_pct)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 1:
            raise ValueError("Data too short")
        return cls(data[0])


@dataclass
class QosFractionRequestPayload:
    num_lds: int
    start_ld_id: int

    def dump(self) -> bytes:
        return pack("<BB", self.num_lds, self.start_ld_id)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 2:
            raise ValueError("Data too short")
        return cls(data[0], data[1])


@dataclass
class QosFractionResponsePayload:
    num_lds: int
    start_ld_id: int
    fractions: bytes  # 1 byte per LD

    def dump(self) -> bytes:
        data = bytearray(2 + len(self.fractions))
        data[0] = self.num_lds
        data[1] = self.start_ld_id
        data[2:] = self.fractions
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 2:
            raise ValueError("Data too short")
        num_lds = data[0]
        start_ld_id = data[1]
        fractions = data[2 : 2 + num_lds]
        return cls(num_lds, start_ld_id, fractions)


class GetQosControlCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_QOS_CONTROL

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        response = QosControlPayload(
            qos_telemetry_control=_qos_telemetry_control,
            egress_moderate_pct=_qos_egress_moderate_pct,
            egress_severe_pct=_qos_egress_severe_pct,
            backpressure_sample_interval=_qos_backpressure_sample_interval,
            req_cmp_basis=_qos_req_cmp_basis,
            completion_collection_interval=_qos_completion_collection_interval,
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class SetQosControlCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_QOS_CONTROL

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        global _qos_telemetry_control, _qos_egress_moderate_pct, _qos_egress_severe_pct
        global _qos_backpressure_sample_interval, _qos_req_cmp_basis, _qos_completion_collection_interval

        try:
            payload = QosControlPayload.parse(request.payload)
        except ValueError as e:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        _qos_telemetry_control = payload.qos_telemetry_control
        _qos_egress_moderate_pct = payload.egress_moderate_pct
        _qos_egress_severe_pct = payload.egress_severe_pct
        _qos_backpressure_sample_interval = payload.backpressure_sample_interval
        _qos_req_cmp_basis = payload.req_cmp_basis
        _qos_completion_collection_interval = payload.completion_collection_interval

        logger.info(self._create_message("Updated QoS Control Configuration"))
        response = payload
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class GetQosStatusCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_QOS_STATUS

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        # Simulate backpressure average percentage as 5%
        response = GetQosStatusResponsePayload(backpressure_avg_pct=5)
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class GetQosAllocatedBwCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_QOS_ALLOCATED_BW

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = QosFractionRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        fractions = bytearray(req.num_lds)
        for i in range(req.num_lds):
            ld_id = req.start_ld_id + i
            fractions[i] = _qos_allocated_bw.get(ld_id, 0)

        response = QosFractionResponsePayload(
            num_lds=req.num_lds, start_ld_id=req.start_ld_id, fractions=bytes(fractions)
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class SetQosAllocatedBwCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_QOS_ALLOCATED_BW

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = QosFractionResponsePayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        for i in range(req.num_lds):
            ld_id = req.start_ld_id + i
            _qos_allocated_bw[ld_id] = req.fractions[i]

        logger.info(self._create_message(f"Set QoS Allocated BW fractions: {list(req.fractions)}"))
        response = req
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class GetQosBwLimitCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_QOS_BW_LIMIT

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = QosFractionRequestPayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        fractions = bytearray(req.num_lds)
        for i in range(req.num_lds):
            ld_id = req.start_ld_id + i
            fractions[i] = _qos_bw_limit.get(ld_id, 0)

        response = QosFractionResponsePayload(
            num_lds=req.num_lds, start_ld_id=req.start_ld_id, fractions=bytes(fractions)
        )
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())


class SetQosBwLimitCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_QOS_BW_LIMIT

    def __init__(self, virtual_switch_manager: VirtualSwitchManager):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        try:
            req = QosFractionResponsePayload.parse(request.payload)
        except ValueError:
            return CciResponse(return_code=CCI_RETURN_CODE.INVALID_INPUT)

        for i in range(req.num_lds):
            ld_id = req.start_ld_id + i
            _qos_bw_limit[ld_id] = req.fractions[i]

        logger.info(self._create_message(f"Set QoS BW Limit fractions: {list(req.fractions)}"))
        response = req
        return CciResponse(return_code=CCI_RETURN_CODE.SUCCESS, payload=response.dump())
