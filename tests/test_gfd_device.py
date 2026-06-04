"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

Unit tests for CxlGfdDevice (spec-correct: NO BAR, CCI-mailbox only).

CXL 4.0 §7.7.13:
  - GFD has NO BAR and NO PCIe config space.
  - GFD communicates ONLY via cci_fifo (CCI mailbox).
  - FM/GAE sends CCI requests to cci_fifo.host_to_target.
  - GFD reads request, dispatches to CciExecutor, puts response on
    cci_fifo.target_to_host.

Tests removed (compared to previous wrong implementation):
  - test_gfd_bar_size              (GFD has no BAR)
  - test_gfd_registers_device_id_sentinel (no MMIO registers)
  - test_gfd_registers_status_ready       (no MMIO registers)
  - test_gfd_scratchpad_roundtrip         (no scratchpad registers)
  - test_gfd_access_counter_increments    (no MMIO registers)

Tests kept / added:
  - test_gfd_starts_and_stops      (lifecycle still valid)
  - test_gfd_cci_identify          (Identify via cci_fifo — now spec-correct path)
  - test_gfd_cci_mailbox_dispatch  (full cci_fifo round-trip: request in, response out)
  - test_gfd_cci_unknown_opcode    (unknown opcode returns UNSUPPORTED)
  - test_gfd_cci_executor_accessible (get_cci_executor() helper)
"""

import asyncio
import struct
import pytest

from opencis.apps.generic_fabric_device import GenericFabricDevice
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.transport.cci_packets import CciMessagePacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.cci.generic.information_and_status.identify import (
    IdentifyCommand,
    IdentifyComponentType,
    IdentifyResponsePayload,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _set_log_level(caplog):
    import logging
    caplog.set_level(logging.WARNING)


def _make_gfd(serial_number: str = "0000000000000001") -> tuple:
    """
    Create a GenericFabricDevice in test_mode=True with a fresh CxlConnection.

    Returns (gfd_app, cxl_connection) where cxl_connection has:
      - cci_fifo.host_to_target  — test puts requests here
      - cci_fifo.target_to_host  — test reads GFD responses from here
    """
    conn = CxlConnection()
    gfd = GenericFabricDevice(
        test_mode=True,
        cxl_connection=conn,
        port_index=1,
        serial_number=serial_number,
    )
    return gfd, conn


async def _send_cci_request(
    conn: CxlConnection,
    opcode: int,
    payload: bytes = b"",
    tag: int = 0,
) -> CciMessagePacket:
    """
    Put a CCI request on cci_fifo.host_to_target and read the response
    from cci_fifo.target_to_host.

    Returns the raw CciMessagePacket response.
    """
    req = CciMessagePacket.create(
        data=payload,
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=opcode,
        message_tag=tag,
    )
    await conn.cci_fifo.host_to_target.put(req)
    resp = await asyncio.wait_for(
        conn.cci_fifo.target_to_host.get(),
        timeout=5.0,
    )
    return resp


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gfd_starts_and_stops():
    """GFD reaches RUNNING state and stops cleanly."""
    gfd, _ = _make_gfd()

    async def _run():
        run_task = asyncio.create_task(gfd.run())
        await asyncio.wait_for(gfd.wait_for_ready(), timeout=5.0)
        await gfd.stop()
        await run_task

    await _run()


@pytest.mark.asyncio
async def test_gfd_cci_identify():
    """
    CCI Identify (opcode 0x0001) sent via cci_fifo returns
    component_type = GFD (0x04).

    This is the spec-correct path:
      test → cci_fifo.host_to_target → GFD _run_cci_mailbox
           → CciExecutor → IdentifyCommand
           → cci_fifo.target_to_host → test reads response
    """
    gfd, conn = _make_gfd()

    async def _run():
        run_task = asyncio.create_task(gfd.run())
        await asyncio.wait_for(gfd.wait_for_ready(), timeout=5.0)

        resp = await _send_cci_request(
            conn,
            opcode=IdentifyCommand.OPCODE,
            tag=42,
        )

        # Verify headers
        assert resp.cci_msg_header.message_tag == 42
        assert resp.cci_msg_header.command_opcode == IdentifyCommand.OPCODE
        assert resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)
        assert resp.cci_msg_header.message_category == CCI_MCTP_MESSAGE_CATEGORY.RESPONSE

        # Verify payload — component_type at bytes 19 (per Identify spec)
        payload = resp.get_payload()
        id_resp = IdentifyResponsePayload.parse(payload)
        assert id_resp.component_type == IdentifyComponentType.GFD

        await gfd.stop()
        await run_task

    await _run()


@pytest.mark.asyncio
async def test_gfd_cci_mailbox_dispatch():
    """
    Full cci_fifo round-trip:
      1. Put CCI request on cci_fifo.host_to_target
      2. GFD dispatches to CciExecutor
      3. Response appears on cci_fifo.target_to_host

    Verifies message_tag echo and return_code = SUCCESS.
    """
    gfd, conn = _make_gfd()

    async def _run():
        run_task = asyncio.create_task(gfd.run())
        await asyncio.wait_for(gfd.wait_for_ready(), timeout=5.0)

        for tag in [0, 1, 127, 255]:
            resp = await _send_cci_request(
                conn,
                opcode=IdentifyCommand.OPCODE,
                tag=tag,
            )
            assert resp.cci_msg_header.message_tag == tag, \
                f"tag echo failed for tag={tag}"
            assert resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS), \
                f"expected SUCCESS for tag={tag}"

        await gfd.stop()
        await run_task

    await _run()


@pytest.mark.asyncio
async def test_gfd_cci_unknown_opcode():
    """
    Unknown opcode returns UNSUPPORTED.
    GFD should not crash; it should return a valid error response.
    """
    gfd, conn = _make_gfd()

    async def _run():
        run_task = asyncio.create_task(gfd.run())
        await asyncio.wait_for(gfd.wait_for_ready(), timeout=5.0)

        resp = await _send_cci_request(
            conn,
            opcode=0xDEAD,  # unknown opcode
            tag=7,
        )

        assert resp.cci_msg_header.message_tag == 7
        assert resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.UNSUPPORTED)

        await gfd.stop()
        await run_task

    await _run()


@pytest.mark.asyncio
async def test_gfd_cci_executor_accessible():
    """
    get_cci_executor() returns a live CciExecutor that has Identify registered.
    """
    gfd, _ = _make_gfd()
    executor = gfd.get_gfd_device().get_cci_executor()
    assert executor is not None
    # Identify should be registered
    assert IdentifyCommand.OPCODE in executor._commands


@pytest.mark.asyncio
async def test_gfd_no_bar():
    """
    GFD must NOT expose any BAR-related attributes.
    Confirms the spec-correct behaviour: GFD has no host-visible registers.
    """
    gfd, _ = _make_gfd()
    device = gfd.get_gfd_device()
    # These must NOT exist on the spec-correct GFD
    assert not hasattr(device, "get_bar_size"), \
        "GFD must not have get_bar_size() — GFD has no BAR per CXL 4.0 §7.7.13"
    assert not hasattr(device, "get_registers"), \
        "GFD must not have get_registers() — GFD has no MMIO registers"
    assert not hasattr(device, "_gfd_registers"), \
        "GFD must not have _gfd_registers — GFD has no BAR"
    assert not hasattr(device, "_cxl_io_manager"), \
        "GFD must not have _cxl_io_manager — GFD is not a PCIe endpoint"
