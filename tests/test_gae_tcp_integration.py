"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

End-to-End TCP Integration Test: Host → Switch → GAE → GFD
============================================================
This test exercises the PRODUCTION code path with a REAL TCP connection:

  CxlSimpleHost → TCP port 8000 → CxlSwitch (PBR mode) → GaeCciMailbox
               ← TCP ────────────────────────────────────── ←

Specifically tests that:
  1. CxlPacketProcessor (R type) serializes CCI requests to TCP
  2. CxlPacketProcessor (USP type) deserializes CCI from TCP → GaeCciMailbox
  3. GaeCciMailbox dispatches → CciExecutor → ProxyGfdMgmtCommand → GaeManager
  4. Response travels back over TCP → CxlRootPortDevice.gae_command() returns

This validates the fix to R type incoming/outgoing CCI TCP transport in
cxl_packet_processor.py (previously had 'else: break' for R type, silently
dropping all CCI packets in production TCP flow).

NOTE: These tests spin up real asyncio TCP servers and clients. They test
the actual production code path, not in-process queue shortcuts.
"""

import asyncio
import inspect
import pytest

from opencis.apps.cxl_switch import CxlSwitch, CxlSwitchConfig
from opencis.apps.cxl_simple_host import CxlSimpleHost
from opencis.cxl.component.physical_port_manager import PortConfig, PORT_TYPE
from opencis.cxl.component.virtual_switch_manager import VirtualSwitchConfig
from oslash.either import Right, Left
from opencis.cxl.cci.common import CCI_RETURN_CODE, CCI_GAE_COMMAND_OPCODE
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.apps.generic_fabric_device import GenericFabricDevice


# ── Fixtures ───────────────────────────────────────────────────────────────────

SWITCH_PORT   = 18200   # port 8000 analog for this test
MCTP_PORT     = 18201   # port 8100 analog
GFD_SW_PORT   = 18200   # GFD connects to same switch


def make_switch(switch_port: int, mctp_port: int) -> CxlSwitch:
    """Create a minimal PBR CxlSwitch with USP (port 0) + one DSP (port 1)."""
    port_configs = [
        PortConfig(PORT_TYPE.USP),  # port 0 — host connects here
        PortConfig(PORT_TYPE.DSP),  # port 1 — GFD connects here
    ]
    # Inspect VirtualSwitchConfig to build it with whatever args it needs
    sig = inspect.signature(VirtualSwitchConfig.__init__)
    params = list(sig.parameters.keys())
    kwargs = dict(upstream_port_index=0, vppb_counts=1, initial_bounds=[1])
    if "irq_host" in params:
        kwargs["irq_host"] = "localhost"
    if "irq_port" in params:
        kwargs["irq_port"] = switch_port + 100
    vcs_configs = [VirtualSwitchConfig(**kwargs)]
    switch_config = CxlSwitchConfig(
        port_configs=port_configs,
        virtual_switch_configs=vcs_configs,
        host="127.0.0.1",
        port=switch_port,
        mctp_host="127.0.0.1",
        mctp_port=mctp_port,
        enable_pbr=True,
    )
    return CxlSwitch(
        switch_config=switch_config,
        device_configs=[],
        start_mctp=True,  # Enable MCTP so MctpCciExecutor & GaeCciMailbox run
    )


def make_host(switch_port: int) -> CxlSimpleHost:
    """Create a CxlSimpleHost connecting to the test switch."""
    return CxlSimpleHost(
        port_index=0,
        switch_host="127.0.0.1",
        switch_port=switch_port,
        hm_mode=False,
        test_mode=True,
    )


def make_gfd(switch_port: int) -> GenericFabricDevice:
    """Create a GenericFabricDevice connecting on DSP port 1."""
    return GenericFabricDevice(
        host="127.0.0.1",
        port=switch_port,
        port_index=1,
    )


# ── Helper: run with timeout and cancel cleanly ───────────────────────────────

async def _run_with_timeout(coros, timeout: float):
    """Start all coros as tasks, run the test body, then cancel them."""
    tasks = [asyncio.create_task(c) for c in coros]
    try:
        await asyncio.wait_for(asyncio.shield(asyncio.gather(*tasks)), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        for t in tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Verify production gae_support_map
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pbr_switch_gae_support_map_is_1():
    """
    IdentifyPbrSwitch (0x5700) must return gae_support_map=0x01 when enable_pbr=True.
    Before the fix this returned 0 (no GAE reported), which would prevent the host
    from knowing the switch has a GAE block.
    """
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
    ]
    sig = inspect.signature(VirtualSwitchConfig.__init__)
    params = list(sig.parameters.keys())
    kwargs = dict(upstream_port_index=0, vppb_counts=1, initial_bounds=[1])
    if "irq_host" in params:
        kwargs["irq_host"] = "localhost"
    if "irq_port" in params:
        kwargs["irq_port"] = 18212
    vcs_configs = [VirtualSwitchConfig(**kwargs)]
    switch = CxlSwitch(
        switch_config=CxlSwitchConfig(
            port_configs=port_configs,
            virtual_switch_configs=vcs_configs,
            host="127.0.0.1",
            port=18210,
            mctp_host="127.0.0.1",
            mctp_port=18211,
            enable_pbr=True,
        ),
        device_configs=[],
        start_mctp=False,
    )
    # gae_support_map is set in __init__ when enable_pbr=True
    info = switch._pbr_switch_manager.get_identify_info()
    assert info.gae_support_map == 0x01, (
        f"Expected gae_support_map=0x01, got {info.gae_support_map:#018x}. "
        "IdentifyPbrSwitch will incorrectly report no GAE."
    )
    assert info.num_drts >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: TCP end-to-end — gae_proxy_gfd_mgmt over real TCP
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gae_proxy_over_real_tcp():
    """
    Full TCP integration test: CxlSimpleHost.gae_proxy_gfd_mgmt() travels over
    a real asyncio TCP connection (not in-process Queues) to a real CxlSwitch
    instance in PBR mode. Validates the CxlPacketProcessor R-type fixes.

    Flow:
      CxlSimpleHost.gae_proxy_gfd_mgmt(gfd_opcode=0x0001)
        → CxlRootPortDevice.gae_command(0x5809)
        → cci_fifo.host_to_target Queue
        → CxlPacketProcessor(R) — NEW: serializes to TCP    ← was broken (else: break)
        → TCP wire
        → CxlPacketProcessor(USP) — NEW: routes to GaeCciMailbox ← already fixed
        → GaeCciMailbox → CciExecutor → ProxyGfdMgmtCommand
        → GaeManager.start_proxy() → thread_id=1
        → cci_fifo.target_to_host Queue
        → CxlPacketProcessor(USP) writes response to TCP
        → CxlPacketProcessor(R) — NEW: routes to cci_fifo.target_to_host  ← was broken
        → CxlRootPortDevice.gae_command() returns (SUCCESS, bytes)
        → CxlSimpleHost.gae_proxy_gfd_mgmt() returns Result(thread_id=1)
    """
    SW_PORT = 18220

    # Start a mock MCTP server to satisfy MctpConnectionClient's connection attempt
    # and allow the switch to transition to the RUNNING state.
    async def mock_mctp_server(reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    mctp_server = await asyncio.start_server(mock_mctp_server, "127.0.0.1", 18221)

    switch = make_switch(switch_port=SW_PORT, mctp_port=18221)
    host   = make_host(switch_port=SW_PORT)
    gfd    = make_gfd(switch_port=SW_PORT)

    result_holder = {}

    def get_val(res):
        if isinstance(res, Right):
            return res._value.result["result"]
        elif isinstance(res, Left):
            raise RuntimeError(f"RPC Error: {res._error.message}")
        return res

    async def _test_body():
        # Wait for everything to connect
        await switch.wait_for_ready()
        await host.wait_for_ready()
        await gfd.wait_for_ready()

        # Send GAE proxy command over REAL TCP
        # GAE Proxy: ask GFD to run Identify (CCI opcode 0x0001)
        result = await host.gae_proxy_gfd_mgmt(gfd_opcode=0x0001, timeout=5.0)
        result_holder["proxy"] = result

        # Let proxy task complete on GAE
        await asyncio.sleep(0.3)

        # Poll status
        thread_id = get_val(result)
        status_result = await host.gae_get_proxy_status(thread_id=thread_id, timeout=5.0)
        result_holder["status"] = status_result

    async def _orchestrate():
        await _test_body()

    async def run():
        run_tasks = [
            asyncio.create_task(switch.run()),
            asyncio.create_task(host.run()),
            asyncio.create_task(gfd.run()),
        ]
        try:
            await asyncio.wait_for(_orchestrate(), timeout=15.0)
        finally:
            for t in run_tasks:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            mctp_server.close()
            await mctp_server.wait_closed()

    await run()

    # Assertions
    assert "proxy" in result_holder, "gae_proxy_gfd_mgmt never returned"
    proxy = result_holder["proxy"]
    thread_id = get_val(proxy)
    assert isinstance(thread_id, int) and thread_id >= 1

    assert "status" in result_holder, "gae_get_proxy_status never returned"
    status = result_holder["status"]
    status_val = get_val(status)
    assert status_val["completed"] is True
    assert status_val["gfd_return_code"] == int(CCI_RETURN_CODE.SUCCESS)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: CxlPacketProcessor R type — outgoing CCI is serialized (unit test)
# ═══════════════════════════════════════════════════════════════════════════════

def test_r_type_cci_wrapping_adds_system_header():
    """
    When CxlPacketProcessor(R) sends a CCI packet over TCP it wraps
    CciMessagePacket → CciPayloadPacket so that PacketReader on the
    switch side sees system_header.payload_type == CCI_MCTP and calls is_cci().

    This tests the wrapping logic directly — no processor, no asyncio, no sideband.
    """
    from opencis.cxl.transport.packet_constants import SYSTEM_PAYLOAD_TYPE

    # 1. Build the internal CciMessagePacket (what GaeCciMailbox / gae_command puts on Queue)
    req_msg = CciMessagePacket.create(
        data=b"\xAB\xCD",
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        message_tag=42,
    )

    # 2. Wrap it — this is what cxl_packet_processor.py does before writing to TCP
    wire_packet = CciPayloadPacket.create(req_msg)
    wire_bytes  = bytes(wire_packet)

    # 3. SystemHeader must be present and carry CCI_MCTP type
    assert wire_packet.system_header.payload_type == SYSTEM_PAYLOAD_TYPE.CCI_MCTP, (
        f"Expected CCI_MCTP({SYSTEM_PAYLOAD_TYPE.CCI_MCTP}), "
        f"got {wire_packet.system_header.payload_type}"
    )

    # 4. Wire bytes must be non-empty and larger than the raw CciMessagePacket
    assert len(wire_bytes) > len(bytes(req_msg)), (
        "CciPayloadPacket must be larger than CciMessagePacket (includes SystemHeader)"
    )

    # 5. Unwrapping must restore the original tag and opcode
    inner = wire_packet.get_cci_message()
    assert inner.cci_msg_header.message_tag == 42
    assert inner.cci_msg_header.command_opcode == int(CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD)


@pytest.mark.asyncio
async def test_r_type_incoming_cci_routes_to_target_to_host():
    """
    CxlPacketProcessor(R) must route incoming CciPayloadPacket bytes (the GAE→Host
    response arriving from TCP) to cci_fifo.target_to_host.  PacketReader reads the
    SystemHeader, sees SYSTEM_PAYLOAD_TYPE.CCI_MCTP, calls is_cci() → True, and
    puts the packet in the CCI queue.  Before the fix, R type was not handled.
    """
    from opencis.cxl.component.cxl_connection import CxlConnection
    from opencis.cxl.component.cxl_packet_processor import CxlPacketProcessor
    from opencis.cxl.component.common import CXL_COMPONENT_TYPE
    from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
    from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY

    conn = CxlConnection()

    # Build a CciPayloadPacket — this is what GaeCciMailbox puts on the target_to_host Queue.
    # Its bytes have SystemHeader.payload_type=CCI_MCTP so PacketReader.is_cci() returns True.
    resp_msg = CciMessagePacket.create(
        data=b"\x01\x00",
        message_category=CCI_MCTP_MESSAGE_CATEGORY.RESPONSE,
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        message_tag=42,
        return_code=int(CCI_RETURN_CODE.SUCCESS),
    )
    resp_payload_pkt = CciPayloadPacket.create(resp_msg)
    raw_bytes = bytes(resp_payload_pkt)

    class FakeReader:
        def __init__(self):
            self._data = raw_bytes
            self._pos = 0

        async def read(self, n):
            chunk = self._data[self._pos:self._pos + n]
            self._pos += n
            if len(chunk) == 0:
                await asyncio.sleep(3600)
            return chunk

    class FakeWriter:
        def write(self, data): pass
        async def drain(self): pass
        def get_extra_info(self, key, default=None): return default

    processor = CxlPacketProcessor(
        reader=FakeReader(),
        writer=FakeWriter(),
        cxl_connection=conn,
        component_type=CXL_COMPONENT_TYPE.R,
        label="TestR-Incoming",
    )

    proc_task = asyncio.create_task(processor._process_incoming_packets())
    try:
        received = await asyncio.wait_for(conn.cci_fifo.target_to_host.get(), timeout=5.0)
    finally:
        proc_task.cancel()
        try:
            await proc_task
        except (asyncio.CancelledError, Exception):
            pass

    assert received is not None, (
        "CxlPacketProcessor(R) did not route incoming CCI to cci_fifo.target_to_host."
    )
    # received is a CciPayloadPacket; unwrap to verify the inner CciMessagePacket tag
    if isinstance(received, CciPayloadPacket):
        inner = received.get_cci_message()
        assert inner.cci_msg_header.message_tag == 42
