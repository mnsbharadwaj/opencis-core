"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio

from opencis.cxl.transport.packet_constants import CXL_MEM_M2SBIRSP_OPCODE
from opencis.util.logger import logger
from opencis.util.component import RunnableComponent
from opencis.cxl.device.root_port_device import CxlRootPortDevice
from opencis.cxl.component.switch_connection_client import SwitchConnectionClient
from opencis.cxl.component.host_manager import HostMgrConnClient, Result
from opencis.cxl.component.common import CXL_COMPONENT_TYPE
from opencis.cxl.cci.common import CCI_GAE_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.cci.fabric_manager.gae.proxy_gfd_mgmt import (
    ProxyGfdMgmtRequestPayload,
    ProxyGfdMgmtResponsePayload,
)
from opencis.cxl.cci.fabric_manager.gae.get_proxy_thread_status import (
    GetProxyThreadStatusRequestPayload,
    GetProxyThreadStatusResponsePayload,
)
from opencis.cxl.cci.fabric_manager.gae.cancel_proxy_thread import (
    CancelProxyThreadRequestPayload,
)


class CxlSimpleHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        switch_host: str = "0.0.0.0",
        switch_port: int = 8000,
        host_host: str = "0.0.0.0",
        host_port: int = 8300,
        hm_mode: bool = True,
        test_mode: bool = False,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._test_mode = test_mode
        self._sw_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.R, host=switch_host, port=switch_port
        )
        self._methods = {
            "HOST_CXL_MEM_READ": self._cxl_mem_read,
            "HOST_CXL_MEM_WRITE": self._cxl_mem_write,
            "HOST_CXL_MEM_BIRSP": self._cxl_mem_birsp,
        }
        if hm_mode:
            self._host_mgr_conn_client = HostMgrConnClient(
                port_index=port_index, host=host_host, port=host_port, methods=self._methods
            )
        else:
            logger.debug(
                self._create_message(
                    "HostMgrConnClient is not starting because of the --no-hm arg."
                )
            )
        self._root_port_device = CxlRootPortDevice(
            downstream_connection=self._sw_conn_client.get_cxl_connection(),
            label=label,
            test_mode=self._test_mode,
        )
        self._port_index = port_index
        self._hm_mode = hm_mode

    def _is_valid_addr(self, addr: int) -> bool:
        return 0 <= addr <= self._root_port_device.get_used_hpa_size() and (addr % 0x40 == 0)

    async def _cxl_mem_read(self, addr: int) -> Result:
        logger.info(self._create_message(f"CXL.mem Read: addr=0x{addr:x}"))
        if self._is_valid_addr(addr) is False:
            logger.error(
                self._create_message(f"CXL.mem Read: Error - 0x{addr:x} is not a valid address")
            )
            return Result(f"Invalid Params: 0x{addr:x} is not a valid address")
        op_addr = addr + self._root_port_device.get_hpa_base()
        res = await self._root_port_device.cxl_mem_read(op_addr)
        return Result(res)

    async def _cxl_mem_write(self, addr: int, data: int) -> Result:
        logger.info(self._create_message(f"CXL.mem Write: addr=0x{addr:x} data=0x{data:x}"))
        if self._is_valid_addr(addr) is False:
            logger.error(
                self._create_message(f"CXL.mem Write: Error - 0x{addr:x} is not a valid address")
            )
            return Result(f"Invalid Params: 0x{addr:x} is not a valid address")
        op_addr = addr + self._root_port_device.get_hpa_base()
        res = await self._root_port_device.cxl_mem_write(op_addr, data)
        return Result(res)

    async def _cxl_mem_birsp(
        self, opcode: CXL_MEM_M2SBIRSP_OPCODE, bi_id: int = 0, bi_tag: int = 0
    ) -> Result:
        logger.info(self._create_message(f"CXL.mem BI-RSP: opcode=0x{opcode:x}"))
        res = await self._root_port_device.cxl_mem_birsp(opcode, bi_id, bi_tag)
        return Result(res)

    # ── GAE Proxy Management (Host-direct CCI to GAE on switch USP) ──────────
    # CXL 4.0 §7.7.14.10 / §7.7.14.11 / §7.7.14.12
    #
    # These methods send CCI commands directly to the GAE via the existing
    # TCP connection on port 8000 (cci_fifo channel). No FM/MCTP path needed.

    async def gae_proxy_gfd_mgmt(
        self,
        gfd_opcode: int,
        gfd_payload: bytes = b"",
        timeout: float = 5.0,
    ) -> Result:
        """
        Send Proxy GFD Mgmt Command (0x5809) to the GAE.

        Tells the GAE to forward a CCI command to the GFD on its behalf and
        return a thread_id for asynchronous status polling.

        Parameters
        ----------
        gfd_opcode : CCI opcode to forward to the GFD (e.g. 0x0001 = Identify).
        gfd_payload : Payload bytes for the inner GFD CCI command.
        timeout : Seconds to wait for GAE acknowledgement.

        Returns
        -------
        Result containing thread_id (int) or error string.
        """
        req_payload = ProxyGfdMgmtRequestPayload(
            gfd_opcode=gfd_opcode,
            gfd_payload=gfd_payload,
        ).dump()
        rc, resp_bytes = await self._root_port_device.gae_command(
            opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
            payload=req_payload,
            timeout=timeout,
        )
        if rc != CCI_RETURN_CODE.SUCCESS:
            return Result(f"GAE ProxyGfdMgmt failed: {rc.name}")
        parsed = ProxyGfdMgmtResponsePayload.parse(resp_bytes)
        logger.info(self._create_message(
            f"GAE ProxyGfdMgmt: gfd_opcode={gfd_opcode:#06x} → thread_id={parsed.thread_id}"
        ))
        return Result(parsed.thread_id)

    async def gae_get_proxy_status(
        self,
        thread_id: int,
        timeout: float = 5.0,
    ) -> Result:
        """
        Send Get Proxy Thread Status (0x580A) to the GAE.

        Returns
        -------
        Result containing dict with keys:
            thread_id, completed (bool), gfd_return_code (int),
            gfd_response_payload (bytes).
        """
        req_payload = GetProxyThreadStatusRequestPayload(
            thread_id=thread_id,
        ).dump()
        rc, resp_bytes = await self._root_port_device.gae_command(
            opcode=CCI_GAE_COMMAND_OPCODE.GET_PROXY_THREAD_STATUS,
            payload=req_payload,
            timeout=timeout,
        )
        if rc != CCI_RETURN_CODE.SUCCESS:
            return Result(f"GAE GetProxyStatus failed: {rc.name}")
        parsed = GetProxyThreadStatusResponsePayload.parse(resp_bytes)
        return Result({
            "thread_id": parsed.thread_id,
            "completed": parsed.completed,
            "gfd_return_code": parsed.gfd_return_code,
            "gfd_response_payload": list(parsed.gfd_response_payload),
        })

    async def gae_cancel_proxy(
        self,
        thread_id: int,
        timeout: float = 5.0,
    ) -> Result:
        """
        Send Cancel Proxy Thread (0x580B) to the GAE.

        Returns
        -------
        Result containing 'SUCCESS' or error string.
        """
        req_payload = CancelProxyThreadRequestPayload(
            thread_id=thread_id,
        ).dump()
        rc, _ = await self._root_port_device.gae_command(
            opcode=CCI_GAE_COMMAND_OPCODE.CANCEL_PROXY_THREAD,
            payload=req_payload,
            timeout=timeout,
        )
        if rc != CCI_RETURN_CODE.SUCCESS:
            return Result(f"GAE CancelProxy failed: {rc.name}")
        return Result("SUCCESS")

    async def _run(self):
        tasks = [
            asyncio.create_task(self._sw_conn_client.run()),
            asyncio.create_task(self._root_port_device.run()),
        ]
        if self._hm_mode:
            tasks.append(asyncio.create_task(self._host_mgr_conn_client.run()))
            await self._host_mgr_conn_client.wait_for_ready()
        await self._sw_conn_client.wait_for_ready()
        await self._root_port_device.wait_for_ready()
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._sw_conn_client.stop()),
            asyncio.create_task(self._root_port_device.stop()),
        ]
        if self._hm_mode:
            tasks.append(asyncio.create_task(self._host_mgr_conn_client.stop()))
        await asyncio.gather(*tasks)
