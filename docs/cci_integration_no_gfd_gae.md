# CXL PBR & GAE/GFD CCI Command Integration Guide (Python)

This guide provides an incremental, command-by-command approach to integrate and test CXL 4.0 Port-Based Routing (PBR) and Generic Access Endpoint (GAE) command sets in Python. 

The values used in this guide correspond to the standard 1 Host + 1 SLD + 1 GFD topology configured in [`configs/1vcs_1sld_1gfd.yaml`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/configs/1vcs_1sld_1gfd.yaml).

---

## 1. Demo Topology Context

To run the end-to-end demo successfully, we configure the switch with the following topology parameters:

| Component | Index / ID | Description |
|---|---|---|
| **USP Port** | `0` | Upstream Port connecting the Host to the Switch. The GAE lives here. |
| **DSP Port 1** | `1` | Downstream Port connected to the Single Logical Device (SLD). |
| **DSP Port 2** | `2` | Downstream Port connected to the Generic Fabric Device (GFD). |
| **VCS ID** | `0` | Virtual CXL Switch ID. |
| **vPPB 0** | `0` | Virtual PCI-to-PCI Bridge representing DSP Port 1 (SLD). |
| **vPPB 1** | `1` | Virtual PCI-to-PCI Bridge representing DSP Port 2 (GFD). |
| **GFD PID** | `0x010` | 12-bit Port Identifier assigned to DSP Port 2 (GFD). |
| **DRT Index** | `0` | DPID Routing Table index. |

---

## 2. Prerequisites and Import Configuration

Ensure your Python environment has `opencis-core` installed. If you haven't done so, install the package in editable mode:
```bash
cd opencis-core
pip install -e .
```

The core imports used to build and frame CCI requests are:
```python
import asyncio
from opencis.cxl.cci.common import (
    CCI_FM_API_COMMAND_OPCODE,
    CCI_GAE_COMMAND_OPCODE,
    CCI_RETURN_CODE,
)
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.cxl.transport.packet_structs import SystemHeader
from opencis.cxl.transport.common import BasePacket
```

---

## 3. Core Transport Round-Trip Client

For direct MCTP-over-TCP connection (default FM Port `8300`), use this helper function to handle connection, serialization, packet framing, write draining, and response parsing:

```python
async def cci_round_trip(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    opcode: int,
    payload: bytes = b"",
    tag: int = 1
) -> CciMessagePacket:
    """
    Frames a CCI request, writes it to the socket, reads the response header
    and body, and returns the parsed CciMessagePacket.
    """
    # 1. Build and send request packet
    req = CciMessagePacket.create(
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=opcode,
        data=payload,
        message_tag=tag,
    )
    writer.write(bytes(CciPayloadPacket.create(req)))
    await writer.drain()

    # 2. Read 2-byte SystemHeader to determine total packet length
    hdr = await reader.readexactly(SystemHeader.get_size())
    base = BasePacket(bytearray(hdr))
    
    # 3. Read the rest of the packet body
    body_len = max(0, base.system_header.payload_length - len(base))
    body = await reader.readexactly(body_len)
    
    # 4. Return the parsed CCI message
    return CciPayloadPacket(bytearray(hdr + body)).get_cci_message()
```

---

## 4. Incremental Command Integration Steps

Follow these steps sequentially to test each command against the running Fabric Manager.

### Step 1: Query Switch Identity (`IDENTIFY_PBR_SWITCH` — Opcode `0x5700`)
Query the basic capabilities of the switch. This command does not modify switch state, making it ideal to verify connection setup and parsing.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.identify_pbr_switch import (
    IdentifyPbrSwitchCommand,
    IdentifyPbrSwitchResponsePayload,
)

async def test_step1_identify_switch(reader, writer):
    print("[Step 1] IDENTIFY_PBR_SWITCH (0x5700)")
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.IDENTIFY_PBR_SWITCH,
        tag=1
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        payload = IdentifyPbrSwitchCommand.parse_response_payload(resp_msg.get_payload())
        print(payload.get_pretty_print())
```

---

### Step 2: Query GAE Identity (`IDENTIFY_GAE` — Opcode `0x5800`)
Query GAE virtual physical port bridge (vPPB) mappings and their support status.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.gae.identify_gae import (
    IdentifyGaeCommand,
    IdentifyGaeResponsePayload,
)

async def test_step2_identify_gae(reader, writer):
    print("[Step 2] IDENTIFY_GAE (0x5800)")
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_GAE_COMMAND_OPCODE.IDENTIFY_GAE,
        tag=2
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        payload = IdentifyGaeCommand.parse_response_payload(resp_msg.get_payload())
        print(payload.get_pretty_print())
```

---

### Step 3: Assign PID to GFD Port (`CONFIGURE_PID_ASSIGNMENT` — Opcode `0x5704`)
Map a 12-bit Port Identifier (PID) to physical Port 2 on the switch where the GFD device is attached.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentCommand,
    ConfigurePidAssignmentRequestPayload,
    PidAssignmentEntry,
    PidAssignmentOperation,
)

async def test_step3_configure_pid(reader, writer):
    print("[Step 3] CONFIGURE_PID_ASSIGNMENT (0x5704) — PID 0x010 -> Port 2 (GFD)")
    
    # Map PID 0x010 to physical Port 2 (where the GFD is attached)
    payload = ConfigurePidAssignmentRequestPayload(
        operation=PidAssignmentOperation.ASSIGN,
        entries=[PidAssignmentEntry(pid=0x010, target_id=2, instance_id=0)]
    )
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_ASSIGNMENT,
        payload=payload.dump(),
        tag=3
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
```

---

### Step 4: Query GAE Access Vectors (`GET_PID_ACCESS_VECTORS` — Opcode `0x5802`)
Query valid target vectors (VTV) and Global Memory vectors (GMV) for the assigned GFD PID `0x010`.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.gae.get_pid_access_vectors import (
    GetPidAccessVectorsCommand,
    GetPidAccessVectorsResponsePayload,
)

async def test_step4_get_vectors(reader, writer):
    print("[Step 4] GET_PID_ACCESS_VECTORS (0x5802) — PID 0x010")
    
    # Request vectors for PID 0x010
    req = GetPidAccessVectorsCommand.create_cci_request(pid=0x010)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=req.opcode,
        payload=req.payload,
        tag=4
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        payload = GetPidAccessVectorsCommand.parse_response_payload(resp_msg.get_payload())
        print(payload.get_pretty_print())
```

---

### Step 5: Program Switch Routing Table (`SET_DRT` — Opcode `0x5709`)
Configure the Destination Routing Table (DRT). We map the programmed PID `0x010` index in DRT 0 to point to physical Port 2.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import (
    SetDrtCommand,
    SetDrtRequestPayload,
)
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType

async def test_step5_set_drt(reader, writer):
    print("[Step 5] SET_DRT (0x5709) — DRT[0x010] = Port 2")
    
    payload = SetDrtRequestPayload(
        drt_index=0,
        start_entry=0x010,
        entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=2)]
    )
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.SET_DRT,
        payload=payload.dump(),
        tag=5
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
```

---

### Step 6: Verify Routing Table Entry (`GET_DRT` — Opcode `0x5708`)
Read the entry back from the DRT to verify it was written correctly in Step 5.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import (
    GetDrtCommand,
    GetDrtRequestPayload,
)

async def test_step6_get_drt(reader, writer):
    print("[Step 6] GET_DRT (0x5708) — Query DRT[0x010]")
    
    payload = GetDrtRequestPayload(drt_index=0, num_entries=1, start_entry=0x010)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.GET_DRT,
        payload=payload.dump(),
        tag=6
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        resp_payload = GetDrtCommand.parse_response_payload(resp_msg.get_payload())
        print(resp_payload.get_pretty_print())
```

---

### Step 7: Query Initial Binding (`GET_PID_BINDING` — Opcode `0x5705`)
Query the binding of vPPB 1 (which represents GFD on Port 2) before setting it. Since we have not bound it yet, the response PID should be `0xFFF` (unbound).

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import (
    GetPidBindingCommand,
    GetPidBindingRequestPayload,
)

async def test_step7_query_initial_binding(reader, writer):
    print("[Step 7] GET_PID_BINDING (0x5705) - Pre-bind Check (vPPB 1)")
    
    # Query VCS 0, vPPB 1 (representing Port 2 / GFD)
    payload = GetPidBindingRequestPayload(target_vcs=0, target_vppb=1)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING,
        payload=payload.dump(),
        tag=7
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        resp_payload = GetPidBindingCommand.parse_response_payload(resp_msg.get_payload())
        print(f"   Bound PID: {resp_payload.pid:#05x} (Expected: 0xfff / UNBOUND)")
```

---

### Step 8: Bind Virtual Port to GFD Endpoint (`CONFIGURE_PID_BINDING` — Opcode `0x5706`)
Bind Virtual Port Bridge (vPPB) 1 on VCS 0 to GFD PID `0x010`.

> [!NOTE]
> `CONFIGURE_PID_BINDING` is a **Background Command**. 
> The FM will return `BACKGROUND_COMMAND_STARTED` (`0x0001`) immediately. The binding executes asynchronously.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingCommand,
    ConfigurePidBindingRequestPayload,
    PidBindingOperation,
)

async def test_step8_bind_vppb(reader, writer):
    print("[Step 8] CONFIGURE_PID_BINDING (0x5706) — Bind vPPB 1 -> PID 0x010")
    
    # Bind VCS 0, vPPB 1 to target PID 0x010
    payload = ConfigurePidBindingRequestPayload(
        operation=PidBindingOperation.BIND,
        target_vcs=0,
        target_vppb=1,
        pid=0x010
    )
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_BINDING,
        payload=payload.dump(),
        tag=8
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    # Expected: BACKGROUND_COMMAND_STARTED
    
    # Pause 50ms to allow background task execution to settle
    await asyncio.sleep(0.05)
```

---

### Step 9: Tunnel CCI Command to GFD (`PROXY_GFD_MGMT_CMD` — Opcode `0x5809`)
Issue a proxy command through the GAE USP down to the GFD device mailbox. We forward the GFD Identify command (`0x0001`).

> [!NOTE]
> Like bindings, proxying is handled as an async background task. 
> The FM will return `BACKGROUND_COMMAND_STARTED` (`0x0001`) immediately with a 2-byte `thread_id` in the output payload.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.gae.proxy_gfd_mgmt import (
    ProxyGfdMgmtCommand,
    ProxyGfdMgmtResponsePayload,
)

async def test_step9_proxy_gfd_command(reader, writer) -> int:
    print("[Step 9] PROXY_GFD_MGMT_CMD (0x5809) — Forward GFD Identify Opcode 0x0001")
    
    # GFD command: IDENTIFY_GFD (opcode 0x0001, payload empty)
    req = ProxyGfdMgmtCommand.create_cci_request(gfd_opcode=0x0001, gfd_payload=b"")
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=req.opcode,
        payload=req.payload,
        tag=9
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    thread_id = 0
    if rc == CCI_RETURN_CODE.SUCCESS or rc == CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED:
        resp_payload = ProxyGfdMgmtCommand.parse_response_payload(resp_msg.get_payload())
        thread_id = resp_payload.thread_id
        print(f"   Assigned Proxy Thread ID: {thread_id}")
        
    return thread_id
```

---

### Step 10: Query Tunnel Status (`GET_PROXY_THREAD_STATUS` — Opcode `0x580A`)
Poll the thread status using the `thread_id` from Step 9. When `completed = True`, it contains the GFD's response payload.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.gae.get_proxy_thread_status import (
    GetProxyThreadStatusCommand,
    GetProxyThreadStatusResponsePayload,
)

async def test_step10_get_proxy_status(reader, writer, thread_id: int):
    print(f"[Step 10] GET_PROXY_THREAD_STATUS (0x580A) — Thread ID {thread_id}")
    if thread_id == 0:
        print("   Skipping (invalid thread ID).")
        return
        
    req = GetProxyThreadStatusCommand.create_cci_request(thread_id=thread_id)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=req.opcode,
        payload=req.payload,
        tag=10
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        payload = GetProxyThreadStatusCommand.parse_response_payload(resp_msg.get_payload())
        print(payload.get_pretty_print())
        if payload.completed:
            # GFD Identify Response Payload contains the GFD properties:
            # - component_type (offset 0): 0x04 (IdentifyComponentType.GFD)
            # - serial_number (offset 16): 64-bit serial
            print(f"   GFD Response Payload: {payload.gfd_response_payload.hex()}")
            if len(payload.gfd_response_payload) > 0:
                comp_type = payload.gfd_response_payload[0]
                print(f"   GFD Component Type: {comp_type:#04x} (Expected: 0x04 / GFD)")
```

---

### Step 11: Verify Final Binding (`GET_PID_BINDING` — Opcode `0x5705`)
Query the binding of VCS 0, vPPB 1 again. It should now report that it is bound to PID `0x010`.

#### Implementation & Verification
```python
async def test_step11_verify_binding(reader, writer):
    print("[Step 11] GET_PID_BINDING (0x5705) - Post-bind Verification (vPPB 1)")
    
    payload = GetPidBindingRequestPayload(target_vcs=0, target_vppb=1)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING,
        payload=payload.dump(),
        tag=11
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        resp_payload = GetPidBindingCommand.parse_response_payload(resp_msg.get_payload())
        print(f"   Bound PID: {resp_payload.pid:#05x} (Expected: 0x010 / BOUND)")
        if resp_payload.pid == 0x010:
            print("   → SUCCESS: Switch & GAE/GFD Control Plane Commissioning Complete!")
        else:
            print("   → FAIL: Unexpected bound PID")
```

---

## 5. Standalone Combined Test Harness

Save the following code as `test_pbr_commissioning.py`. Running it will connect to a running Fabric Manager instance on port `8300` and execute all 11 steps sequentially.

```python
"""
test_pbr_commissioning.py - Incremental CXL Switch and GAE/GFD Commissioning Script
"""
import asyncio
from opencis.cxl.cci.common import (
    CCI_FM_API_COMMAND_OPCODE,
    CCI_GAE_COMMAND_OPCODE,
    CCI_RETURN_CODE,
)
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.cxl.transport.packet_structs import SystemHeader
from opencis.cxl.transport.common import BasePacket

# PBR Imports
from opencis.cxl.cci.fabric_manager.pbr_switch.identify_pbr_switch import (
    IdentifyPbrSwitchCommand
)
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentCommand,
    ConfigurePidAssignmentRequestPayload,
    PidAssignmentEntry,
    PidAssignmentOperation,
)
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import (
    GetPidBindingCommand,
    GetPidBindingRequestPayload,
)
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import (
    SetDrtCommand,
    SetDrtRequestPayload,
)
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import (
    GetDrtCommand,
    GetDrtRequestPayload,
)
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingCommand,
    ConfigurePidBindingRequestPayload,
    PidBindingOperation,
)

# GAE Imports
from opencis.cxl.cci.fabric_manager.gae.identify_gae import (
    IdentifyGaeCommand
)
from opencis.cxl.cci.fabric_manager.gae.get_pid_access_vectors import (
    GetPidAccessVectorsCommand
)
from opencis.cxl.cci.fabric_manager.gae.proxy_gfd_mgmt import (
    ProxyGfdMgmtCommand
)
from opencis.cxl.cci.fabric_manager.gae.get_proxy_thread_status import (
    GetProxyThreadStatusCommand
)

FM_HOST = "127.0.0.1"
FM_PORT = 8300

async def cci_round_trip(reader, writer, opcode, payload=b"", tag=1):
    req = CciMessagePacket.create(
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=opcode,
        data=payload,
        message_tag=tag,
    )
    writer.write(bytes(CciPayloadPacket.create(req)))
    await writer.drain()

    hdr = await reader.readexactly(SystemHeader.get_size())
    base = BasePacket(bytearray(hdr))
    body = await reader.readexactly(max(0, base.system_header.payload_length - len(base)))
    return CciPayloadPacket(bytearray(hdr + body)).get_cci_message()

async def main():
    print(f"Connecting to Fabric Manager at {FM_HOST}:{FM_PORT}...")
    try:
        reader, writer = await asyncio.open_connection(FM_HOST, FM_PORT)
    except Exception as e:
        print(f"Connection failed: {e}. Is Fabric Manager running?")
        return
    print("Connected.\n")

    # Run steps sequentially
    await test_step1_identify_switch(reader, writer)
    await asyncio.sleep(0.01)

    await test_step2_identify_gae(reader, writer)
    await asyncio.sleep(0.01)

    await test_step3_configure_pid(reader, writer)
    await asyncio.sleep(0.01)

    await test_step4_get_vectors(reader, writer)
    await asyncio.sleep(0.01)

    await test_step5_set_drt(reader, writer)
    await asyncio.sleep(0.01)

    await test_step6_get_drt(reader, writer)
    await asyncio.sleep(0.01)

    await test_step7_query_initial_binding(reader, writer)
    await asyncio.sleep(0.01)

    await test_step8_bind_vppb(reader, writer)
    await asyncio.sleep(0.01)

    thread_id = await test_step9_proxy_gfd_command(reader, writer)
    # Wait for GFD transaction to complete
    await asyncio.sleep(0.2)

    await test_step10_get_proxy_status(reader, writer, thread_id)
    await asyncio.sleep(0.01)

    await test_step11_verify_binding(reader, writer)

    print("\nClosing connection.")
    writer.close()
    await writer.wait_closed()

# [Paste Step 1 to Step 11 functions here]

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. Execution and Server Verification

1. **Start the Fabric Manager Environment:**
   Run the Fabric Manager with a config defining your switch, GAE, and GFD topology:
   ```bash
   python run_pbr_env.py --config-file configs/1vcs_1sld_1gfd.yaml
   ```

2. **Execute the Commissioning client:**
   In a separate terminal tab, run your Python script:
   ```bash
   python test_pbr_commissioning.py
   ```

3. **Verify Output logs:**
   On the test client terminal, you should see SUCCESS for all 11 steps.
   * `Bound PID: 0xfff` in Step 7.
   * `GFD Component Type: 0x04` in Step 10.
   * `Bound PID: 0x010` in Step 11.
