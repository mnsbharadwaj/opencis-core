"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from asyncio import (
    StreamReader,
    StreamWriter,
    create_task,
    gather,
    Queue,
)
from dataclasses import dataclass
from enum import StrEnum, IntEnum
from typing import cast, Optional, Dict, Union, List

from opencis.util.logger import logger
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE
from opencis.util.component import RunnableComponent
from opencis.cxl.component.common import CXL_COMPONENT_TYPE
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.packet_reader import PacketReader
from opencis.cxl.transport.common import BasePacket
from opencis.cxl.transport.sideband_packets import BaseSidebandPacket
from opencis.cxl.transport.cxl_io_packets import CxlIoBasePacket
from opencis.cxl.transport.cxl_cache_packets import CxlCacheBasePacket
from opencis.cxl.transport.cxl_mem_packets import CxlMemBasePacket
from opencis.cxl.transport.packet_constants import (
    SYSTEM_PAYLOAD_TYPE,
    CXL_IO_FMT_TYPE,
    SIDEBAND_TYPES,
)

from opencis.cxl.transport.cci_packets import (
    CciMessagePacket,
    CciPayloadPacket,
    CciRequestPacket,
    CciResponsePacket,
    GetLdInfoResponsePacket,
    GetLdAllocationsResponsePacket,
    SetLdAllocationsResponsePacket,
)
from opencis.cxl.device.cxl_type3_device import CXL_T3_DEV_TYPE
from opencis.cxl.component.fmld import FMLD


@dataclass
class FifoGroup:
    cfg_space: Queue
    mmio: Queue
    cxl_mem: Queue
    cxl_cache: Queue
    cci_fifo: Optional[Queue]  # To LD, TODO: Enable later when CCI towards LD is implemented


class CXL_IO_FIFO_TYPE(IntEnum):
    CFG = 0
    MMIO = 1


class PROCESSOR_DIRECTION(StrEnum):
    HOST_TO_TARGET = "host to target"
    TARGET_TO_HOST = "target to host"


class CxlPacketProcessor(RunnableComponent):
    def __init__(
        self,
        reader: StreamReader,
        writer: StreamWriter,
        # cxl_connection for SLD & MLD
        cxl_connection: Union[CxlConnection, List[CxlConnection]],
        component_type: CXL_COMPONENT_TYPE,
        mld_config=None,
        label: Optional[str] = None,
    ):
        super().__init__(label)
        logger.info(f"CxlPacketProcessor: Constructor called with mld_config: {mld_config}")
        if mld_config:
            # num_lds_supported only exists on MultiLogicalDeviceConfig,
            # not SingleLogicalDeviceConfig
            num_lds = getattr(mld_config, "num_lds_supported", 1)
            logger.info(f"CxlPacketProcessor: mld_config.num_lds_supported = {num_lds}")
        else:
            logger.info("CxlPacketProcessor: mld_config is None")

        self._reader = PacketReader(reader, label=label)
        self._writer = writer
        self._tlp_table: Dict[int, CXL_IO_FIFO_TYPE] = {}
        self._cxl_connection = cxl_connection
        self._component_type = component_type
        self._mld_config = mld_config
        self._fmld = None
        self._cci_connection_for_fmld = None

        logger.debug(self._create_message(f"Configured for {component_type.name}"))
        if component_type in (CXL_COMPONENT_TYPE.R, CXL_COMPONENT_TYPE.DSP):
            self._incoming = FifoGroup(
                cfg_space=self._cxl_connection.cfg_fifo.target_to_host,
                mmio=self._cxl_connection.mmio_fifo.target_to_host,
                cxl_mem=self._cxl_connection.cxl_mem_fifo.target_to_host,
                cxl_cache=self._cxl_connection.cxl_cache_fifo.target_to_host,
                cci_fifo=self._cxl_connection.cci_fifo.target_to_host,
            )
            self._incoming_dir = PROCESSOR_DIRECTION.TARGET_TO_HOST
            self._outgoing = FifoGroup(
                cfg_space=self._cxl_connection.cfg_fifo.host_to_target,
                mmio=self._cxl_connection.mmio_fifo.host_to_target,
                cxl_mem=self._cxl_connection.cxl_mem_fifo.host_to_target,
                cxl_cache=self._cxl_connection.cxl_cache_fifo.host_to_target,
                cci_fifo=self._cxl_connection.cci_fifo.host_to_target,
            )
            self._outgoing_dir = PROCESSOR_DIRECTION.HOST_TO_TARGET
        elif component_type in (
            CXL_COMPONENT_TYPE.P,
            CXL_COMPONENT_TYPE.T1,
            CXL_COMPONENT_TYPE.T2,
            CXL_COMPONENT_TYPE.D2,
            CXL_COMPONENT_TYPE.USP,
        ):
            self._incoming_dir = PROCESSOR_DIRECTION.HOST_TO_TARGET
            self._outgoing_dir = PROCESSOR_DIRECTION.TARGET_TO_HOST

            # Add common FIFOs
            # For USP: cci_fifo.host_to_target carries CCI requests FROM the
            # host (e.g. CxlSimpleHost GAE commands). cci_fifo.target_to_host
            # carries CCI responses back to the host.
            usp_cci_incoming = (
                self._cxl_connection.cci_fifo.host_to_target
                if component_type in (CXL_COMPONENT_TYPE.USP, CXL_COMPONENT_TYPE.D2)
                else None
            )
            usp_cci_outgoing = (
                self._cxl_connection.cci_fifo.target_to_host
                if component_type in (CXL_COMPONENT_TYPE.USP, CXL_COMPONENT_TYPE.D2)
                else None
            )
            self._incoming = FifoGroup(
                cfg_space=self._cxl_connection.cfg_fifo.host_to_target,
                mmio=self._cxl_connection.mmio_fifo.host_to_target,
                cxl_mem=None,
                cxl_cache=None,
                cci_fifo=usp_cci_incoming,
            )

            self._outgoing = FifoGroup(
                cfg_space=self._cxl_connection.cfg_fifo.target_to_host,
                mmio=self._cxl_connection.mmio_fifo.target_to_host,
                cxl_mem=None,
                cxl_cache=None,
                cci_fifo=usp_cci_outgoing,
            )

            # Add CXL.cache and CXL.mem FIFO based on the device type
            if component_type in (
                CXL_COMPONENT_TYPE.T1,
                CXL_COMPONENT_TYPE.T2,
                CXL_COMPONENT_TYPE.USP,
            ):
                self._incoming.cxl_cache = self._cxl_connection.cxl_cache_fifo.host_to_target
                self._outgoing.cxl_cache = self._cxl_connection.cxl_cache_fifo.target_to_host

            if component_type in (
                CXL_COMPONENT_TYPE.T2,
                CXL_COMPONENT_TYPE.D2,
                CXL_COMPONENT_TYPE.USP,
            ):
                self._incoming.cxl_mem = self._cxl_connection.cxl_mem_fifo.host_to_target
                self._outgoing.cxl_mem = self._cxl_connection.cxl_mem_fifo.target_to_host
        elif component_type == CXL_COMPONENT_TYPE.LD:
            # Handle the case where _cxl_connection is a single object or a list
            if self._cxl_connection is None:
                raise Exception("CxlConnection is None - cannot initialize LD packet processor")

            if isinstance(self._cxl_connection, list):
                cxl_connections = self._cxl_connection
                self._ld_count = len(cxl_connections)
            else:
                # Single CxlConnection object - happens when ld_count=0
                # For dynamic configs with no initial LDs, start with ld_count=0
                cxl_connections = [self._cxl_connection]
                self._ld_count = 0  # Start with no LDs for dynamic configuration

            if not cxl_connections:
                raise Exception("No CxlConnection objects available for LD packet processor")

            self._cci_connection_for_fmld = cxl_connections[0]

            # Use mld_config if available, otherwise use default values
            if self._mld_config is not None:
                total_capacity = self._mld_config.total_capacity
                ld_count = self._mld_config.ld_count
                memory_sizes = self._mld_config.memory_sizes
                num_lds_supported = self._mld_config.num_lds_supported
                logger.info(
                    "CxlPacketProcessor: Using mld_config - "
                    f"num_lds_supported = {num_lds_supported}"
                )
            else:
                total_capacity = 0
                ld_count = 0
                memory_sizes = None
                num_lds_supported = 16  # Default value
                logger.info(
                    "CxlPacketProcessor: mld_config is None, "
                    f"using default num_lds_supported = {num_lds_supported}"
                )

            logger.info(
                f"CxlPacketProcessor: Creating FMLD with num_lds_supported = {num_lds_supported}"
            )
            self._fmld = FMLD(
                upstream_fifo=self._cci_connection_for_fmld.cci_fifo,
                total_capacity=total_capacity,
                dev_type=CXL_T3_DEV_TYPE.MLD,
                ld_count=ld_count,
                memory_sizes=memory_sizes,
                num_lds_supported=num_lds_supported,
            )
            self._incoming_dir = PROCESSOR_DIRECTION.HOST_TO_TARGET
            self._outgoing_dir = PROCESSOR_DIRECTION.TARGET_TO_HOST
            self._incoming = [
                FifoGroup(
                    cfg_space=cxl_conn.cfg_fifo.host_to_target,
                    mmio=cxl_conn.mmio_fifo.host_to_target,
                    cxl_mem=cxl_conn.cxl_mem_fifo.host_to_target,
                    cxl_cache=None,
                    cci_fifo=None,
                )
                for cxl_conn in cxl_connections
            ]

            self._outgoing = FifoGroup(
                cfg_space=cxl_connections[0].cfg_fifo.target_to_host,
                mmio=cxl_connections[0].mmio_fifo.target_to_host,
                cxl_mem=cxl_connections[0].cxl_mem_fifo.target_to_host,
                cxl_cache=None,
                cci_fifo=None,
            )
        else:
            raise Exception(f"Unsupported component type {component_type.name}")

    @staticmethod
    def _is_disconnection_notification(packet) -> bool:
        # CciMessagePacket and other internal packets have no system_header —
        # only wire-level BasePacket subclasses (sideband, TLP, CciPayloadPacket) do.
        # Guard here so we never crash when an internal packet reaches this check.
        if not hasattr(packet, "system_header"):
            return False
        base_packet = cast(BasePacket, packet)
        if base_packet.system_header.payload_type != SYSTEM_PAYLOAD_TYPE.SIDEBAND:
            return False
        sideband = cast(BaseSidebandPacket, packet)
        return sideband.sideband_header.type == SIDEBAND_TYPES.CONNECTION_DISCONNECTED

    def _push_tlp_table_entry(self, cxl_io_packet: CxlIoBasePacket):
        tid = cxl_io_packet.get_transaction_id()
        # Since USP and R (Root Port) are agnostic to the existence (or even the concept of)
        # MLD, the LD-ID field is considered undefined. To ensure consistent matching of TLP
        # entries, always set LD-ID to 0.
        if self._component_type in (CXL_COMPONENT_TYPE.USP, CXL_COMPONENT_TYPE.R):
            ld_id = 0
        else:
            ld_id = cxl_io_packet.tlp_prefix.ld_id
        t_index = (tid << 8) | ld_id

        if t_index in self._tlp_table:
            raise Exception(f"t_index ({t_index:02x}) already exists in the TLP table")
        if cxl_io_packet.is_cfg():
            fifo_type = CXL_IO_FIFO_TYPE.CFG
        elif cxl_io_packet.is_mmio():
            fifo_type = CXL_IO_FIFO_TYPE.MMIO
        else:
            fmt_type_str = CXL_IO_FMT_TYPE(cxl_io_packet.cxl_io_header.fmt_type)
            raise Exception(f"pushing t_index of {fmt_type_str} type is not allowed")
        self._tlp_table[t_index] = fifo_type

    def _pop_tlp_table_entry(self, cxl_io_packet: CxlIoBasePacket) -> CXL_IO_FIFO_TYPE:
        tid = cxl_io_packet.get_transaction_id()
        # Same reasoning as push function, push and pop must have same mechanism
        # for no mismatch in TLP table
        if self._component_type in (CXL_COMPONENT_TYPE.USP, CXL_COMPONENT_TYPE.R):
            ld_id = 0
        else:
            ld_id = cxl_io_packet.tlp_prefix.ld_id
        t_index = (tid << 8) | ld_id

        if t_index not in self._tlp_table:
            raise Exception(f"t_index ({t_index:02x}) is not found in the TLP table")
        fifo_type = self._tlp_table[t_index]
        del self._tlp_table[t_index]
        return fifo_type

    async def _process_incoming_packets(self):
        logger.debug(self._create_message(f"Starting {self._incoming_dir} packet processor"))
        while True:  # pylint: disable=too-many-nested-blocks
            try:
                packet = await self._reader.get_packet()
                if packet.is_cxl_io():
                    cxl_io_packet = cast(CxlIoBasePacket, packet)
                    if cxl_io_packet.is_cpl() or cxl_io_packet.is_cpld():
                        logger.debug(
                            self._create_message(
                                f"Received {self._incoming_dir} CXL.io (CPL/CPLD) packet"
                            )
                        )
                        fifo_type = self._pop_tlp_table_entry(cxl_io_packet)
                        # Add MLD
                        if self._component_type == CXL_COMPONENT_TYPE.LD:
                            ld_id = cxl_io_packet.tlp_prefix.ld_id
                            if fifo_type == CXL_IO_FIFO_TYPE.CFG:
                                await self._incoming[ld_id].cfg_space.put(cxl_io_packet)
                            else:
                                await self._incoming[ld_id].mmio.put(cxl_io_packet)
                        else:
                            if fifo_type == CXL_IO_FIFO_TYPE.CFG:
                                await self._incoming.cfg_space.put(cxl_io_packet)
                            else:
                                await self._incoming.mmio.put(cxl_io_packet)
                    elif cxl_io_packet.is_cfg():
                        logger.debug(
                            self._create_message(
                                f"Received {self._incoming_dir} CXL.io (CFG_RD/CFG_WR) packet"
                            )
                        )
                        self._push_tlp_table_entry(cxl_io_packet)
                        # Add MLD
                        if self._component_type == CXL_COMPONENT_TYPE.LD:
                            ld_id = cxl_io_packet.tlp_prefix.ld_id
                            await self._incoming[ld_id].cfg_space.put(cxl_io_packet)
                        else:
                            await self._incoming.cfg_space.put(cxl_io_packet)
                    elif cxl_io_packet.is_mmio():
                        logger.debug(
                            self._create_message(
                                f"Received {self._incoming_dir} CXL.io (MRD/MWR) packet"
                            )
                        )
                        if cxl_io_packet.is_mem_write() is False:
                            self._push_tlp_table_entry(cxl_io_packet)
                        # Add MLD
                        if self._component_type == CXL_COMPONENT_TYPE.LD:
                            ld_id = cxl_io_packet.tlp_prefix.ld_id
                            await self._incoming[ld_id].mmio.put(cxl_io_packet)
                        else:
                            await self._incoming.mmio.put(cxl_io_packet)
                    else:
                        logger.warning(self._create_message("Unexpected CXL.io packet"))
                        logger.debug(self._create_message(packet.get_pretty_string()))
                        raise Exception("Received unexpected CXL.io packet")
                elif packet.is_cxl_mem():
                    if (
                        self._component_type != CXL_COMPONENT_TYPE.LD
                        and self._incoming.cxl_mem is None
                    ):
                        logger.error(self._create_message("Got CXL.mem packet on no CXL.mem FIFO"))
                        continue
                    logger.debug(
                        self._create_message(f"Received {self._incoming_dir} CXL.mem packet")
                    )
                    cxl_mem_packet = cast(CxlMemBasePacket, packet)
                    if self._component_type == CXL_COMPONENT_TYPE.LD:
                        # Add LD routing code
                        if cxl_mem_packet.is_m2sreq():
                            ld_id = cxl_mem_packet.m2sreq_header.ld_id
                        elif cxl_mem_packet.is_m2srwd():
                            ld_id = cxl_mem_packet.m2srwd_header.ld_id
                        elif cxl_mem_packet.is_s2mndr():
                            ld_id = cxl_mem_packet.s2mndr_header.ld_id
                        elif cxl_mem_packet.is_s2mdrs():
                            ld_id = cxl_mem_packet.s2mdrs_header.ld_id
                        else:
                            logger.warning(self._create_message("Unexpected CXL.mem packet"))

                        await self._incoming[ld_id].cxl_mem.put(cxl_mem_packet)
                    else:
                        await self._incoming.cxl_mem.put(cxl_mem_packet)

                elif packet.is_cxl_cache():
                    if self._incoming.cxl_cache is None:
                        logger.error(
                            self._create_message("Got CXL.cache packet on no CXL.cache FIFO")
                        )
                        continue
                    logger.debug(
                        self._create_message(f"Received {self._incoming_dir} CXL.cache packet")
                    )
                    cxl_cache_packet = cast(CxlCacheBasePacket, packet)
                    await self._incoming.cxl_cache.put(cxl_cache_packet)
                elif packet.is_cci():
                    if self._component_type == CXL_COMPONENT_TYPE.D2:
                        # Allow D2 (e.g. GFD) to receive CCI if cci_fifo is configured
                        if self._incoming.cci_fifo is not None:
                            logger.debug(self._create_message(
                                "Received Switch→GFD CCI packet — routing to cci_fifo"
                            ))
                            if hasattr(packet, "get_cci_message"):
                                inner = packet.get_cci_message()
                                await self._incoming.cci_fifo.put(inner)
                            else:
                                await self._incoming.cci_fifo.put(packet)
                        else:
                            logger.error(
                                self._create_message("Got CCI packet on wrong device type - SLD")
                            )
                            raise Exception("Got CCI packet on wrong device type - SLD")
                    elif self._component_type == CXL_COMPONENT_TYPE.LD:
                        if self._fmld.upstream_fifo is None:
                            logger.error(self._create_message("Got CCI packet on no CCI FIFO"))
                            raise Exception("Got CCI packet on no CCI FIFO")
                        cci_packet = cast(CciRequestPacket, packet)
                        await self._fmld.upstream_fifo.host_to_target.put(cci_packet)
                    elif self._component_type == CXL_COMPONENT_TYPE.DSP:
                        if self._incoming.cci_fifo is not None:
                            logger.debug(self._create_message(
                                "Received GFD→Switch CCI response — routing to cci_fifo"
                            ))
                            if hasattr(packet, "get_cci_message"):
                                inner = packet.get_cci_message()
                                await self._incoming.cci_fifo.put(inner)
                            else:
                                await self._incoming.cci_fifo.put(packet)
                    elif self._component_type == CXL_COMPONENT_TYPE.USP:
                        # Host-direct CCI to GAE: put request into USP cci_fifo
                        # so GaeCciMailbox can pick it up.
                        if self._incoming.cci_fifo is None:
                            logger.warning(self._create_message(
                                "Got CCI packet on USP but cci_fifo not configured — dropping"
                            ))
                        else:
                            logger.debug(self._create_message(
                                "Received Host→GAE CCI packet — routing to cci_fifo"
                            ))
                            # Over TCP the packet arrives as CciPayloadPacket.
                            # Unwrap to CciMessagePacket so GaeCciMailbox (and in-process
                            # tests) always see a plain CciMessagePacket on the Queue.
                            if hasattr(packet, "get_cci_message"):
                                inner = packet.get_cci_message()
                                await self._incoming.cci_fifo.put(inner)
                            else:
                                await self._incoming.cci_fifo.put(packet)
                    elif self._component_type == CXL_COMPONENT_TYPE.R:
                        # GAE→Host CCI response arriving from the switch over TCP.
                        # CxlRootPortDevice.gae_command() is waiting on cci_fifo.target_to_host.
                        # _incoming.cci_fifo IS cci_fifo.target_to_host for R type (line 103).
                        if self._incoming.cci_fifo is not None:
                            logger.debug(self._create_message(
                                "Received GAE→Host CCI response — routing to cci_fifo.target_to_host"
                            ))
                            # Over TCP the packet arrives as CciPayloadPacket.
                            # Unwrap to CciMessagePacket so gae_command() always gets one.
                            if isinstance(packet, CciPayloadPacket):
                                inner = packet.get_cci_message()
                                await self._incoming.cci_fifo.put(inner)
                            else:
                                await self._incoming.cci_fifo.put(packet)
                        else:
                            logger.warning(self._create_message(
                                "Got CCI packet on R type but cci_fifo is None — dropping"
                            ))
                else:
                    message = f"Received unexpected {self._incoming_dir} packet"
                    logger.debug(self._create_message(message))
                    raise Exception(message)
            except Exception as e:
                logger.debug(self._create_message(str(e)))
                notification_packet = BaseSidebandPacket.create(
                    SIDEBAND_TYPES.CONNECTION_DISCONNECTED
                )
                await self._notify_outgoing_processors(notification_packet)
                break
        logger.debug(self._create_message(f"Stopped {self._incoming_dir} packet processor"))

    async def _notify_outgoing_processors(self, packet):
        await self._outgoing.cfg_space.put(packet)
        await self._outgoing.mmio.put(packet)
        if self._outgoing.cxl_mem:
            await self._outgoing.cxl_mem.put(packet)
        if self._outgoing.cxl_cache:
            await self._outgoing.cxl_cache.put(packet)
        if self._cci_connection_for_fmld:
            logger.info(self._create_message("Sending disconnection notification to FMLD CCI"))
            await self._fmld.upstream_fifo.target_to_host.put(packet)
        if self._outgoing.cci_fifo:
            logger.info(self._create_message("Sending disconnection notification to CCI"))
            await self._outgoing.cci_fifo.put(packet)
        # For USP incoming cci_fifo (GaeCciMailbox), also send sentinel
        if (
            self._component_type == CXL_COMPONENT_TYPE.USP
            and self._incoming.cci_fifo is not None
        ):
            await self._incoming.cci_fifo.put(packet)

    async def _process_outgoing_cfg_packets(self):
        logger.debug(self._create_message("Starting outgoing CFG FIFO processor"))
        while True:
            packet = await self._outgoing.cfg_space.get()
            if self._is_disconnection_notification(packet):
                break

            cxl_io_packet = cast(CxlIoBasePacket, packet)
            if cxl_io_packet.is_cpl() or cxl_io_packet.is_cpld():
                logger.debug(
                    self._create_message(f"Received {self._outgoing_dir} CXL.io (CPL/CPLD) packet")
                )
                self._pop_tlp_table_entry(cxl_io_packet)
            else:
                logger.debug(
                    self._create_message(
                        f"Received {self._outgoing_dir} CXL.io (CFG_RD/CFG_WR) packet"
                    )
                )
                self._push_tlp_table_entry(cxl_io_packet)
            self._writer.write(bytes(packet))
            await self._writer.drain()
        logger.debug(self._create_message("Stopped outgoing CFG FIFO processor"))

    async def _process_outgoing_mmio_packets(self):
        logger.debug(self._create_message("Starting outgoing MMIO FIFO processor"))
        while True:
            packet = await self._outgoing.mmio.get()
            if self._is_disconnection_notification(packet):
                break
            cxl_io_packet = cast(CxlIoBasePacket, packet)
            if cxl_io_packet.is_cpl() or cxl_io_packet.is_cpld():
                logger.debug(
                    self._create_message(f"Received {self._outgoing_dir} CXL.io (CPL/CPLD) packet")
                )
                self._pop_tlp_table_entry(cxl_io_packet)
            else:
                logger.debug(
                    self._create_message(f"Received {self._outgoing_dir} CXL.io (MRD/MWR) packet")
                )
                if cxl_io_packet.is_mem_write() is False:
                    self._push_tlp_table_entry(cxl_io_packet)
            self._writer.write(bytes(packet))
            await self._writer.drain()
        logger.debug(self._create_message("Stopped outgoing MMIO FIFO processor"))

    async def _process_outgoing_cxl_mem_packets(self):
        logger.debug(self._create_message("Starting outgoing CXL.mem FIFO processor"))
        while True:
            packet = await self._outgoing.cxl_mem.get()
            if self._is_disconnection_notification(packet):
                break
            self._writer.write(bytes(packet))
            await self._writer.drain()
        logger.debug(self._create_message("Stopped outgoing CXL.mem FIFO processor"))

    async def _process_outgoing_cxl_cache_packets(self):
        logger.debug(self._create_message("Starting outgoing CXL.cache FIFO processor"))
        while True:
            packet = await self._outgoing.cxl_cache.get()
            if self._is_disconnection_notification(packet):
                break
            self._writer.write(bytes(packet))
            await self._writer.drain()
        logger.debug(self._create_message("Stopped outgoing CXL.cache FIFO processor"))

    async def _process_outgoing_cci_packets(self):
        logger.debug(self._create_message("Starting outgoing CCI FIFO processor"))
        while True:
            if self._component_type == CXL_COMPONENT_TYPE.LD:
                packet: CciResponsePacket = await self._fmld.upstream_fifo.target_to_host.get()
                if self._is_disconnection_notification(packet):
                    logger.info(self._create_message("Stopped outgoing CCI FIFO processor"))
                    break
                opcode = packet.get_command_opcode()
                logger.info(self._create_message(f"Received CCI packet with opcode {opcode:x}"))
                if opcode == CCI_FM_API_COMMAND_OPCODE.GET_LD_INFO:
                    packet = cast(GetLdInfoResponsePacket, packet)
                    self._writer.write(bytes(packet))
                    await self._writer.drain()
                elif opcode == CCI_FM_API_COMMAND_OPCODE.GET_LD_ALLOCATIONS:
                    packet = cast(GetLdAllocationsResponsePacket, packet)
                    self._writer.write(bytes(packet))
                    await self._writer.drain()
                elif opcode == CCI_FM_API_COMMAND_OPCODE.SET_LD_ALLOCATIONS:
                    packet = cast(SetLdAllocationsResponsePacket, packet)
                    self._writer.write(bytes(packet))
                    await self._writer.drain()
                else:
                    logger.warning(self._create_message("Unsupported CCI packet"))
            elif self._component_type == CXL_COMPONENT_TYPE.DSP:
                packet = await self._outgoing.cci_fifo.get()
                if self._is_disconnection_notification(packet):
                    break
                logger.debug(self._create_message(
                    "Sending Switch→GFD CCI request packet to GFD"
                ))
                if isinstance(packet, CciMessagePacket) and not isinstance(packet, CciPayloadPacket):
                    wire_packet = CciPayloadPacket.create(packet)
                else:
                    wire_packet = packet
                self._writer.write(bytes(wire_packet))
                await self._writer.drain()
            elif self._component_type == CXL_COMPONENT_TYPE.USP:
                # GAE→Host CCI responses: read from cci_fifo.target_to_host
                # and write back across TCP to the host.
                # GaeCciMailbox puts CciMessagePacket on the Queue; we wrap it in
                # CciPayloadPacket (adds SystemHeader) so PacketReader on the R side
                # identifies it as CCI via is_cci().
                if self._outgoing.cci_fifo is None:
                    break
                packet = await self._outgoing.cci_fifo.get()
                if self._is_disconnection_notification(packet):
                    break
                logger.debug(self._create_message(
                    "Sending GAE→Host CCI response packet to host"
                ))
                if isinstance(packet, CciMessagePacket) and not isinstance(packet, CciPayloadPacket):
                    wire_packet = CciPayloadPacket.create(packet)
                else:
                    wire_packet = packet
                self._writer.write(bytes(wire_packet))
                await self._writer.drain()
            elif self._component_type == CXL_COMPONENT_TYPE.R:
                # Host→GAE CCI requests: CxlRootPortDevice.gae_command() puts
                # CciMessagePacket on cci_fifo.host_to_target. _outgoing.cci_fifo IS
                # cci_fifo.host_to_target for R type (line 111). Wrap in CciPayloadPacket
                # so PacketReader on the USP side identifies it as CCI via is_cci().
                if self._outgoing.cci_fifo is None:
                    break
                packet = await self._outgoing.cci_fifo.get()
                if self._is_disconnection_notification(packet):
                    break
                logger.debug(self._create_message(
                    "Sending Host→GAE CCI request packet to switch"
                ))
                if isinstance(packet, CciMessagePacket) and not isinstance(packet, CciPayloadPacket):
                    wire_packet = CciPayloadPacket.create(packet)
                else:
                    wire_packet = packet
                self._writer.write(bytes(wire_packet))
                await self._writer.drain()
            elif self._component_type == CXL_COMPONENT_TYPE.D2:
                if self._outgoing.cci_fifo is None:
                    break
                packet = await self._outgoing.cci_fifo.get()
                if self._is_disconnection_notification(packet):
                    break
                logger.debug(self._create_message(
                    "Sending GFD→Switch CCI response packet to switch"
                ))
                if isinstance(packet, CciMessagePacket) and not isinstance(packet, CciPayloadPacket):
                    wire_packet = CciPayloadPacket.create(packet)
                else:
                    wire_packet = packet
                self._writer.write(bytes(wire_packet))
                await self._writer.drain()
            else:
                break
        logger.debug(self._create_message("Stopped outgoing CCI FIFO processor"))

    async def _process_outgoing_packets(self):
        tasks = [
            create_task(self._process_outgoing_cfg_packets()),
            create_task(self._process_outgoing_mmio_packets()),
        ]
        if self._outgoing.cxl_mem:
            tasks.append(create_task(self._process_outgoing_cxl_mem_packets()))
        if self._outgoing.cxl_cache:
            tasks.append(create_task(self._process_outgoing_cxl_cache_packets()))
        tasks.append(create_task(self._process_outgoing_cci_packets()))
        # TODO: Enable later when CCI for LD is needed
        # if self._outgoing.cci_fifo:
        #     tasks.append(create_task(self._process_outgoing_XXX()))
        await gather(*tasks)

    async def _run(self):
        tasks = [
            create_task(self._process_incoming_packets()),
            create_task(self._process_outgoing_packets()),
        ]
        if self._fmld:
            fmld_task = [create_task(self._fmld.run())]
            await self._fmld.wait_for_ready()

        await self._change_status_to_running()

        if self._fmld:
            await gather(*fmld_task)
        await gather(*tasks)

    async def _stop(self):
        # TODO: Enable later when CCI for LD is needed
        # if self._outgoing.cci_fifo:
        #     self._fmld._upstream_fifo.target_to_host.abort()
        # await self._fmld._upstream_fifo.target_to_host.put(None)
        if self._fmld:
            task = create_task(self._fmld.stop())
            await gather(task)
        self._reader.abort()
