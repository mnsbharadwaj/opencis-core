# GFD Commissioning — Commands, Arguments & Wire Format
### opencis-core · CXL 4.0 Rev 1.0 · June 2026
### Real byte layouts extracted from test_smbus_mctp_server_pbr_cmds.py

---

## Overview

This document contains the **exact byte-level wire format**, **real Python arguments**, and
**expected responses** for every GFD commissioning command.  Every value shown here
matches what the actual test suite sends and receives.

```
Constants used throughout:
  FM_I2C_ADDR   = 0x10   (Fabric Manager I2C slave address)
  DEV_I2C_ADDR  = 0x20   (Device / QEMU I2C slave address)
  FM_EID        = 0x08   (FM MCTP Endpoint ID)
  DEV_EID       = 0x09   (Device MCTP Endpoint ID)
  MCTP_MSG_TYPE = 0x7E   (CXL FM API message type)
  PID           = 0x010  (12-bit Port Identifier for this GFD)
  TARGET_PORT   = 1      (DSP physical port number GFD is connected to)
  DRT_INDEX     = 0      (which DRT table to use; 0 = first table)
  VCS_ID        = 0      (Virtual CXL Switch ID)
  VPPB_ID       = 0      (Virtual PPB ID within the VCS)
```

---

## Complete Commissioning Sequence

```
Step  Opcode   Command Name                 Direction   rc Expected
----  ------   -------------------------    ---------   -----------
 1    0x5700   Identify PBR Switch          FM -> SW    SUCCESS
 2    0x5704   Configure PID Assignment     FM -> SW    SUCCESS
 3    0x5709   Set DRT                      FM -> SW    SUCCESS
 4    0x5705   Get PID Binding (check unbound)  FM -> SW    SUCCESS, pid=0xFFF
 5    0x5706   Configure PID Binding (BIND) FM -> SW    BACKGROUND_COMMAND_STARTED
 6    0x5705   Get PID Binding (verify bound)   FM -> SW    SUCCESS, pid=0x010
```

---

## Command 1 — Identify PBR Switch (0x5700)

### Purpose
Learn the switch's capabilities before any commissioning.
The response tells you: how many DRT tables exist (`num_drts`), and which VCSs have a GAE.
**Must be called first.** If `num_drts == 0`, the switch cannot route PBR traffic.

### SMBus+MCTP Request Frame Layout

```
Offset  Bytes  Field               Value          Meaning
------  -----  ------------------  -------------  --------------------------
[B00]     1    dest_slave_addr     0x20           FM I2C addr (0x10 << 1), R/W=0 (write)
[B01]     1    command_code        0x0F           MCTP-over-SMBus fixed code (DSP0237)
[B02]     1    byte_count          0x12 (18)      bytes from B03 to last payload byte
[B03]     1    src_slave_addr      0x41           DEV I2C addr (0x20 << 1) | 0x01
-- MCTP Transport Header (DSP0236) --
[B04]     1    hdr_ver             0x01           MCTP version 1
[B05]     1    dest_eid            0x08           FM endpoint ID
[B06]     1    src_eid             0x09           Device endpoint ID
[B07]     1    flags               0xC8           SOM|EOM|TO, tag=0
-- MCTP Message Body --
[B08]     1    msg_type            0x7E           CXL FM API (IC=0)
-- CCI Message Header (12 bytes, B09..B20) --
[B09]     1    message_category    0x00           REQUEST (0)
[B10]     1    message_tag         0x01           echo'd back in response
[B11]     1    reserved            0x00
[B12]     2    command_opcode      0x00 0x57      0x5700 little-endian
[B14]     3    payload_length      0x00 0x00 0x00 0 bytes payload (no request payload)
[B17]     2    return_code         0x00 0x00      zero in request
[B19]     2    vendor_status       0x00 0x00
[B21]     1    PEC                 CRC-8          SMBus CRC over B00..B20
-- CCI Payload: NONE (0 bytes) --
```

**Python payload builder:**
```python
def build_identify_payload() -> bytes:
    return b""    # 0x5700 has NO request payload
```

**Full frame (22 bytes):**
```
Offset: 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15
Data:   20 0F 12 41 01 08 09 C8 7E 00 01 00 00 57 00 00 00 00 00 00 00 PEC

         ^dest  ^cmd ^cnt ^src  ^hdr_ver ^dei ^sei ^flags ^msg_type
                                                               ^cci_cat ^tag
                                                                          ^opcode (0x5700 LE)
```

### Expected Response (12-byte CCI payload)

```python
def make_identify_response(num_drts: int = 1) -> bytes:
    data = bytearray(12)
    data[0:8] = (1).to_bytes(8, "little")  # gae_support_map = 1 (VCS 0 has GAE)
    data[8]   = num_drts & 0xFF            # num_drts = 1
    # data[9]  = 0x00   num_rgts = 0
    # data[10] = 0x00   routing_caps = 0
    return bytes(data)
```

**Response payload byte map:**
```
Offset  Bytes  Field            Value           Meaning
------  -----  ---------------  --------------  ---------------------------
0x00      8    gae_support_map  0x01 00 00...   Bit 0=1: VCS 0 has a GAE
0x08      1    num_drts         0x01            1 DRT table exists
0x09      1    num_rgts         0x00            0 Routing Group Tables
0x0A      1    routing_caps     0x00            no special routing modes
0x0B      1    reserved         0x00
```

**API call (MctpCciApiClient):**
```python
rc, info = await api_client.identify_pbr_switch()
# Returns: (CCI_RETURN_CODE.SUCCESS, IdentifyPbrSwitchResponsePayload)
print(f"num_drts        = {info.num_drts}")          # 1
print(f"num_rgts        = {info.num_rgts}")          # 0
print(f"gae_support_map = {info.gae_support_map:#018x}")  # 0x0000000000000001
assert info.num_drts >= 1
```

---

## Command 2 — Configure PID Assignment (0x5704) — ASSIGN

### Purpose
Assign PID `0x010` to physical DSP port 1.  This records the mapping in the switch's
`_pid_assignments` dict but does NOT update the DRT — traffic cannot flow yet.

### CCI Request Payload Layout (9 bytes)

```python
def build_configure_pid_assignment_payload(
    pid: int = 0x010, target_id: int = 1,
    instance_id: int = 0, operation: int = 0,   # 0 = ASSIGN, 1 = CLEAR
) -> bytes:
    header = bytearray(4)
    header[0] = operation & 0x07           # bits[2:0] = 0b000 = ASSIGN
    # header[1] = 0x00  reserved
    struct.pack_into("<H", header, 2, 1)   # num_targets = 1 (little-endian)
    entry = bytearray(5)
    struct.pack_into("<H", entry, 0, pid & 0x0FFF)    # PID = 0x0010 (LE)
    struct.pack_into("<H", entry, 2, target_id)        # target_id = 0x0001 (LE)
    entry[4] = instance_id & 0xFF                      # instance_id = 0x00
    return bytes(header) + bytes(entry)
```

**Resulting 9-byte payload:**
```
Offset  Bytes  Field           Value  Meaning
------  -----  --------------- -----  -----------------------------------
0x00      1    operation       0x00   bits[2:0]=000 = ASSIGN
0x01      1    reserved        0x00
0x02      2    num_targets     01 00  1 entry follows (little-endian)
-- PID Assignment Entry (Table 7-124, 5 bytes) --
0x04      2    pid             10 00  0x0010 little-endian, bits[11:0]
0x06      2    target_id       01 00  0x0001 = DSP port 1 (little-endian)
0x08      1    instance_id     0x00   instance 0 (only one GFD on port 1)
```

**Hex dump:**
```
0000  00 00 01 00 10 00 01 00 00  |.........|
       ^op ^rs ^num_tgt ^pid  ^tgt_id  ^inst
```

### Expected Response
```
CCI payload: empty (0 bytes)
return_code: 0x0000 (SUCCESS)
background:  False
```

**API call:**
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentRequestPayload, PidAssignmentEntry, PidAssignmentOperation,
)
request = ConfigurePidAssignmentRequestPayload(
    operation=PidAssignmentOperation.ASSIGN,   # = 0
    entries=[
        PidAssignmentEntry(
            pid=0x010,        # 12-bit PID to assign
            target_id=1,      # DSP port number (physical port index)
            instance_id=0,    # 0 if only one GFD on this port
        )
    ],
)
rc, _ = await api_client.configure_pid_assignment(request)
assert rc == CCI_RETURN_CODE.SUCCESS
```

> **CRITICAL:** After this command, `PbrSwitchManager._pid_assignments[0x010]` exists,
> but `_drt_tables[0].entries[0x010]` is still `INVALID`. The router cannot route yet.
> You MUST call Set DRT (command 3) next.

---

## Command 3 — Set DRT (0x5709)

### Purpose
Write `DRT[0][0x010] = PHYSICAL_PORT → port 1` into the switch's routing table.
After this command, `PbrSwitchRouter` will forward TLPs with DPID=0x010 to port 1.
**This is what actually enables the data plane.**

### CCI Request Payload Layout (8 bytes)

```python
def build_set_drt_payload(
    drt_index: int = 0,         # which DRT table (0 = first)
    start_entry: int = 0x010,   # starting DPID (= PID assigned in step 2)
    entry_type: int = 1,        # 0b01 = PHYSICAL_PORT
    routing_target: int = 1,    # physical port number to route to
) -> bytes:
    header = bytearray(6)
    header[0x00] = drt_index & 0xFF         # DRT table index = 0
    # header[0x01] = 0x00  reserved
    struct.pack_into("<H", header, 0x02, 1)              # num_entries = 1
    struct.pack_into("<H", header, 0x04, start_entry)    # start_entry = 0x0010 (LE)
    # DRT Entry: 2 bytes (Table 7-133)
    drt_entry = bytes([entry_type & 0x03, routing_target & 0xFF])
    return bytes(header) + drt_entry
```

**Resulting 8-byte payload:**
```
Offset  Bytes  Field           Value  Meaning
------  -----  --------------- -----  -----------------------------------
0x00      1    drt_index       0x00   DRT table 0
0x01      1    reserved        0x00
0x02      2    num_entries     01 00  1 DRT entry follows (little-endian)
0x04      2    start_entry     10 00  0x0010 = DPID / array index (LE)
-- DRT Entry (Table 7-133, 2 bytes each) --
0x06      1    entry_type      0x01   bits[1:0]=01 = PHYSICAL_PORT
                                      bits[7:2]=00 = reserved (MUST be 0)
0x07      1    routing_target  0x01   physical port 1 (where GFD is)
```

**Hex dump:**
```
0000  00 00 01 00 10 00 01 01  |........|
       ^drt ^rs ^n_ent ^start  ^type ^target
                      0x0010
```

**DRT Entry Types:**
```
0b00 = 0x00  INVALID        -- drop this DPID (no routing)
0b01 = 0x01  PHYSICAL_PORT  -- route to routing_target port number
0b10 = 0x02  RGT_INDEX      -- multicast via Routing Group Table
0b11 = 0x03  RESERVED       -- ILLEGAL, switch returns INVALID_INPUT
```

### Expected Response
```
CCI payload: empty (0 bytes)
return_code: 0x0000 (SUCCESS)
background:  False
```

**API call:**
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import SetDrtRequestPayload
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType

request = SetDrtRequestPayload(
    drt_index=0,              # DRT table 0
    start_entry=0x010,        # DPID to program (same as PID from step 2)
    entries=[
        DrtEntry(
            entry_type=DrtEntryType.PHYSICAL_PORT,   # = 0b01 = 0x01
            routing_target=1,                         # DSP port 1
        )
    ],
)
rc, _ = await api_client.set_drt(request)
assert rc == CCI_RETURN_CODE.SUCCESS
# Data plane is now live: DPID=0x010 routes to port 1
```

**What the switch does internally:**
```python
# pbr_switch_manager.py  set_drt()
table = self._drt_tables[0]
table.entries[0x010] = DrtEntry(PHYSICAL_PORT, 1)
# entries[0x010] is now valid -- PbrSwitchRouter will forward here
```

---

## Command 4 — Get PID Binding (0x5705) — Verify Unbound

### Purpose
Read the current PID binding for vPPB (vcs=0, vppb=0).
**Expected result before binding: `pid = 0xFFF` (= PID_UNASSIGNED).**
If you get anything other than 0xFFF, the vPPB is already bound.

### CCI Request Payload Layout (2 bytes)

```python
def build_get_pid_binding_payload(vcs: int = 0, vppb: int = 0) -> bytes:
    return bytes([vcs & 0xFF, vppb & 0xFF])
```

**Resulting 2-byte payload:**
```
Offset  Bytes  Field           Value  Meaning
------  -----  --------------- -----  -----------------------------------
0x00      1    vcs_id          0x00   Virtual CXL Switch 0
0x01      1    vppb_id         0x00   Virtual PPB 0 within VCS 0
```

**Hex dump:**
```
0000  00 00  |..|
       ^vcs  ^vppb
```

### Expected Response — UNBOUND (24 bytes)

```python
def make_get_pid_binding_response(pid: int = 0xFFF) -> bytes:
    data = bytearray(0x18)                        # 24 bytes
    struct.pack_into("<H", data, 0, pid & 0x0FFF) # bound_pid at offset 0
    return bytes(data)
```

**Response payload byte map (24 bytes, 0x18):**
```
Offset  Bytes  Field             Value    Meaning
------  -----  ----------------  -------  -----------------------------
0x00      2    bound_pid         FF 0F    0x0FFF = PID_UNASSIGNED (UNBOUND)
0x02      1    reserved          0x00
0x03      1    reserved          0x00
0x04-0x17     HMAT info          0x00..  all zeros (unbound has no HMAT)
```

**After binding (command 5 + 6), the response becomes:**
```
Offset  Bytes  Field             Value    Meaning
------  -----  ----------------  -------  -----------------------------
0x00      2    bound_pid         10 00    0x0010 = PID assigned in step 2
```

**API call:**
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import GetPidBindingRequestPayload

# Before binding (step 4):
request = GetPidBindingRequestPayload(vcs_id=0, vppb_id=0)
rc, resp = await api_client.get_pid_binding(request)
assert rc == CCI_RETURN_CODE.SUCCESS
assert resp.bound_pid == 0xFFF,  f"Expected unbound (0xFFF), got {resp.bound_pid:#05x}"
print(f"  bound_pid = {resp.bound_pid:#05x}  (UNBOUND -- 0xFFF confirmed)")

# After binding (step 6):
rc, resp = await api_client.get_pid_binding(request)
assert resp.bound_pid == 0x010, f"Expected bound (0x010), got {resp.bound_pid:#05x}"
print(f"  bound_pid = {resp.bound_pid:#05x}  (BOUND to PID 0x010)")
```

---

## Command 5 — Configure PID Binding (0x5706) — BIND

### Purpose
Bind vPPB (vcs=0, vppb=0) to PID `0x010`.
This is a **background command** — the switch returns `BACKGROUND_COMMAND_STARTED (rc=0x0001)`
immediately.  The actual binding happens in an asyncio background task.

### CCI Request Payload Layout (28 bytes = 0x1C)

```python
def build_configure_pid_binding_payload(
    pid: int = 0x010,
    vcs: int = 0, vppb: int = 0,
    operation: int = 0,    # 0 = BIND, 1 = UNBIND
) -> bytes:
    data = bytearray(0x1C)          # 28 bytes total
    data[0x00] = operation & 0x07   # bits[2:0]: 000=BIND, 001=UNBIND
    data[0x01] = vcs & 0xFF         # vcs_id
    data[0x02] = vppb & 0xFF        # vppb_id
    # data[0x03] = 0x00  reserved
    struct.pack_into("<H", data, 0x04, pid & 0x0FFF)  # PID (little-endian)
    # 0x06..0x1B = HMAT fields (latency/BW) -- all zeros = no constraints
    return bytes(data)
```

**Resulting 28-byte payload:**
```
Offset  Bytes  Field            Value   Meaning
------  -----  ---------------  ------  -----------------------------------
0x00      1    operation        0x00    bits[2:0]=000 = BIND (001=UNBIND)
0x01      1    vcs_id           0x00    VCS 0
0x02      1    vppb_id          0x00    vPPB 0 within VCS 0
0x03      1    reserved         0x00
0x04      2    pid              10 00   0x0010 little-endian
0x06      8    latency_base_unit 0x00.. (HMAT: 0 = no latency constraint)
0x0E      2    latency_entry    00 00   (HMAT: 0 = use default)
0x10      8    bw_base_unit     0x00..  (HMAT: 0 = no BW constraint)
0x18      2    bw_entry         00 00   (HMAT: 0 = use default)
0x1A      2    reserved         00 00
```

**Hex dump (28 bytes):**
```
0000  00 00 00 00 10 00 00 00  |........|
0008  00 00 00 00 00 00 00 00  |........|
0010  00 00 00 00 00 00 00 00  |........|
0018  00 00 00 00              |....|
       ^op ^vc ^vp ^rs ^pid
```

### Expected Response — BACKGROUND COMMAND STARTED
```
CCI payload: empty (0 bytes)
return_code: 0x0001  (BACKGROUND_COMMAND_STARTED)
background:  True    (bo_flag bit set in CCI header)
```

**API call:**
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingRequestPayload, PidBindingOperation,
)
from opencis.cxl.component.pbr_switch_manager import HmatInfo

request = ConfigurePidBindingRequestPayload(
    operation=PidBindingOperation.BIND,   # = 0
    vcs_id=0,
    vppb_id=0,
    pid=0x010,
    hmat=HmatInfo(            # all zeros = no HMAT constraints
        latency_entry_base_unit=0,
        latency_entry=0,
        bw_entry_base_unit=0,
        bw_entry=0,
    ),
)
rc, _ = await api_client.configure_pid_binding(request, wait_for_completion=False)
assert rc == CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED
# rc == 0x0001: background task started, not complete yet
# DO NOT call wait_for_completion=True unless you want to block until done
```

**What the switch does:**
```python
# configure_pid_binding.py  -- CciBackgroundCommand
# Immediately:
#   response.return_code = BACKGROUND_COMMAND_STARTED
#   response.bo_flag     = True

# Background asyncio task:
rc = pbr_switch_manager.configure_pid_binding(BIND, vcs_id=0, vppb_id=0, pid=0x010, hmat)
# pbr_switch_manager.py:
#   self._pid_bindings[(0, 0)] = PidBinding(pid=0x010, hmat=hmat)
```

---

## Command 6 — Get PID Binding (0x5705) — Verify Bound

### Purpose
Confirm the background binding (command 5) completed successfully.
**Expected result: `pid = 0x010`** (matching the PID assigned in command 2).

### Request Payload (same as command 4)

```python
payload = build_get_pid_binding_payload(vcs=0, vppb=0)
# 0000  00 00  |..|
```

### Expected Response — BOUND (24 bytes)

```
Offset  Bytes  Field             Value    Meaning
------  -----  ----------------  -------  ---------------------------
0x00      2    bound_pid         10 00    0x0010 = BOUND to PID 0x010
0x02..0x17     HMAT fields       00..     zeros (no constraints set)
```

**API call:**
```python
rc, resp = await api_client.get_pid_binding(GetPidBindingRequestPayload(vcs_id=0, vppb_id=0))
assert rc == CCI_RETURN_CODE.SUCCESS
assert resp.bound_pid == 0x010
print(f"  bound_pid = {resp.bound_pid:#05x}  -->  BOUND to PID 0x010")
# Commissioning COMPLETE
```

---

## Command 7 — Get DRT (0x5708) — Optional Verification

### Purpose
Read back the DRT entry just written to verify Set DRT (command 3) succeeded.

### CCI Request Payload Layout (6 bytes)

```python
def build_get_drt_payload(
    drt_index: int = 0,       # DRT table 0
    start_entry: int = 0x010, # DPID to read (= PID from step 2)
    num_entries: int = 1,     # how many entries to read
) -> bytes:
    data = bytearray(6)
    data[0x00] = drt_index & 0xFF
    # data[0x01] = 0x00  reserved
    struct.pack_into("<H", data, 0x02, num_entries)  # num_entries = 1 (LE)
    struct.pack_into("<H", data, 0x04, start_entry)  # start_entry = 0x0010 (LE)
    return bytes(data)
```

**Resulting 6-byte payload:**
```
Offset  Bytes  Field          Value   Meaning
------  -----  -------------- ------  ----------------------------------
0x00      1    drt_index      0x00    DRT table 0
0x01      1    reserved       0x00
0x02      2    num_entries    01 00   read 1 entry
0x04      2    start_entry    10 00   starting at DPID 0x0010
```

**Hex dump:**
```
0000  00 00 01 00 10 00  |......|
       ^drt ^rs ^n_ent   ^start_dpid
```

### Expected Response (10 bytes)

```python
def make_get_drt_response() -> bytes:
    header = bytearray(8)
    struct.pack_into("<H", header, 0x02, 1)          # num_entries = 1
    struct.pack_into("<H", header, 0x04, 0x010)      # start_entry = DPID 0x010
    entry = bytes([0x01, 1])                          # PHYSICAL_PORT -> port 1
    return bytes(header) + entry
```

**Response payload byte map (10 bytes):**
```
Offset  Bytes  Field            Value   Meaning
------  -----  ---------------  ------  ----------------------------------
0x00      1    rgt_associated   0x00    no RGT association
0x01      1    reserved         0x00
0x02      2    num_entries      01 00   1 entry returned
0x04      2    start_entry      10 00   DPID 0x0010
0x06      1    reserved         0x00
0x07      1    reserved         0x00
-- DRT Entry (2 bytes) --
0x08      1    entry_type       0x01    PHYSICAL_PORT (bits[1:0]=01)
0x09      1    routing_target   0x01    physical port 1
```

**API call:**
```python
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import GetDrtRequestPayload

request = GetDrtRequestPayload(drt_index=0, start_entry=0x010, num_entries=1)
rc, resp = await api_client.get_drt(request)
assert rc == CCI_RETURN_CODE.SUCCESS
entry = resp.entries[0]
assert entry.entry_type == DrtEntryType.PHYSICAL_PORT   # == 1
assert entry.routing_target == 1
print(f"  DRT[0][0x010] = {entry.entry_type.name} -> port {entry.routing_target}")
```

---

## SMBus+MCTP Frame Structure (DSP0237)

Every command above is wrapped in this frame when sent over the SMBus path (QEMU):

```
+--------+----------+-----------+--------+-------------------+-----------+
| B00    | B01      | B02       | B03    | B04..B07          | B08       |
| dest   | command  | byte_count| src    | MCTP Transport    | msg_type  |
| addr   | code     |           | addr   | Header            |           |
| (FM<<1)| (0x0F)   | (18+plen) |(DEV<<1)|ver|dei|sei|flags | (0x7E)    |
|        |          |           |  |0x01 |                   |           |
+--------+----------+-----------+--------+-------------------+-----------+
| B09..B20                                                               |
| CCI Message Header (12 bytes)                                          |
| cat|tag|rsv|opcode(2)|payload_len(3)|background|return_code(2)|vsesR(2)|
+------------------------------------------------------------------------+
| B21..B(21+plen-1)                                                      |
| CCI Payload (0..N bytes, command-specific)                             |
+------------------------------------------------------------------------+
| BPEC                                                                   |
| PEC (CRC-8 over all previous bytes)                                    |
+------------------------------------------------------------------------+

dest_addr  = (FM_I2C_ADDR << 1) & 0xFE  = (0x10 << 1) = 0x20
src_addr   = (DEV_I2C_ADDR << 1) | 0x01 = (0x20 << 1) | 1 = 0x41
byte_count = 18 + len(cci_payload)        (B03 through last payload byte)
flags      = 0xC0 | 0x08 | tag           (SOM=1, EOM=1, TO=1, seq=0)
msg_type   = 0x7E                         (CXL FM API)
```

**Python frame builder:**
```python
def build_request_frame(opcode: int, cci_payload: bytes = b"",
                         cci_tag: int = 0, msg_tag: int = 0) -> bytes:
    plen = len(cci_payload)
    cci_hdr = bytearray(12)
    cci_hdr[0] = 0x00                      # REQUEST category
    cci_hdr[1] = cci_tag & 0xFF            # message_tag (echoed back)
    cci_hdr[3] = opcode & 0xFF             # opcode low byte
    cci_hdr[4] = (opcode >> 8) & 0xFF      # opcode high byte
    cci_hdr[5] = plen & 0xFF               # payload_length low
    cci_hdr[6] = (plen >> 8) & 0xFF
    cci_hdr[7] = (plen >> 16) & 0x1F
    cci_msg = bytes(cci_hdr) + cci_payload

    flags = 0xC0 | 0x08 | (msg_tag & 0x7)  # SOM|EOM|TO
    body = bytes([
        (DEV_I2C_ADDR << 1) | 0x01,       # src_slave_addr
        MCTP_HDR_VER, FM_EID, DEV_EID,    # MCTP header
        flags, MCTP_MSG_TYPE,              # flags + CXL FM API type
    ]) + cci_msg

    dest_addr  = (FM_I2C_ADDR << 1) & 0xFE
    byte_count = len(body)
    frame      = bytes([dest_addr, SMBUS_MCTP_COMMAND_CODE, byte_count]) + body
    return frame + bytes([_crc8(frame)])   # append PEC
```

---

## Full Commissioning Python Script (Copy-Paste Ready)

```python
"""
gfd_commissioning.py
Complete GFD commissioning sequence using MctpCciApiClient.
Replace 'api_client' with your actual connected MctpCciApiClient instance.
"""

import asyncio
import struct
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentRequestPayload, PidAssignmentEntry, PidAssignmentOperation,
)
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import SetDrtRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import GetDrtRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import GetPidBindingRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingRequestPayload, PidBindingOperation,
)
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType, HmatInfo

# ---- CHANGE THESE for your setup ----
GFD_PID     = 0x010   # 12-bit PID to assign
GFD_PORT    = 1       # DSP physical port the GFD is on
DRT_INDEX   = 0       # DRT table index (0 = first)
VCS_ID      = 0       # Virtual CXL Switch ID
VPPB_ID     = 0       # Virtual PPB ID
# --------------------------------------


async def commission_gfd(api_client):
    """Run all 6 commissioning commands in sequence. Raises AssertionError on failure."""

    sep = "=" * 60

    # ------------------------------------------------------------------
    # STEP 1: Identify PBR Switch (0x5700)
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 1  IdentifyPbrSwitch  (opcode 0x5700)")
    print(f"  Request payload: (none)")
    print(sep)

    rc, info = await api_client.identify_pbr_switch()
    assert rc == CCI_RETURN_CODE.SUCCESS,  f"Step 1 failed: {rc}"
    assert info.num_drts >= 1,             f"num_drts={info.num_drts} -- must be >= 1"
    print(f"  rc              = {rc.name}")
    print(f"  num_drts        = {info.num_drts}")
    print(f"  num_rgts        = {info.num_rgts}")
    print(f"  gae_support_map = {info.gae_support_map:#018x}")

    # ------------------------------------------------------------------
    # STEP 2: Configure PID Assignment (0x5704) -- ASSIGN
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 2  ConfigurePidAssignment  (opcode 0x5704)")
    print(f"  Request payload: operation=ASSIGN(0x00), num_targets=1")
    print(f"    entry[0]: pid=0x{GFD_PID:03X}  target_id={GFD_PORT}  instance_id=0")
    print(sep)

    assign_req = ConfigurePidAssignmentRequestPayload(
        operation=PidAssignmentOperation.ASSIGN,
        entries=[PidAssignmentEntry(pid=GFD_PID, target_id=GFD_PORT, instance_id=0)],
    )
    rc, _ = await api_client.configure_pid_assignment(assign_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"Step 2 failed: {rc}"
    print(f"  rc = {rc.name}")
    print(f"  Response payload: (empty -- success)")
    print(f"  PID 0x{GFD_PID:03X} recorded in switch PID assignment map")
    print(f"  NOTE: DRT NOT updated yet -- data plane still dark")

    # ------------------------------------------------------------------
    # STEP 3: Set DRT (0x5709)
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 3  SetDrt  (opcode 0x5709)")
    print(f"  Request payload: drt_index={DRT_INDEX}  start_entry=0x{GFD_PID:03X}")
    print(f"    entry[0]: entry_type=PHYSICAL_PORT(0x01)  routing_target={GFD_PORT}")
    print(sep)

    set_drt_req = SetDrtRequestPayload(
        drt_index=DRT_INDEX,
        start_entry=GFD_PID,
        entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=GFD_PORT)],
    )
    rc, _ = await api_client.set_drt(set_drt_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"Step 3 failed: {rc}"
    print(f"  rc = {rc.name}")
    print(f"  Response payload: (empty -- success)")
    print(f"  DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = PHYSICAL_PORT -> port {GFD_PORT}")
    print(f"  DATA PLANE IS NOW LIVE for DPID=0x{GFD_PID:03X}")

    # ------------------------------------------------------------------
    # STEP 4: Get PID Binding (0x5705) -- Verify Unbound
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 4  GetPidBinding  (opcode 0x5705) -- expect UNBOUND (0xFFF)")
    print(f"  Request payload: vcs_id={VCS_ID}  vppb_id={VPPB_ID}")
    print(sep)

    bind_req = GetPidBindingRequestPayload(vcs_id=VCS_ID, vppb_id=VPPB_ID)
    rc, resp = await api_client.get_pid_binding(bind_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"Step 4 failed: {rc}"
    assert resp.bound_pid == 0xFFF, f"Step 4: expected 0xFFF got {resp.bound_pid:#05x}"
    print(f"  rc        = {rc.name}")
    print(f"  bound_pid = 0x{resp.bound_pid:03X}  (0xFFF = UNBOUND -- confirmed)")

    # ------------------------------------------------------------------
    # STEP 5: Configure PID Binding (0x5706) -- BIND  [background]
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 5  ConfigurePidBinding  (opcode 0x5706) -- BIND  [background command]")
    print(f"  Request payload: operation=BIND(0x00)  vcs_id={VCS_ID}  vppb_id={VPPB_ID}")
    print(f"    pid=0x{GFD_PID:03X}  hmat=zeros (no latency/BW constraint)")
    print(sep)

    cfg_bind_req = ConfigurePidBindingRequestPayload(
        operation=PidBindingOperation.BIND,
        vcs_id=VCS_ID,
        vppb_id=VPPB_ID,
        pid=GFD_PID,
        hmat=HmatInfo(latency_entry_base_unit=0, latency_entry=0,
                      bw_entry_base_unit=0, bw_entry=0),
    )
    rc, _ = await api_client.configure_pid_binding(cfg_bind_req, wait_for_completion=False)
    assert rc == CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED, f"Step 5 unexpected rc: {rc}"
    print(f"  rc = {rc.name}  (0x0001 -- background task started)")
    print(f"  bo_flag = True")
    print(f"  Response payload: (empty)")
    print(f"  Background task: binding vPPB({VCS_ID},{VPPB_ID}) to PID 0x{GFD_PID:03X}")

    # Give background task a moment (in real hardware, poll background_operation_status)
    await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # STEP 6: Get PID Binding (0x5705) -- Verify Bound
    # ------------------------------------------------------------------
    print(f"\n{sep}")
    print(f"  STEP 6  GetPidBinding  (opcode 0x5705) -- expect BOUND (0x{GFD_PID:03X})")
    print(f"  Request payload: vcs_id={VCS_ID}  vppb_id={VPPB_ID}")
    print(sep)

    rc, resp = await api_client.get_pid_binding(bind_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"Step 6 failed: {rc}"
    assert resp.bound_pid == GFD_PID, f"Step 6: expected {GFD_PID:#05x} got {resp.bound_pid:#05x}"
    print(f"  rc        = {rc.name}")
    print(f"  bound_pid = 0x{resp.bound_pid:03X}  (BOUND to PID 0x{GFD_PID:03X} -- confirmed)")

    print(f"\n{'*' * 60}")
    print(f"  GFD COMMISSIONING COMPLETE")
    print(f"  PID 0x{GFD_PID:03X} -> DSP port {GFD_PORT} -> GFD device")
    print(f"  TLPs with DPID=0x{GFD_PID:03X} will route to port {GFD_PORT}")
    print(f"  vPPB({VCS_ID},{VPPB_ID}) is bound to PID 0x{GFD_PID:03X}")
    print(f"{'*' * 60}\n")
```

---

## Running the Tests

```bash
# Run all 6 PBR commissioning tests (individual test per command)
python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py -v

# Run the full sequence test (all 6 in one test)
python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py::test_smbus_mctp_server_all_pbr_commands -v

# Run standalone (no pytest, direct output)
python tests/test_smbus_mctp_server_pbr_cmds.py

# Expected output (all 7 tests):
# test_smbus_mctp_server_all_pbr_commands    PASSED
# test_smbus_mctp_identify                   PASSED
# test_smbus_mctp_configure_pid_assignment   PASSED
# test_smbus_mctp_get_pid_binding_unbound    PASSED
# test_smbus_mctp_configure_pid_binding_background PASSED
# test_smbus_mctp_get_drt                    PASSED
# test_smbus_mctp_set_drt                    PASSED
# 7 passed in 0.28s
```

---

## Return Code Reference

| Code  | Hex    | Name                       | When                                      |
|-------|--------|----------------------------|-------------------------------------------|
| 0     | 0x0000 | `SUCCESS`                  | Command completed OK, check response payload |
| 1     | 0x0001 | `BACKGROUND_COMMAND_STARTED` | Background command accepted (5706h only) |
| 2     | 0x0002 | `INVALID_INPUT`            | Bad opcode args (out-of-range PID, RESERVED DRT type, etc.) |
| 3     | 0x0003 | `UNSUPPORTED`              | Opcode not registered on this switch      |
| 4     | 0x0004 | `INTERNAL_ERROR`           | Switch internal fault                     |

---

## Quick Reference Card

```
Command                  Opcode   Payload  Response    bg?   Critical Rule
-----------------------  ------   -------  ----------  ----  -------------------------
Identify PBR Switch      0x5700   0 bytes  12 bytes    No    num_drts must be >= 1
Configure PID Assignment 0x5704   9 bytes  0 bytes     No    Call BEFORE Set DRT
Set DRT                  0x5709   8 bytes  0 bytes     No    entry_type MUST not be RESERVED
Get PID Binding          0x5705   2 bytes  24 bytes    No    0xFFF = unbound sentinel
Configure PID Binding    0x5706   28 bytes 0 bytes     YES   rc=0x0001 is correct/expected
Get DRT (verify)         0x5708   6 bytes  10+ bytes   No    optional verification step
```

---

*Document version: 1.0 — June 2026*
*Branch: `smbus_dual_port`*
*Source test: [test_smbus_mctp_server_pbr_cmds.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/tests/test_smbus_mctp_server_pbr_cmds.py)*
*Spec: CXL 4.0 Rev 1.0, Section 7.7.13*
