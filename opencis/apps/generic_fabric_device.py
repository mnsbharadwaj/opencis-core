"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

GFD Application Entry Point
-----------------------------
Runs a single CXL Generic Fabric Device that connects to a PBR switch DSP
port via TCP.

Usage (CLI / programmatic)::

    from opencis.apps.generic_fabric_device import GenericFabricDevice

    gfd = GenericFabricDevice(
        host="127.0.0.1",
        port=8000,
        port_index=1,          # DSP port on the PBR switch
        serial_number="0000000000000001",
    )
    await gfd.run()
"""

from asyncio import gather, create_task
from typing import Optional

from opencis.util.component import RunnableComponent
from opencis.cxl.device.cxl_gfd_device import CxlGfdDevice
from opencis.cxl.component.switch_connection_client import SwitchConnectionClient
from opencis.cxl.component.common import CXL_COMPONENT_TYPE


class GenericFabricDevice(RunnableComponent):
    """
    Runnable wrapper that:
      1. Opens a TCP connection to the PBR switch DSP port
         (via ``SwitchConnectionClient``).
      2. Starts the ``CxlGfdDevice`` data plane.

    Parameters
    ----------
    host:
        Hostname / IP of the CXL switch (default ``"0.0.0.0"``).
    port:
        TCP port of the CXL switch (default ``8000``).
    port_index:
        DSP port number to connect to (default ``1``).
    serial_number:
        16-hex-digit serial number string (default ``"0000000000000001"``).
    test_mode:
        If ``True``, ``cxl_connection`` must be supplied and no TCP connection
        is opened.  Useful for in-process unit tests.
    cxl_connection:
        Pre-built ``CxlConnection`` object (test mode only).
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        port_index: int = 1,
        serial_number: str = "0000000000000001",
        test_mode: bool = False,
        cxl_connection=None,
    ):
        """Initialize the GenericFabricDevice application wrapper.

        Creates a SwitchConnectionClient (for TCP connectivity) and a
        CxlGfdDevice (the actual CCI-mailbox device).  In test mode the
        TCP layer is skipped and a pre-built CxlConnection is used instead.

        Args:
            host: Hostname or IP address of the CXL PBR switch.
            port: TCP port number of the CXL PBR switch.
            port_index: DSP port index on the PBR switch to connect to.
            serial_number: 16-hex-digit serial number string for the GFD identity.
            test_mode: If True, skip TCP; cxl_connection must be provided.
            cxl_connection: Pre-built CxlConnection object (required in test mode,
                must be None in non-test mode).

        Raises:
            AssertionError: If test_mode is True but cxl_connection is None,
                or if test_mode is False but cxl_connection is provided.
        """
        label = f"GFD:Port{port_index}"
        super().__init__(label)

        self._test_mode = test_mode

        assert not test_mode or cxl_connection is not None, (
            "cxl_connection must be provided in test mode"
        )
        assert test_mode or cxl_connection is None, (
            "cxl_connection must not be provided in non-test mode"
        )

        if cxl_connection is not None:
            self._cxl_connection = cxl_connection
        else:
            # CXL_COMPONENT_TYPE.D2 = generic downstream device
            self._sw_conn_client = SwitchConnectionClient(
                port_index, CXL_COMPONENT_TYPE.D2, host=host, port=port
            )
            self._cxl_connection = self._sw_conn_client.get_cxl_connection()

        self._gfd_device = CxlGfdDevice(
            transport_connection=self._cxl_connection,
            port_index=port_index,
            serial_number=serial_number,
            label=label,
        )

    # ── Public helpers ─────────────────────────────────────────────────────────

    def get_gfd_device(self) -> CxlGfdDevice:
        """Return the underlying device (useful for test introspection)."""
        return self._gfd_device

    # ── RunnableComponent lifecycle ────────────────────────────────────────────

    async def _run(self):
        """Start the GFD device and (optionally) the switch connection client.

        In normal mode, both the SwitchConnectionClient and CxlGfdDevice are
        started concurrently.  In test mode only the CxlGfdDevice is started.
        The method waits for all sub-components to report ready before
        transitioning this component to RUNNING, then awaits all run tasks.
        """
        run_tasks = [create_task(self._gfd_device.run())]
        wait_tasks = [create_task(self._gfd_device.wait_for_ready())]

        if not self._test_mode:
            run_tasks.append(create_task(self._sw_conn_client.run()))
            wait_tasks.append(create_task(self._sw_conn_client.wait_for_ready()))

        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        """Stop all sub-components gracefully.

        Stops the CxlGfdDevice and, in non-test mode, the
        SwitchConnectionClient.  Both stop calls run concurrently.
        """
        stop_tasks = [create_task(self._gfd_device.stop())]
        if not self._test_mode:
            stop_tasks.append(create_task(self._sw_conn_client.stop()))
        await gather(*stop_tasks)
