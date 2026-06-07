"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio
import sys
import socketio
from yaml import dump

# Standard Python client setup for Socket.IO
sio = socketio.AsyncClient()


@sio.on("port:updated")
def handle_port_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("vcs:updated")
def handle_vcs_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("device:updated")
def handle_device_updated():
    print("[Notification]")
    print("device:updated")


class CustomSemaphore(asyncio.Semaphore):
    def __init__(self, value=0, custom_value=None):
        super().__init__(value)
        self.custom_value = custom_value

    def set_custom_value(self, value):
        self.custom_value = value


async def send(event, param=None):
    sema = CustomSemaphore()

    def callback_handler(result):
        sema.set_custom_value(result)
        sema.release()

    print("[Request]")
    print(event)
    await sio.emit(event, param, callback=callback_handler)
    await sema.acquire()
    result = sema.custom_value

    print("[Response]")
    print_result(result)
    return result


def print_result(data):
    print(dump(data, sort_keys=False, default_flow_style=False))


# Connect event handler
@sio.event
async def connect():
    print("Connected to the server")


# Disconnect event handler
@sio.event
def disconnect():
    print("Disconnected from server")


async def get_port():
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "port:get",
    )
    await sio.disconnect()


async def get_vcs():
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:get",
    )
    await sio.disconnect()


async def get_device():
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "device:get",
    )
    await sio.disconnect()


# Bind & unbind
async def bind(vcs: int, vppb: int, physical_port: int, ld_id: int = 0):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb, "physicalPortId": physical_port, "ldId": ld_id},
    )
    await sio.disconnect()


async def unbind(vcs: int, vppb: int):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:unbind",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb},
    )
    await sio.disconnect()


async def get_ld_info(port_index: int):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "mld:get",
        {"portIndex": port_index},
    )
    await sio.disconnect()


async def get_ld_allocation(port_index: int, start_ld_id: int, ld_allocation_list_limit: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "mld:getAllocation",
        {
            "portIndex": port_index,
            "startLdId": start_ld_id,
            "ldAllocationListLimit": ld_allocation_list_limit,
        },
    )
    await sio.disconnect()
    return result


async def set_ld_allocation(
    port_index: int, number_of_lds: int, start_ld_id: int, ld_allocation_list: list
):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "mld:setAllocation",
        {
            "portIndex": port_index,
            "numberOfLds": number_of_lds,
            "startLdId": start_ld_id,
            "ldAllocationList": ld_allocation_list,
        },
    )
    await sio.disconnect()


async def get_background_status():
    await sio.connect("http://0.0.0.0:8200")
    await send("background:getStatus", {})
    await sio.disconnect()


async def freeze(vcs: int, vppb: int):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:freeze",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb},
    )
    await sio.disconnect()


async def unfreeze(vcs: int, vppb: int):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:unfreeze",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb},
    )
    await sio.disconnect()


async def pbr_identify():
    await sio.connect("http://0.0.0.0:8200")
    result = await send("pbr:identify")
    await sio.disconnect()
    return result


async def pbr_configure_pid(operation: int, entries: list):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "pbr:configurePid",
        {"operation": operation, "entries": entries},
    )
    await sio.disconnect()
    return result


async def pbr_get_pid_binding(target_vcs: int, target_vppb: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "pbr:getPidBinding",
        {"targetVcs": target_vcs, "targetVppb": target_vppb},
    )
    await sio.disconnect()
    return result


async def pbr_configure_pid_binding(
    operation: int,
    target_vcs: int,
    target_vppb: int,
    pid: int,
    latency_entry_base_unit: int = 0,
    latency_entry: int = 0,
    bw_entry_base_unit: int = 0,
    bw_entry: int = 0,
):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "pbr:configurePidBinding",
        {
            "operation": operation,
            "targetVcs": target_vcs,
            "targetVppb": target_vppb,
            "pid": pid,
            "latencyEntryBaseUnit": latency_entry_base_unit,
            "latencyEntry": latency_entry,
            "bwEntryBaseUnit": bw_entry_base_unit,
            "bwEntry": bw_entry,
        },
    )
    await sio.disconnect()
    return result


async def pbr_get_drt(drt_index: int, start_entry: int, num_entries: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "pbr:getDrt",
        {"drtIndex": drt_index, "startEntry": start_entry, "numEntries": num_entries},
    )
    await sio.disconnect()
    return result


async def pbr_set_drt(drt_index: int, start_entry: int, entries: list):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "pbr:setDrt",
        {"drtIndex": drt_index, "startEntry": start_entry, "entries": entries},
    )
    await sio.disconnect()
    return result


async def gae_identify():
    await sio.connect("http://0.0.0.0:8200")
    result = await send("gae:identify")
    await sio.disconnect()
    return result


async def gae_get_pid_access_vectors(pid: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "gae:getPidAccessVectors",
        {"pid": pid},
    )
    await sio.disconnect()
    return result


async def gae_proxy_gfd_mgmt(gfd_opcode: int, gfd_payload: list):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "gae:proxyGfdMgmt",
        {"gfdOpcode": gfd_opcode, "gfdPayload": gfd_payload},
    )
    await sio.disconnect()
    return result


async def gae_get_proxy_status(thread_id: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "gae:getProxyStatus",
        {"threadId": thread_id},
    )
    await sio.disconnect()
    return result


async def gae_cancel_proxy(thread_id: int):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "gae:cancelProxy",
        {"threadId": thread_id},
    )
    await sio.disconnect()
    return result


async def gae_fabric_crawl_out(target_port: int, gfd_opcode: int, gfd_payload: list):
    await sio.connect("http://0.0.0.0:8200")
    result = await send(
        "gae:fabricCrawlOut",
        {"targetPort": target_port, "gfdOpcode": gfd_opcode, "gfdPayload": gfd_payload},
    )
    await sio.disconnect()
    return result


# Main asynchronous function to start the client
async def start_client():
    await sio.connect("http://0.0.0.0:8200")
    await sio.wait()


# Stop the client gracefully
async def stop_client():
    await sio.disconnect()
    sys.exit()


# Run the client
if __name__ == "__main__":
    asyncio.run(start_client())
