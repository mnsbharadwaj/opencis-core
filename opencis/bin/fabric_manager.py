"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio
import click

from opencis.util.logger import logger
from opencis.apps.fabric_manager import CxlFabricManager
from opencis.bin import socketio_client
from opencis.bin.common import BASED_INT


# Fabric Manager command group
@click.group(name="fm")
def fabric_manager_group():
    """Command group for Fabric Manager."""


@fabric_manager_group.command(name="start")
@click.option("--use-test-runner", is_flag=True, help="Run with the test runner.")
@click.option("--config-file", help="<Config File> input path.")
def start(use_test_runner, config_file):
    """Run the Fabric Manager."""
    logger.info("Starting CXL FabricManager")
    fabric_manager = CxlFabricManager(use_test_runner=use_test_runner, config_file=config_file)
    try:
        asyncio.run(fabric_manager.run())
    except SystemExit:
        # Signal handler called sys.exit() - process is terminating, don't try to stop
        # in a new event loop as it would fail with "bound to different event loop"
        pass
    except Exception as e:
        logger.error("Error while running CXL FabricManager", exc_info=e)
        try:
            asyncio.run(fabric_manager.stop())
        except Exception as stop_e:
            logger.error("Error while stopping CXL FabricManager", exc_info=stop_e)


@fabric_manager_group.command(name="get-port")
def get_port():
    """Get physical port states."""
    asyncio.run(socketio_client.get_port())


@fabric_manager_group.command(name="get-vcs")
def get_vcs():
    """Get virtual CXL switch info."""
    asyncio.run(socketio_client.get_vcs())


@fabric_manager_group.command(name="get-device")
def get_device():
    """Get connected devices."""
    asyncio.run(socketio_client.get_device())


@fabric_manager_group.command(name="bind")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
@click.argument("physical", nargs=1, type=BASED_INT)
@click.argument(
    "ld_id",
    nargs=1,
    type=BASED_INT,
    default=0,
)
def fm_bind(vcs: int, vppb: int, physical: int, ld_id: int):
    asyncio.run(socketio_client.bind(vcs, vppb, physical, ld_id))


@fabric_manager_group.command(name="unbind")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
def fm_unbind(vcs: int, vppb: int):
    asyncio.run(socketio_client.unbind(vcs, vppb))


@fabric_manager_group.command(name="get-ld-info")
@click.argument("port_index", nargs=1, type=BASED_INT)
def get_ld_info(port_index: int):
    asyncio.run(socketio_client.get_ld_info(port_index))


@fabric_manager_group.command(name="get-ld-allocations")
@click.argument("port_index", nargs=1, type=BASED_INT)
@click.argument("start_ld_id", nargs=1, type=BASED_INT)
@click.argument("ld_allocation_list_limit", nargs=1, type=BASED_INT)
def get_ld_allocation(port_index: int, start_ld_id: int, ld_allocation_list_limit: int):
    asyncio.run(
        socketio_client.get_ld_allocation(port_index, start_ld_id, ld_allocation_list_limit)
    )


@fabric_manager_group.command(name="freeze")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
def fm_freeze(vcs: int, vppb: int):
    asyncio.run(socketio_client.freeze(vcs, vppb))


@fabric_manager_group.command(name="unfreeze")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
def fm_unfreeze(vcs: int, vppb: int):
    asyncio.run(socketio_client.unfreeze(vcs, vppb))


# TODO: Implement set_ld_allocation
@fabric_manager_group.command(name="set-ld-allocation")
@click.argument("port_index", nargs=1, type=BASED_INT)
@click.argument("number_of_lds", nargs=1, type=BASED_INT)
@click.argument("start_ld_id", nargs=1, type=BASED_INT)
@click.argument("ld_allocation_list", nargs=1, type=BASED_INT)
def set_ld_allocation(
    port_index: int, number_of_lds: int, start_ld_id: int, ld_allocation_list: int
):
    asyncio.run(
        socketio_client.set_ld_allocation(
            port_index, number_of_lds, start_ld_id, ld_allocation_list
        )
    )


@fabric_manager_group.command(name="background-status")
def background_status():
    """Check the status of background commands."""
    asyncio.run(socketio_client.get_background_status())


@fabric_manager_group.command(name="pbr-identify")
def pbr_identify():
    """Identify PBR Switch (Opcode 5700h)."""
    asyncio.run(socketio_client.pbr_identify())


@fabric_manager_group.command(name="pbr-configure-pid")
@click.option("--operation", type=click.Choice(["assign", "clear"]), default="assign")
@click.option("--instance-id", type=BASED_INT, default=0)
@click.argument("pid", type=BASED_INT)
@click.argument("target_id", type=BASED_INT, default=0)
def pbr_configure_pid(operation, instance_id, pid, target_id):
    """Configure PID Assignment (Opcode 5704h)."""
    op_val = 0 if operation == "assign" else 1
    entries = [{"pid": pid, "targetId": target_id, "instanceId": instance_id}]
    asyncio.run(socketio_client.pbr_configure_pid(op_val, entries))


@fabric_manager_group.command(name="pbr-get-pid-binding")
@click.argument("vcs", type=BASED_INT)
@click.argument("vppb", type=BASED_INT)
def pbr_get_pid_binding(vcs, vppb):
    """Get PID Binding (Opcode 5705h)."""
    asyncio.run(socketio_client.pbr_get_pid_binding(vcs, vppb))


@fabric_manager_group.command(name="pbr-configure-pid-binding")
@click.option("--operation", type=click.Choice(["bind", "unbind"]), default="bind")
@click.option("--latency-base", type=BASED_INT, default=0)
@click.option("--latency", type=BASED_INT, default=0)
@click.option("--bw-base", type=BASED_INT, default=0)
@click.option("--bw", type=BASED_INT, default=0)
@click.argument("vcs", type=BASED_INT)
@click.argument("vppb", type=BASED_INT)
@click.argument("pid", type=BASED_INT)
def pbr_configure_pid_binding(operation, latency_base, latency, bw_base, bw, vcs, vppb, pid):
    """Configure PID Binding (Opcode 5706h)."""
    op_val = 0 if operation == "bind" else 1
    asyncio.run(
        socketio_client.pbr_configure_pid_binding(
            operation=op_val,
            target_vcs=vcs,
            target_vppb=vppb,
            pid=pid,
            latency_entry_base_unit=latency_base,
            latency_entry=latency,
            bw_entry_base_unit=bw_base,
            bw_entry=bw,
        )
    )


@fabric_manager_group.command(name="pbr-get-drt")
@click.argument("drt_index", type=BASED_INT)
@click.argument("start_entry", type=BASED_INT)
@click.argument("num_entries", type=BASED_INT)
def pbr_get_drt(drt_index, start_entry, num_entries):
    """Get DRT Table Entries (Opcode 5708h)."""
    asyncio.run(socketio_client.pbr_get_drt(drt_index, start_entry, num_entries))


@fabric_manager_group.command(name="pbr-set-drt")
@click.option("--entry-type", type=click.Choice(["physical", "rgt", "invalid"]), default="physical")
@click.option("--routing-target", type=BASED_INT, default=0)
@click.argument("drt_index", type=BASED_INT)
@click.argument("start_entry", type=BASED_INT)
def pbr_set_drt(entry_type, routing_target, drt_index, start_entry):
    """Set DRT Table Entry (Opcode 5709h)."""
    type_map = {
        "physical": "PHYSICAL_PORT",
        "rgt": "RGT_INDEX",
        "invalid": "INVALID",
    }
    entries = [{"entryType": type_map[entry_type], "routingTarget": routing_target}]
    asyncio.run(socketio_client.pbr_set_drt(drt_index, start_entry, entries))


@fabric_manager_group.command(name="gae-identify")
def gae_identify():
    """Identify GAE (Opcode 5800h)."""
    asyncio.run(socketio_client.gae_identify())


@fabric_manager_group.command(name="gae-get-pid-access-vectors")
@click.argument("pid", type=BASED_INT)
def gae_get_pid_access_vectors(pid):
    """Get PID Access Vectors (Opcode 5802h)."""
    asyncio.run(socketio_client.gae_get_pid_access_vectors(pid))


@fabric_manager_group.command(name="gae-proxy-gfd-mgmt")
@click.option("--payload", help="Comma-separated byte values, e.g. '0x01,0x02'")
@click.argument("gfd_opcode", type=BASED_INT)
def gae_proxy_gfd_mgmt(payload, gfd_opcode):
    """Proxy GFD Management (Opcode 5809h)."""
    gfd_payload = []
    if payload:
        gfd_payload = [int(x.strip(), 0) for x in payload.split(",")]
    asyncio.run(socketio_client.gae_proxy_gfd_mgmt(gfd_opcode, gfd_payload))


@fabric_manager_group.command(name="gae-get-proxy-status")
@click.argument("thread_id", type=BASED_INT)
def gae_get_proxy_status(thread_id):
    """Get Proxy Thread Status (Opcode 580Ah)."""
    asyncio.run(socketio_client.gae_get_proxy_status(thread_id))


@fabric_manager_group.command(name="gae-cancel-proxy")
@click.argument("thread_id", type=BASED_INT)
def gae_cancel_proxy(thread_id):
    """Cancel Proxy Thread (Opcode 580Bh)."""
    asyncio.run(socketio_client.gae_cancel_proxy(thread_id))


@fabric_manager_group.command(name="gae-fabric-crawl-out")
@click.option("--payload", help="Comma-separated byte values, e.g. '0x01,0x02'")
@click.argument("target_port", type=BASED_INT)
@click.argument("gfd_opcode", type=BASED_INT)
def gae_fabric_crawl_out(payload, target_port, gfd_opcode):
    """Fabric Crawl Out (Opcode 5701h)."""
    gfd_payload = []
    if payload:
        gfd_payload = [int(x.strip(), 0) for x in payload.split(",")]
    asyncio.run(
        socketio_client.gae_fabric_crawl_out(
            target_port=target_port, gfd_opcode=gfd_opcode, gfd_payload=gfd_payload
        )
    )


@fabric_manager_group.command(name="test-dynamic-ld")
def test_dynamic_ld():
    """Test dynamic LD allocation with empty configuration."""
    print("Testing dynamic LD allocation...")
    print("This will create logical devices dynamically using the Set LD Allocation command.")

    async def test():
        # Create first LD
        await socketio_client.set_ld_allocation(
            port_index=1,
            number_of_lds=1,
            start_ld_id=0,
            ld_allocation_list=[{"range1": 16384, "range2": 0}],
        )
        print("✅ Created first LD (ID: 16384)")

        # Create multiple LDs
        await socketio_client.set_ld_allocation(
            port_index=1,
            number_of_lds=2,
            start_ld_id=1,
            ld_allocation_list=[{"range1": 16385, "range2": 0}, {"range1": 16386, "range2": 0}],
        )
        print("✅ Created additional LDs (IDs: 16385, 16386)")

        print("Dynamic LD allocation test completed successfully!")

    asyncio.run(test())
