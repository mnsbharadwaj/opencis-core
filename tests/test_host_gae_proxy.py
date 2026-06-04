"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Host-direct GAE Proxy Management — Integration Tests
====================================================
CXL 4.0 §7.7.14.10 / §7.7.14.11 / §7.7.14.12

Tests the full proxy management lifecycle via the HOST path
(CxlSimpleHost → cci_fifo → GaeCciMailbox → GaeManager → DspCciTunnel → GFD):

  No FM/MCTP path is used — commands travel on port-8000 TCP cci_fifo.

Flow:
  CxlSimpleHost.gae_proxy_gfd_mgmt()
    → CxlRootPortDevice.gae_command(0x5809)
    → cci_fifo.host_to_target  (across TCP)
    → GaeCciMailbox._run_mailbox()
    → CciExecutor → ProxyGfdMgmtCommand → GaeManager.start_proxy()
    → DspCciTunnel (or executor) → GFD → response
    → cci_fifo.target_to_host (back to host)
    → CxlSimpleHost gets thread_id

  CxlSimpleHost.gae_get_proxy_status(thread_id)  [0x580A]
  CxlSimpleHost.gae_cancel_proxy(thread_id)       [0x580B]
"""

import asyncio
import pytest

from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.gae_cci_mailbox import GaeCciMailbox
from opencis.cxl.component.gae_manager import GaeManager
from opencis.cxl.component.cci_executor import CciExecutor, CciRequest
from opencis.cxl.cci.common import CCI_RETURN_CODE, CCI_GAE_COMMAND_OPCODE
from opencis.cxl.cci.generic.information_and_status.identify import (
    IdentifyCommand, IdentifyComponentType, IdentifyResponsePayload,
)
from opencis.cxl.cci.fabric_manager.gae.proxy_gfd_mgmt import (
    ProxyGfdMgmtCommand,
    ProxyGfdMgmtRequestPayload,
    ProxyGfdMgmtResponsePayload,
)
from opencis.cxl.cci.fabric_manager.gae.get_proxy_thread_status import (
    GetProxyThreadStatusCommand,
    GetProxyThreadStatusRequestPayload,
    GetProxyThreadStatusResponsePayload,
)
from opencis.cxl.cci.fabric_manager.gae.cancel_proxy_thread import (
    CancelProxyThreadCommand,
    CancelProxyThreadRequestPayload,
)
from opencis.cxl.cci.fabric_manager.gae.identify_gae import IdentifyGaeCommand
from opencis.cxl.device.root_port_device import CxlRootPortDevice
from opencis.cxl.transport.cci_packets import CciMessagePacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY


# ── Helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    import logging
    caplog.set_level(logging.WARNING)


def _make_shared_executor_with_gae(gfd_executor: CciExecutor) -> tuple:
    """
    Build a shared CciExecutor that has all three GAE proxy commands registered
    (using an in-process GFD executor for fast tests).
    Returns (shared_executor, gae_manager).
    """
    gae = GaeManager(vppbs=[], label="TestGAE")
    gae.set_gfd_executor(gfd_executor)

    shared_exec = CciExecutor(label="Switch:GAE")
    shared_exec.register_command(
        ProxyGfdMgmtCommand.OPCODE, ProxyGfdMgmtCommand(gae)
    )
    shared_exec.register_command(
        GetProxyThreadStatusCommand.OPCODE, GetProxyThreadStatusCommand(gae)
    )
    shared_exec.register_command(
        CancelProxyThreadCommand.OPCODE, CancelProxyThreadCommand(gae)
    )
    shared_exec.register_command(
        IdentifyGaeCommand.OPCODE, IdentifyGaeCommand(gae)
    )
    return shared_exec, gae


async def _start_gfd_executor(executor: CciExecutor):
    """Start a CciExecutor and return its task."""
    t = asyncio.create_task(executor.run())
    await executor.wait_for_ready()
    return t


async def _stop(executor: CciExecutor, task):
    await executor.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: GaeCciMailbox unit tests (in-process, no TCP)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gae_cci_mailbox_routes_proxy_gfd_mgmt():
    """
    GaeCciMailbox reads CciMessagePacket from cci_fifo.host_to_target,
    dispatches to CciExecutor, and puts the response on cci_fifo.target_to_host.
    """
    gfd_exec = CciExecutor(label="GFD")
    gfd_exec.register_command(
        IdentifyCommand.OPCODE,
        IdentifyCommand(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t_gfd = await _start_gfd_executor(gfd_exec)

    shared_exec, gae = _make_shared_executor_with_gae(gfd_exec)
    t_sw = await _start_gfd_executor(shared_exec)

    # Create a fake USP CxlConnection (in-process test — no real TCP)
    usp_conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=usp_conn, cci_executor=shared_exec, label="TestMailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    # Build ProxyGfdMgmt request payload
    req_payload = ProxyGfdMgmtRequestPayload(gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b"")
    req_msg = CciMessagePacket.create(
        data=req_payload.dump(),
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        message_tag=1,
    )

    # Simulate host putting packet on cci_fifo.host_to_target
    await usp_conn.cci_fifo.host_to_target.put(req_msg)

    # Wait for the mailbox to process and put response on cci_fifo.target_to_host
    resp_msg = await asyncio.wait_for(
        usp_conn.cci_fifo.target_to_host.get(), timeout=3.0
    )
    assert resp_msg is not None
    assert resp_msg.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)

    # Parse the response payload to get thread_id
    resp_parsed = ProxyGfdMgmtResponsePayload.parse(resp_msg.get_payload())
    assert resp_parsed.thread_id == 1

    # Wait for proxy to complete and verify
    await asyncio.sleep(0.1)
    entry = gae.get_proxy_status(1)
    assert entry is not None
    assert entry.completed is True

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
    await _stop(gfd_exec, t_gfd)


@pytest.mark.asyncio
async def test_gae_cci_mailbox_routes_get_proxy_status():
    """
    GaeCciMailbox dispatches GetProxyThreadStatus (0x580A) and returns
    the correct completed status.
    """
    gfd_exec = CciExecutor(label="GFD")
    gfd_exec.register_command(
        IdentifyCommand.OPCODE,
        IdentifyCommand(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t_gfd = await _start_gfd_executor(gfd_exec)

    shared_exec, gae = _make_shared_executor_with_gae(gfd_exec)
    t_sw = await _start_gfd_executor(shared_exec)

    usp_conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=usp_conn, cci_executor=shared_exec, label="TestMailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    # Step 1: Start proxy
    req_payload = ProxyGfdMgmtRequestPayload(gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b"")
    req_msg = CciMessagePacket.create(
        data=req_payload.dump(),
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        message_tag=0,
    )
    await usp_conn.cci_fifo.host_to_target.put(req_msg)
    resp_msg = await asyncio.wait_for(usp_conn.cci_fifo.target_to_host.get(), timeout=3.0)
    assert resp_msg.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)
    tid = ProxyGfdMgmtResponsePayload.parse(resp_msg.get_payload()).thread_id

    # Wait for proxy task to finish
    await asyncio.sleep(0.1)

    # Step 2: Get status
    status_req_payload = GetProxyThreadStatusRequestPayload(thread_id=tid)
    status_msg = CciMessagePacket.create(
        data=status_req_payload.dump(),
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.GET_PROXY_THREAD_STATUS,
        message_tag=1,
    )
    await usp_conn.cci_fifo.host_to_target.put(status_msg)
    status_resp = await asyncio.wait_for(usp_conn.cci_fifo.target_to_host.get(), timeout=3.0)
    assert status_resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)

    parsed_status = GetProxyThreadStatusResponsePayload.parse(status_resp.get_payload())
    assert parsed_status.thread_id == tid
    assert parsed_status.completed is True
    assert parsed_status.gfd_return_code == int(CCI_RETURN_CODE.SUCCESS)
    id_resp = IdentifyResponsePayload.parse(parsed_status.gfd_response_payload)
    assert id_resp.component_type == IdentifyComponentType.GFD

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
    await _stop(gfd_exec, t_gfd)


@pytest.mark.asyncio
async def test_gae_cci_mailbox_cancel_proxy():
    """
    GaeCciMailbox dispatches CancelProxyThread (0x580B) on an in-flight thread.
    Entry should be marked ABORTED.
    """
    gfd_exec = CciExecutor(label="GFD")

    class SlowCmd(IdentifyCommand):
        async def _execute(self, r):
            await asyncio.sleep(10.0)
            return await super()._execute(r)

    gfd_exec.register_command(
        IdentifyCommand.OPCODE,
        SlowCmd(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t_gfd = await _start_gfd_executor(gfd_exec)

    shared_exec, gae = _make_shared_executor_with_gae(gfd_exec)
    t_sw = await _start_gfd_executor(shared_exec)

    usp_conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=usp_conn, cci_executor=shared_exec, label="TestMailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    # Start proxy (will not finish due to 10s sleep)
    req_payload = ProxyGfdMgmtRequestPayload(gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b"")
    req_msg = CciMessagePacket.create(
        data=req_payload.dump(),
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        message_tag=0,
    )
    await usp_conn.cci_fifo.host_to_target.put(req_msg)
    resp_msg = await asyncio.wait_for(usp_conn.cci_fifo.target_to_host.get(), timeout=3.0)
    tid = ProxyGfdMgmtResponsePayload.parse(resp_msg.get_payload()).thread_id

    # Cancel the proxy thread
    cancel_payload = CancelProxyThreadRequestPayload(thread_id=tid)
    cancel_msg = CciMessagePacket.create(
        data=cancel_payload.dump(),
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=CCI_GAE_COMMAND_OPCODE.CANCEL_PROXY_THREAD,
        message_tag=1,
    )
    await usp_conn.cci_fifo.host_to_target.put(cancel_msg)
    cancel_resp = await asyncio.wait_for(usp_conn.cci_fifo.target_to_host.get(), timeout=3.0)
    assert cancel_resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)

    # Entry should be ABORTED
    entry = gae.get_proxy_status(tid)
    assert entry is not None
    assert entry.completed is True
    assert entry.return_code == int(CCI_RETURN_CODE.ABORTED)

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
    await _stop(gfd_exec, t_gfd)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: CxlRootPortDevice.gae_command() — over in-process cci_fifo
#         (simulates what happens across TCP without real networking)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_root_port_device_gae_command_proxy():
    """
    CxlRootPortDevice.gae_command() sends ProxyGfdMgmt over cci_fifo.host_to_target.
    A GaeCciMailbox on the other side processes it and returns the response
    on cci_fifo.target_to_host. gae_command() returns (SUCCESS, bytes).
    """
    # Build GFD executor
    gfd_exec = CciExecutor(label="GFD")
    gfd_exec.register_command(
        IdentifyCommand.OPCODE,
        IdentifyCommand(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t_gfd = await _start_gfd_executor(gfd_exec)

    # Build shared switch CCI executor + GAE mailbox
    shared_exec, _ = _make_shared_executor_with_gae(gfd_exec)
    t_sw = await _start_gfd_executor(shared_exec)

    # Shared CxlConnection — host puts on host_to_target, switch reads it
    conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=conn, cci_executor=shared_exec, label="Mailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    # Root port uses the SAME connection
    root_port = CxlRootPortDevice(
        downstream_connection=conn, label="Port0", test_mode=True
    )

    # Build ProxyGfdMgmt payload
    req_payload = ProxyGfdMgmtRequestPayload(
        gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b""
    ).dump()

    rc, resp_bytes = await root_port.gae_command(
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        payload=req_payload,
        timeout=3.0,
    )
    assert rc == CCI_RETURN_CODE.SUCCESS
    parsed = ProxyGfdMgmtResponsePayload.parse(resp_bytes)
    assert parsed.thread_id == 1

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
    await _stop(gfd_exec, t_gfd)


@pytest.mark.asyncio
async def test_root_port_device_gae_full_3_command_flow():
    """
    Full host-direct GAE proxy 3-command flow via CxlRootPortDevice.gae_command():
      0x5809 ProxyGfdMgmt  → thread_id
      0x580A GetStatus     → completed=True, GFD Identify payload
      0x580B CancelProxy   → SUCCESS (idempotent after completion)
    """
    gfd_exec = CciExecutor(label="GFD")
    gfd_exec.register_command(
        IdentifyCommand.OPCODE,
        IdentifyCommand(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t_gfd = await _start_gfd_executor(gfd_exec)

    shared_exec, gae = _make_shared_executor_with_gae(gfd_exec)
    t_sw = await _start_gfd_executor(shared_exec)

    conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=conn, cci_executor=shared_exec, label="Mailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    root_port = CxlRootPortDevice(
        downstream_connection=conn, label="Port0", test_mode=True
    )

    # ── Step 1: ProxyGfdMgmt ──────────────────────────────────────────────────
    req_bytes = ProxyGfdMgmtRequestPayload(
        gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b""
    ).dump()
    rc, resp = await root_port.gae_command(
        opcode=CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD,
        payload=req_bytes, timeout=3.0, tag=0,
    )
    assert rc == CCI_RETURN_CODE.SUCCESS
    tid = ProxyGfdMgmtResponsePayload.parse(resp).thread_id
    assert tid == 1

    # ── Step 2: Wait + GetStatus ──────────────────────────────────────────────
    await asyncio.sleep(0.1)  # let proxy task finish

    status_bytes = GetProxyThreadStatusRequestPayload(thread_id=tid).dump()
    rc2, resp2 = await root_port.gae_command(
        opcode=CCI_GAE_COMMAND_OPCODE.GET_PROXY_THREAD_STATUS,
        payload=status_bytes, timeout=3.0, tag=1,
    )
    assert rc2 == CCI_RETURN_CODE.SUCCESS
    status = GetProxyThreadStatusResponsePayload.parse(resp2)
    assert status.thread_id == tid
    assert status.completed is True
    assert status.gfd_return_code == int(CCI_RETURN_CODE.SUCCESS)
    id_resp = IdentifyResponsePayload.parse(status.gfd_response_payload)
    assert id_resp.component_type == IdentifyComponentType.GFD

    # ── Step 3: CancelProxy (idempotent) ─────────────────────────────────────
    cancel_bytes = CancelProxyThreadRequestPayload(thread_id=tid).dump()
    rc3, _ = await root_port.gae_command(
        opcode=CCI_GAE_COMMAND_OPCODE.CANCEL_PROXY_THREAD,
        payload=cancel_bytes, timeout=3.0, tag=2,
    )
    assert rc3 == CCI_RETURN_CODE.SUCCESS

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
    await _stop(gfd_exec, t_gfd)


@pytest.mark.asyncio
async def test_gae_cci_mailbox_unknown_opcode_returns_unsupported():
    """GaeCciMailbox returns UNSUPPORTED for an opcode not registered."""
    shared_exec = CciExecutor(label="Switch:GAE")
    t_sw = await _start_gfd_executor(shared_exec)

    conn = CxlConnection()
    mailbox = GaeCciMailbox(usp_connection=conn, cci_executor=shared_exec, label="Mailbox")
    mb_task = asyncio.create_task(mailbox.run())
    await mailbox.wait_for_ready()

    req_msg = CciMessagePacket.create(
        data=b"",
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=0xDEAD,
        message_tag=0,
    )
    await conn.cci_fifo.host_to_target.put(req_msg)
    resp = await asyncio.wait_for(conn.cci_fifo.target_to_host.get(), timeout=3.0)
    assert resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.UNSUPPORTED)

    await mailbox.stop()
    mb_task.cancel()
    try:
        await mb_task
    except asyncio.CancelledError:
        pass
    await _stop(shared_exec, t_sw)
