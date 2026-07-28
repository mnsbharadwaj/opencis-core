"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from dataclasses import dataclass, field
from struct import pack, unpack
from typing import TypedDict

from opencis.util.logger import logger
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.component.cci_executor import (
    CciForegroundCommand,
    CciRequest,
    CciResponse,
)


class GetLdInfoResponsePayloadDict(TypedDict):
    memorySize: int
    ldCount: int
    qosTelemetryCapability: int


@dataclass
class GetLdInfoResponsePayload:
    """Get LD Info Response Payload per CXL Spec (opcode 5400h).

    Wire format (11 bytes total):
        Bytes [0:7]  - Memory Size (8 bytes, LE uint64)
        Bytes [8:9]  - LD Count (2 bytes, LE uint16)
        Byte  [10]   - QoS Telemetry Capability (1 byte)
    """

    memory_size: int = field(default=0)  # 8 bytes - total memory across all LDs
    ld_count: int = field(default=0)  # 2 bytes - number of LDs supported
    qos_telemetry_capability: int = field(default=0)  # 1 byte

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < 11:
            raise ValueError("Data provided is too short to parse.")

        memory_size = unpack("<Q", data[:8])[0]
        ld_count = unpack("<H", data[8:10])[0]
        qos_telemetry_capability = data[10]
        return cls(memory_size, ld_count, qos_telemetry_capability)

    def dump(self):
        data = bytearray(11)
        data[:8] = pack("<Q", self.memory_size)
        data[8:10] = pack("<H", self.ld_count)
        data[10] = self.qos_telemetry_capability
        return bytes(data)

    def get_pretty_print(self):
        return (
            f"- Memory Size: {self.memory_size}\n"
            f"- LD Count: {self.ld_count}\n"
            f"- QoS Telemetry Capability: {self.qos_telemetry_capability}\n"
        )

    def to_dict(self) -> GetLdInfoResponsePayloadDict:
        return {
            "memorySize": self.memory_size,
            "ldCount": self.ld_count,
            "qosTelemetryCapability": self.qos_telemetry_capability,
        }


class GetLdInfoCommand(CciForegroundCommand):
    """Get LD Info (opcode 5400h) — CXL Spec MLD Component Command Set.

    Request: No payload.
    Response: GetLdInfoResponsePayload (11 bytes).

    The command returns the total memory size across all configured LDs,
    the number of LDs supported, and the QoS telemetry capability.
    """

    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_LD_INFO

    def __init__(self, virtual_switch_manager=None):
        super().__init__(self.OPCODE)
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        # Query the virtual switch manager for MLD configuration info
        memory_size = 0
        ld_count = 0
        qos_telemetry_capability = 0  # Not supported in emulation

        if self._virtual_switch_manager is not None:
            try:
                # Get device configs from the virtual switch manager
                device_configs = None
                if hasattr(self._virtual_switch_manager, "_device_configs"):
                    device_configs = self._virtual_switch_manager._device_configs
                elif hasattr(self._virtual_switch_manager, "get_device_configs"):
                    device_configs = self._virtual_switch_manager.get_device_configs()

                if device_configs:
                    from opencis.cxl.device.config.logical_device import (
                        MultiLogicalDeviceConfig,
                    )

                    # Sum up memory across all MLD configs
                    for config in device_configs:
                        if isinstance(config, MultiLogicalDeviceConfig):
                            memory_size += config.total_capacity
                            ld_count += config.ld_count
                            break  # Use first MLD config found
            except Exception as e:
                logger.warning(
                    f"[GetLdInfoCommand] Could not query MLD config: {e}"
                )

        logger.info(
            f"[GetLdInfoCommand] Returning: memory_size={memory_size}, "
            f"ld_count={ld_count}, qos_telemetry_capability={qos_telemetry_capability}"
        )

        response_payload = GetLdInfoResponsePayload(
            memory_size=memory_size,
            ld_count=ld_count,
            qos_telemetry_capability=qos_telemetry_capability,
        )

        return CciResponse(
            return_code=CCI_RETURN_CODE.SUCCESS,
            payload=response_payload.dump(),
            vendor_specific_status=0,
            bo_flag=False,
        )

    @classmethod
    def create_cci_request(cls) -> CciRequest:
        cci_request = CciRequest()
        cci_request.opcode = cls.OPCODE
        # Get LD Info request has no payload per CXL spec
        return cci_request

    @classmethod
    def parse_response_payload(cls, payload: bytes) -> GetLdInfoResponsePayload:
        return GetLdInfoResponsePayload.parse(payload)
