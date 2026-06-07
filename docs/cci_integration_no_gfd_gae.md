# CXL PBR CCI Command Integration Guide (Python - No GFD/GAE)

This guide provides an incremental, command-by-command approach to integrate and test CXL 4.0 Port-Based Routing (PBR) commands using Python. It is designed for setups **without Generic Fabric Device (GFD) or Generic Access Endpoint (GAE)** implementations.

By implementing and testing these commands one by one, you can ensure compatibility, field alignment, and transport layer correctness in your Python setup before moving on.

---

## 1. Prerequisites and Import Configuration

Ensure your python environment has `opencis-core` installed. If you haven't done so, install the package in editable mode:
```bash
cd opencis-core
pip install -e .
```

The core imports used to build and frame CCI requests are:
```python
import asyncio
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.cxl.transport.packet_structs import SystemHeader
from opencis.cxl.transport.common import BasePacket
```

---

## 2. Core Transport Round-Trip Client

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

## 3. Incremental Command Integration Steps

Follow these steps sequentially to test each command against the running Fabric Manager.

### Step 1: Query Switch Identity (`IDENTIFY_PBR_SWITCH` — Opcode `0x5700`)
Query the basic capabilities of the switch. This command does not modify switch state, making it ideal to verify connection setup and parsing.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.identify_pbr_switch import (
    IdentifyPbrSwitchCommand,
    IdentifyPbrSwitchResponsePayload,
)

async def test_step1_identify(reader, writer):
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
        # Expected: num_drts = 1 or 2, gae_support_map = 0
```

---

### Step 2: Assign PID to Port (`CONFIGURE_PID_ASSIGNMENT` — Opcode `0x5704`)
Map a 12-bit Port Identifier (PID) to a physical port on the switch. 

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentCommand,
    ConfigurePidAssignmentRequestPayload,
    PidAssignmentEntry,
    PidAssignmentOperation,
)

async def test_step2_configure_pid(reader, writer):
    print("[Step 2] CONFIGURE_PID_ASSIGNMENT (0x5704) — PID 0x010 -> Port 1")
    
    # Define mapping: PID 0x010 maps to physical Port 1
    payload = ConfigurePidAssignmentRequestPayload(
        operation=PidAssignmentOperation.ASSIGN,
        entries=[PidAssignmentEntry(pid=0x010, target_id=1, instance_id=0)]
    )
    
    req_bytes = payload.dump()
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_ASSIGNMENT,
        payload=req_bytes,
        tag=2
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    # Expected: SUCCESS
```

---

### Step 3: Query Initial Binding (`GET_PID_BINDING` — Opcode `0x5705`)
Query the binding of a virtual port (VCS 0, vPPB 0). Since we have not bound it yet, the response PID should be `0xFFF` (unbound).

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import (
    GetPidBindingCommand,
    GetPidBindingRequestPayload,
)

async def test_step3_query_initial_binding(reader, writer):
    print("[Step 3] GET_PID_BINDING (0x5705) - Pre-bind Check")
    
    payload = GetPidBindingRequestPayload(target_vcs=0, target_vppb=0)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING,
        payload=payload.dump(),
        tag=3
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        resp_payload = GetPidBindingCommand.parse_response_payload(resp_msg.get_payload())
        print(f"   Bound PID: {resp_payload.pid:#05x} (Expected: 0xfff / UNBOUND)")
```

---

### Step 4: Program Routing Entry (`SET_DRT` — Opcode `0x5709`)
Configure the Destination Routing Table (DRT). We map the programmed PID `0x010` index to point to physical Port 1.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import (
    SetDrtCommand,
    SetDrtRequestPayload,
)
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType

async def test_step4_set_drt(reader, writer):
    print("[Step 4] SET_DRT (0x5709) — DRT[0x010] = Port 1")
    
    # Program DRT table 0 at entry index 0x010
    payload = SetDrtRequestPayload(
        drt_index=0,
        start_entry=0x010,
        entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=1)]
    )
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.SET_DRT,
        payload=payload.dump(),
        tag=4
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    # Expected: SUCCESS
```

---

### Step 5: Read and Verify Routing Entry (`GET_DRT` — Opcode `0x5708`)
Read the entry back from the DRT to verify it was written correctly in Step 4.

#### Implementation & Verification
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import (
    GetDrtCommand,
    GetDrtRequestPayload,
)

async def test_step5_get_drt(reader, writer):
    print("[Step 5] GET_DRT (0x5708) — Query DRT[0x010]")
    
    # Read 1 entry starting at index 0x010 of DRT table 0
    payload = GetDrtRequestPayload(drt_index=0, num_entries=1, start_entry=0x010)
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.GET_DRT,
        payload=payload.dump(),
        tag=5
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    
    if rc == CCI_RETURN_CODE.SUCCESS:
        resp_payload = GetDrtCommand.parse_response_payload(resp_msg.get_payload())
        print(resp_payload.get_pretty_print())
        # Expected: [0x010] type=PHYSICAL_PORT target=1
```

---

### Step 6: Bind VCS Port to Endpoint (`CONFIGURE_PID_BINDING` — Opcode `0x5706`)
Bind Virtual Port Bridge (vPPB) 0 on VCS 0 to PID `0x010`.

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

async def test_step6_bind_vppb(reader, writer):
    print("[Step 6] CONFIGURE_PID_BINDING (0x5706) — Bind vPPB 0 -> PID 0x010")
    
    payload = ConfigurePidBindingRequestPayload(
        operation=PidBindingOperation.BIND,
        target_vcs=0,
        target_vppb=0,
        pid=0x010
    )
    
    resp_msg = await cci_round_trip(
        reader, writer,
        opcode=CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_BINDING,
        payload=payload.dump(),
        tag=6
    )
    
    rc = CCI_RETURN_CODE(resp_msg.cci_msg_header.return_code)
    print(f"   Return Code: {rc.name}")
    # Expected: BACKGROUND_COMMAND_STARTED (or SUCCESS if completed instantly)
    
    # Pause 50ms to allow background task execution to settle
    await asyncio.sleep(0.05)
```

---

### Step 7: Verify final Binding (`GET_PID_BINDING` — Opcode `0x5705`)
Query the binding of VCS 0, vPPB 0 again. It should now report that it is bound to PID `0x010`.

#### Implementation & Verification
```python
async def test_step7_verify_binding(reader, writer):
    print("[Step 7] GET_PID_BINDING (0x5705) - Post-bind Verification")
    
    payload = GetPidBindingRequestPayload(target_vcs=0, target_vppb=0)
    
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
        print(f"   Bound PID: {resp_payload.pid:#05x} (Expected: 0x010 / BOUND)")
        if resp_payload.pid == 0x010:
            print("   → SUCCESS: Switch Control Plane Commissioning Complete!")
        else:
            print("   → FAIL: Unexpected bound PID")
```

---

## 4. Standalone Combined Test Harness

Save the following code as `test_pbr_commissioning.py`. Running it will connect to a running Fabric Manager instance on port `8300` and execute the steps incrementally.

```python
"""
test_pbr_commissioning.py - Incremental CXL Switch Commissioning Script
"""
import asyncio
from opencis.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
from opencis.cxl.transport.packet_structs import SystemHeader
from opencis.cxl.transport.common import BasePacket

# Payload imports
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
    await test_step1_identify(reader, writer)
    await asyncio.sleep(0.01)

    await test_step2_configure_pid(reader, writer)
    await asyncio.sleep(0.01)

    await test_step3_query_initial_binding(reader, writer)
    await asyncio.sleep(0.01)

    await test_step4_set_drt(reader, writer)
    await asyncio.sleep(0.01)

    await test_step5_get_drt(reader, writer)
    await asyncio.sleep(0.01)

    await test_step6_bind_vppb(reader, writer)
    await asyncio.sleep(0.01)

    await test_step7_verify_binding(reader, writer)

    print("\nClosing connection.")
    writer.close()
    await writer.wait_closed()

# Paste step functions here...
# [Step 1 to Step 7 definitions go here]

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. Execution and Server logs

1. **Start the Fabric Manager Environment:**
   Run the Fabric Manager with a config defining your switch routing backend:
   ```bash
   python run_pbr_env.py --config-file configs/1vcs_1mld.yaml
   ```

2. **Execute the Commissioning client:**
   In a separate terminal tab, run your Python script:
   ```bash
   python test_pbr_commissioning.py
   ```

3. **Verify Output logs:**
   On the test client terminal, you should see:
   * SUCCESS for all steps.
   * `Bound PID: 0xfff` in Step 3.
   * `Bound PID: 0x010` in Step 7.
