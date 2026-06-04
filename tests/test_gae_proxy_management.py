"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

GAE Proxy Thread Management — Integration Tests
===============================================
CXL 4.0 §7.7.14.10 / §7.7.14.11 / §7.7.14.12

Tests the complete proxy management lifecycle:

  ProxyGfdMgmtCommand (0x5809)
    → GaeManager.start_proxy()
    → asyncio task via DspCciTunnel or direct CciExecutor
    → GFD _run_cci_mailbox() processes
    → entry.completed = True, entry.response = <GFD response>

  GetProxyThreadStatusCommand (0x580A)
    → GaeManager.get_proxy_status(thread_id)
    → returns completed=True/False, gfd_return_code, gfd_response_payload

  CancelProxyThreadCommand (0x580B)
    → GaeManager.cancel_proxy(thread_id)
    → task cancelled, entry marked completed=True, return_code=ABORTED

Both production mode (via cci_fifo + DspCciTunnel) and unit-test mode
(direct executor) are tested.
"""

import asyncio
import pytest

from opencis.apps.generic_fabric_device import GenericFabricDevice
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.gae_manager import GaeManager, GaeVppbInfo
from opencis.cxl.component.dsp_cci_tunnel import DspCciTunnel
from opencis.cxl.component.cci_executor import CciExecutor, CciRequest, CciResponse
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


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    import logging
    caplog.set_level(logging.WARNING)


def _make_gfd_with_conn():
    """Return (GenericFabricDevice, CxlConnection) in test mode."""
    conn = CxlConnection()
    gfd = GenericFabricDevice(
        test_mode=True, cxl_connection=conn, port_index=1,
    )
    return gfd, conn


def _make_gae_manager():
    """Return a GaeManager with no vPPBs (simple-device GFD)."""
    return GaeManager(vppbs=[], label="TestGAE")


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: GaeManager unit tests (in-process executor mode)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gae_has_no_binding_initially():
    """GaeManager starts with no GFD binding."""
    gae = _make_gae_manager()
    assert not gae.has_gfd_binding()
    assert gae.thread_count() == 0
    assert gae.active_thread_count() == 0


@pytest.mark.asyncio
async def test_gae_set_executor_has_binding():
    """Binding an executor makes has_gfd_binding() True."""
    gae = _make_gae_manager()
    gfd_executor = CciExecutor(label="GFD")
    gae.set_gfd_executor(gfd_executor)
    assert gae.has_gfd_binding()
    assert gae.get_gfd_executor() is gfd_executor


@pytest.mark.asyncio
async def test_gae_set_tunnel_has_binding():
    """Binding a DspCciTunnel makes has_gfd_binding() True (tunnel takes precedence)."""
    conn = CxlConnection()
    tunnel = DspCciTunnel(cci_fifo=conn.cci_fifo, port_index=1)
    gae = _make_gae_manager()
    gae.set_gfd_tunnel(tunnel)
    assert gae.has_gfd_binding()
    assert gae.get_gfd_executor() is None   # tunnel takes precedence, executor is None


@pytest.mark.asyncio
async def test_gae_start_proxy_no_binding_returns_zero():
    """start_proxy() without binding returns 0 (error)."""
    gae = _make_gae_manager()
    tid = await gae.start_proxy(gfd_opcode=0x0001, gfd_request_payload=b"")
    assert tid == 0


@pytest.mark.asyncio
async def test_gae_start_proxy_executor_returns_thread_id():
    """start_proxy() with a bound executor returns a positive thread_id."""
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))

    async def _run_executor():
        await gfd_executor.run()

    exec_task = asyncio.create_task(_run_executor())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    tid = await gae.start_proxy(
        gfd_opcode=IdentifyCommand.OPCODE,
        gfd_request_payload=b"",
    )
    assert tid == 1   # first thread_id

    # Wait for the proxy task to complete
    await asyncio.sleep(0.1)

    entry = gae.get_proxy_status(tid)
    assert entry is not None
    assert entry.completed is True
    assert entry.return_code == int(CCI_RETURN_CODE.SUCCESS)
    assert entry.response is not None

    await gfd_executor.stop()
    exec_task.cancel()
    try:
        await exec_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_gae_proxy_response_contains_identify_payload():
    """Proxy response should contain GFD Identify payload with component_type=GFD."""
    identity = IdentifyResponsePayload(
        vendor_id=0xEEEE, device_id=0x0100,
        component_type=IdentifyComponentType.GFD,
    )
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(identity))

    async def _run():
        await gfd_executor.run()

    t = asyncio.create_task(_run())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")
    await asyncio.sleep(0.1)

    entry = gae.get_proxy_status(tid)
    assert entry.completed is True
    assert entry.response is not None
    parsed = IdentifyResponsePayload.parse(entry.response.payload)
    assert parsed.component_type == IdentifyComponentType.GFD
    assert parsed.vendor_id == 0xEEEE

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_gae_thread_id_increments():
    """Each call to start_proxy() gets a unique, incrementing thread_id."""
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = asyncio.create_task(gfd_executor.run())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    tids = []
    for _ in range(5):
        tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")
        tids.append(tid)

    assert tids == [1, 2, 3, 4, 5]
    assert gae.thread_count() == 5

    await asyncio.sleep(0.1)
    assert gae.active_thread_count() == 0   # all should be done

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_gae_cancel_unknown_thread():
    """cancel_proxy() for unknown thread_id returns INVALID_INPUT."""
    gae = _make_gae_manager()
    rc = gae.cancel_proxy(999)
    assert rc == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_gae_cancel_completed_thread_is_idempotent():
    """cancel_proxy() on already-completed thread returns SUCCESS (idempotent)."""
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = asyncio.create_task(gfd_executor.run())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)
    tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")

    await asyncio.sleep(0.1)  # let it complete

    rc = gae.cancel_proxy(tid)
    assert rc == CCI_RETURN_CODE.SUCCESS   # idempotent

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_gae_cancel_marks_entry_aborted():
    """
    cancel_proxy() on an in-flight thread marks entry completed=True,
    return_code=ABORTED so Get Proxy Thread Status can still return it.
    """
    gfd_executor = CciExecutor(label="GFD")

    # Register a slow command (1-second sleep)
    class SlowCommand(IdentifyCommand):
        async def _execute(self, _):
            await asyncio.sleep(10.0)
            return await super()._execute(_)

    gfd_executor.register_command(
        IdentifyCommand.OPCODE,
        SlowCommand(IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)),
    )
    t = asyncio.create_task(gfd_executor.run())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)
    tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")

    # Cancel before it finishes
    rc = gae.cancel_proxy(tid)
    assert rc == CCI_RETURN_CODE.SUCCESS

    # Entry should still be accessible and marked ABORTED
    entry = gae.get_proxy_status(tid)
    assert entry is not None
    assert entry.completed is True
    assert entry.return_code == int(CCI_RETURN_CODE.ABORTED)

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_gae_purge_completed_threads():
    """purge_completed_threads() removes completed entries, keeps active ones."""
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = asyncio.create_task(gfd_executor.run())
    await gfd_executor.wait_for_ready()

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    for _ in range(3):
        await gae.start_proxy(IdentifyCommand.OPCODE, b"")

    await asyncio.sleep(0.1)   # all 3 complete

    assert gae.thread_count() == 3
    purged = gae.purge_completed_threads()
    assert purged == 3
    assert gae.thread_count() == 0

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: ProxyGfdMgmtCommand (0x5809), GetProxyThreadStatusCommand (0x580A),
#         CancelProxyThreadCommand (0x580B) — Command handler unit tests
# ═══════════════════════════════════════════════════════════════════════════════


async def _run_gfd_with_executor(gfd_executor: CciExecutor):
    """Run a CciExecutor and return a cancel function."""
    t = asyncio.create_task(gfd_executor.run())
    await gfd_executor.wait_for_ready()
    return t


@pytest.mark.asyncio
async def test_proxy_cmd_returns_thread_id():
    """
    ProxyGfdMgmtCommand._execute() returns a thread_id in the response payload.
    """
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = await _run_gfd_with_executor(gfd_executor)

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    cmd = ProxyGfdMgmtCommand(gae)
    req = ProxyGfdMgmtCommand.create_cci_request(
        gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b""
    )
    resp = await cmd._execute(req)

    assert resp.return_code == CCI_RETURN_CODE.SUCCESS
    parsed = ProxyGfdMgmtResponsePayload.parse(resp.payload)
    assert parsed.thread_id == 1

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_proxy_cmd_no_binding_returns_internal_error():
    """ProxyGfdMgmtCommand returns INTERNAL_ERROR when no GFD binding."""
    gae = _make_gae_manager()   # no executor, no tunnel
    cmd = ProxyGfdMgmtCommand(gae)
    req = ProxyGfdMgmtCommand.create_cci_request(
        gfd_opcode=IdentifyCommand.OPCODE, gfd_payload=b""
    )
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INTERNAL_ERROR


@pytest.mark.asyncio
async def test_proxy_cmd_empty_payload_returns_invalid_input():
    """ProxyGfdMgmtCommand returns INVALID_INPUT for empty payload."""
    gae = _make_gae_manager()
    cmd = ProxyGfdMgmtCommand(gae)
    req = CciRequest(opcode=ProxyGfdMgmtCommand.OPCODE, payload=b"")
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_get_proxy_thread_status_completed():
    """
    GetProxyThreadStatusCommand returns completed=True with GFD response after
    the proxy task finishes.
    """
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = await _run_gfd_with_executor(gfd_executor)

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    # Start proxy
    tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")
    await asyncio.sleep(0.1)   # let it finish

    # Get status
    status_cmd = GetProxyThreadStatusCommand(gae)
    status_req = GetProxyThreadStatusCommand.create_cci_request(thread_id=tid)
    status_resp = await status_cmd._execute(status_req)

    assert status_resp.return_code == CCI_RETURN_CODE.SUCCESS
    parsed = GetProxyThreadStatusResponsePayload.parse(status_resp.payload)
    assert parsed.thread_id == tid
    assert parsed.completed is True
    assert parsed.gfd_return_code == int(CCI_RETURN_CODE.SUCCESS)
    # Should contain Identify response payload (18 bytes)
    assert len(parsed.gfd_response_payload) == IdentifyResponsePayload.structure_size
    id_resp = IdentifyResponsePayload.parse(parsed.gfd_response_payload)
    assert id_resp.component_type == IdentifyComponentType.GFD

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_get_proxy_thread_status_unknown_thread():
    """GetProxyThreadStatusCommand returns INVALID_INPUT for unknown thread_id."""
    gae = _make_gae_manager()
    cmd = GetProxyThreadStatusCommand(gae)
    req = GetProxyThreadStatusCommand.create_cci_request(thread_id=999)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


@pytest.mark.asyncio
async def test_cancel_proxy_thread_success():
    """CancelProxyThreadCommand cancels in-flight thread, marks it ABORTED."""
    gfd_executor = CciExecutor(label="GFD")

    class SlowCmd(IdentifyCommand):
        async def _execute(self, r):
            await asyncio.sleep(10.0)
            return await super()._execute(r)

    gfd_executor.register_command(IdentifyCommand.OPCODE, SlowCmd(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = await _run_gfd_with_executor(gfd_executor)

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)
    tid = await gae.start_proxy(IdentifyCommand.OPCODE, b"")

    cancel_cmd = CancelProxyThreadCommand(gae)
    cancel_req = CancelProxyThreadCommand.create_cci_request(thread_id=tid)
    cancel_resp = await cancel_cmd._execute(cancel_req)
    assert cancel_resp.return_code == CCI_RETURN_CODE.SUCCESS

    # Entry should be marked ABORTED and still visible
    entry = gae.get_proxy_status(tid)
    assert entry is not None
    assert entry.completed is True
    assert entry.return_code == int(CCI_RETURN_CODE.ABORTED)

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_proxy_thread_unknown_id():
    """CancelProxyThreadCommand returns INVALID_INPUT for unknown thread_id."""
    gae = _make_gae_manager()
    cmd = CancelProxyThreadCommand(gae)
    req = CancelProxyThreadCommand.create_cci_request(thread_id=42)
    resp = await cmd._execute(req)
    assert resp.return_code == CCI_RETURN_CODE.INVALID_INPUT


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3: Full end-to-end proxy flow via DspCciTunnel + GFD cci_fifo
#         (production-mode path — no direct executor call)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_via_dsp_cci_tunnel_end_to_end():
    """
    Full production proxy path:
      GAE start_proxy()
        → DspCciTunnel.send_and_wait()
          → cci_fifo.host_to_target
            → GFD._run_cci_mailbox()
              → CciExecutor.execute_command()
            → cci_fifo.target_to_host
          → DspCciTunnel._drain_responses() resolves future
        → entry.completed = True

    This is the spec-correct path — no direct in-process calls.
    """
    # 1. Create a shared CxlConnection (GFD side has cci_fifo)
    conn = CxlConnection()

    # 2. Create GFD and start it
    gfd, _ = _make_gfd_with_conn()
    # We need to use the same conn
    gfd2 = GenericFabricDevice(
        test_mode=True, cxl_connection=conn, port_index=1,
    )
    gfd_run = asyncio.create_task(gfd2.run())
    await asyncio.wait_for(gfd2.wait_for_ready(), timeout=5.0)

    # 3. Create DspCciTunnel on the switch side (uses same conn.cci_fifo)
    tunnel = DspCciTunnel(cci_fifo=conn.cci_fifo, port_index=1)
    tunnel.start()   # starts drain_responses background task

    # 4. Create GaeManager and bind the tunnel (production path)
    gae = _make_gae_manager()
    gae.set_gfd_tunnel(tunnel)
    assert gae.has_gfd_binding()

    # 5. Start proxy — sends Identify to GFD via tunnel
    tid = await gae.start_proxy(
        gfd_opcode=IdentifyCommand.OPCODE,
        gfd_request_payload=b"",
    )
    assert tid == 1

    # 6. Wait for proxy to complete
    for _ in range(20):
        await asyncio.sleep(0.05)
        entry = gae.get_proxy_status(tid)
        if entry and entry.completed:
            break

    # 7. Verify result
    entry = gae.get_proxy_status(tid)
    assert entry is not None
    assert entry.completed is True
    assert entry.return_code == int(CCI_RETURN_CODE.SUCCESS)
    assert entry.response is not None

    # 8. Decode the GFD response
    id_resp = IdentifyResponsePayload.parse(entry.response.payload)
    assert id_resp.component_type == IdentifyComponentType.GFD

    # Cleanup
    await tunnel.stop()
    await gfd2.stop()
    gfd_run.cancel()
    try:
        await gfd_run
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_full_proxy_3_command_flow():
    """
    Complete 3-command host workflow via GaeManager:
      0x5809  Proxy GFD Mgmt → returns thread_id=1
      0x580A  Get Status     → returns in-progress initially, then completed
      0x580A  Get Status     → returns completed=True with GFD Identify payload
      0x580B  Cancel         → idempotent success (already completed)
    """
    gfd_executor = CciExecutor(label="GFD")
    gfd_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(
        IdentifyResponsePayload(component_type=IdentifyComponentType.GFD)
    ))
    t = await _run_gfd_with_executor(gfd_executor)

    gae = _make_gae_manager()
    gae.set_gfd_executor(gfd_executor)

    # --- Step 1: Proxy GFD Mgmt (0x5809) ---
    proxy_cmd = ProxyGfdMgmtCommand(gae)
    proxy_req = ProxyGfdMgmtCommand.create_cci_request(IdentifyCommand.OPCODE, b"")
    proxy_resp = await proxy_cmd._execute(proxy_req)
    assert proxy_resp.return_code == CCI_RETURN_CODE.SUCCESS
    thread_id = ProxyGfdMgmtResponsePayload.parse(proxy_resp.payload).thread_id
    assert thread_id == 1

    # --- Step 2: Wait for completion ---
    await asyncio.sleep(0.1)

    # --- Step 3: Get Proxy Thread Status (0x580A) ---
    status_cmd = GetProxyThreadStatusCommand(gae)
    status_req = GetProxyThreadStatusCommand.create_cci_request(thread_id=thread_id)
    status_resp = await status_cmd._execute(status_req)
    assert status_resp.return_code == CCI_RETURN_CODE.SUCCESS
    status = GetProxyThreadStatusResponsePayload.parse(status_resp.payload)
    assert status.completed is True
    assert status.gfd_return_code == int(CCI_RETURN_CODE.SUCCESS)
    assert len(status.gfd_response_payload) == IdentifyResponsePayload.structure_size
    id_resp = IdentifyResponsePayload.parse(status.gfd_response_payload)
    assert id_resp.component_type == IdentifyComponentType.GFD

    # --- Step 4: Cancel (0x580B) — idempotent ---
    cancel_cmd = CancelProxyThreadCommand(gae)
    cancel_req = CancelProxyThreadCommand.create_cci_request(thread_id=thread_id)
    cancel_resp = await cancel_cmd._execute(cancel_req)
    assert cancel_resp.return_code == CCI_RETURN_CODE.SUCCESS   # idempotent

    # --- Step 5: Purge ---
    purged = gae.purge_completed_threads()
    assert purged >= 1
    assert gae.thread_count() == 0

    await gfd_executor.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
