# CXL 4.0 Port-Based Routing (PBR) — Design & Implementation Guide

> **opencis-core · v0.5-dev · May 2026**

---

## 1. What is PBR?

Port-Based Routing (PBR) is a CXL 4.0 fabric routing mechanism defined in §7.7.13 of the CXL Specification Rev 4.0 v1.0.

Unlike the virtual-switch model (HBR — Host-Based Routing), where a host OS drives PCIe enumeration, PBR allows the **Fabric Manager (FM)** to assign numeric Port IDs (PIDs) to physical ports and program a **DPID Routing Table (DRT)** that the switch hardware uses to route TLPs by their destination PID.

### Key Concepts

| Term | Meaning |
|------|---------|
| **PID** | Port ID — a 12-bit identifier assigned by the FM to a physical port |
| **SPID** | Source PID — the PID of the ingress port |
| **DPID** | Destination PID — the PID the packet is routed *to* |
| **DRT** | DPID Routing Table — maps DPID → egress physical port or RGT index |
| **PBR TLP Header (PTH)** | 6-byte header prepended to a TLP: `[SystemHeader 2B][PbrHeader 4B]` |
| **HBR** | Host-Based Routing — standard PCIe TLP without PBR header |
| **Ingress Edge Port** | First PBR switch port that receives HBR traffic; encapsulates to PBR |
| **Egress Edge Port** | Last PBR switch port before a non-PBR host; strips PBR header |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    QEMU Host / FM CLI                           │
│                    (Socket.IO client)                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Socket.IO  pbr:setDrt / pbr:identify …
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│           FabricManagerSocketIoServer                           │
│    socketio_server.py  ·  Port 8200                             │
│                                                                 │
│  _pbr_identify()        _pbr_configure_pid()                    │
│  _pbr_get_pid_binding() _pbr_configure_pid_binding()            │
│  _pbr_get_drt()         _pbr_set_drt()                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ calls MctpCciApiClient methods
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│            MctpCciApiClient                                     │
│    mctp_cci_api_client.py                                       │
│                                                                 │
│  identify_pbr_switch()     configure_pid_assignment()           │
│  get_pid_binding()         configure_pid_binding()              │
│  get_drt()                 set_drt()                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ CCI packets over TCP (port 8100)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│           MctpCciExecutor  (inside CxlSwitch)                   │
│    mctp_cci_executor.py                                         │
│                                                                 │
│  Dispatches by opcode to registered CciCommand handlers         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼──────────────────┐
          │                │                  │
          ▼                ▼                  ▼
  IdentifyPbrSwitch  SetDrtCommand    ConfigurePidAssignment
  Command            (5709h)          Command (5704h)
  (5700h)                             … etc.
          │                │
          └────────────────┤
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              PbrSwitchManager                                   │
│    pbr_switch_manager.py                                        │
│                                                                 │
│  PID table:  pid → (target_id, instance_id)                     │
│  DRT:        drt[drt_index][dpid] → DrtEntry                    │
│  Bindings:   pid → bound_pid / hmat_entry_index                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Plane — Packet Flow

### 3.1 HBR Ingress → PBR Encapsulation

```
CXL Device / QEMU                   PbrSwitchRouter
─────────────────                   ───────────────
HBR TLP (CxlIoMemRdPacket)
  system_header.payload_type = CXL_IO (1)
  cxl_io_header.addr = 0x1500
         │
         │  put to port_fifos[0].target_to_host
         ▼
    _route_packet(ingress_port=0, packet)
         │
         │  is_pbr() == False  →  HBR path
         │
         │  PbrHdmDecoderManager.lookup_dpid(addr=0x1500)
         │       returns DPID = 0x123
         │
         │  PbrBasePacket.encapsulate(spid=0, dpid=0x123,
         │                            inner_packet=hbr_pkt)
         │       → PBR TLP:
         │         [SystemHeader: payload_type=5]
         │         [PbrHeader:   spid=0, dpid=0x123]
         │         [HBR payload bytes...]
         │
         │  recurse: _route_packet(0, pbr_packet)
         │
         │  is_pbr() == True  →  PBR path
         │  pbr_manager.get_drt(0, dpid=0x123)
         │       returns DrtEntry(PHYSICAL_PORT, target=2)
         │
         └──► port_fifos[2].host_to_target.put(inner_packet)
                   (inner_packet = original hbr_pkt, zero-copy)
```

### 3.2 PBR Decapsulation at Egress

```
Upstream PBR Switch                 PbrSwitchRouter
───────────────────                 ───────────────
PBR TLP
  system_header.payload_type = PBR (5)
  pbr_header.dpid = 0x123
         │
         │  put to port_fifos[N].target_to_host
         ▼
    _route_packet(ingress_port=N, pbr_packet)
         │
         │  is_pbr() == True
         │  pbr_manager.get_drt(0, dpid=0x123)
         │       returns DrtEntry(PHYSICAL_PORT, target=2)
         │
         │  inner = pbr_packet._inner_packet   (stash from encapsulate)
         │     or   BasePacket(pbr_packet_payload_bytes)  (wire path)
         │
         └──► port_fifos[2].host_to_target.put(inner_packet)
```

---

## 4. Transport Layer Changes

### 4.1 New Fields (`fields.py`)

```python
PbrHeader = [
    ("spid",     0,  12),   # bits 0–11   Source PID
    ("dpid",     12, 12),   # bits 12–23  Destination PID
    ("reserved", 24, 8),    # bits 24–31  Reserved
]
```

### 4.2 New Packet (`packets.py` + `packet_constants.py`)

```
SYSTEM_PAYLOAD_TYPE.PBR = 5

PbrBasePacket wire layout (6 bytes header):
  ┌──────────────────────┬────────────────────────────┐
  │ SystemHeader  [2 B]  │ PbrHeader  [4 B]           │
  │ payload_type = 5     │ spid[11:0] | dpid[11:0]    │
  └──────────────────────┴────────────────────────────┘
  [inner TLP bytes ...]
```

### 4.3 Pure-Python Fallback (`packet_structs.py`)

Because MSVC is not available in this environment, the Cython `packet_structs.pyx` extension cannot be compiled. The generator script `generate_py_fallback.py` produces `packet_structs.py` — a complete pure-Python drop-in.

**Critical fix**: field bit-offsets must account for the struct's byte offset in the shared buffer:
```python
# Wrong (previous):
return _read_bits(self._parent_buf, {start}, {width})

# Correct (fixed):
return _read_bits(self._parent_buf, self._p * 8 + {start}, {width})
```

When the Cython `.pyd` extension is compiled, Python will automatically prefer it over the `.py` fallback.

---

## 5. PBR HDM Decoder (`hdm_decoder.py`)

Resolves a Host Physical Address (HPA) to a DPID at ingress:

```python
@dataclass
class PbrHdmDecoder:
    index: int
    base: int        # HPA base
    size: int        # region size in bytes
    ig: int          # interleave granularity
    iw: int          # interleave ways
    target_dpids: List[int]

    def get_dpid(self, hpa: int) -> Optional[int]:
        if self.base <= hpa < self.base + self.size:
            return self.target_dpids[0]
        return None

class PbrHdmDecoderManager:
    def lookup_dpid(self, hpa: int) -> Optional[int]: ...
    def commit(self, index, base, size, ig, iw, target_dpids): ...
```

---

## 6. PBR Switch Manager (`pbr_switch_manager.py`)

Central control-plane state store. **Stateless across restarts** (FM re-programs on reconnect).

```python
class PbrSwitchManager:
    # PID → (target_id, instance_id)
    _pid_table: Dict[int, Tuple[int, int]]

    # DRT: drt_index → {dpid: DrtEntry}
    _drt: Dict[int, Dict[int, DrtEntry]]

    # Bindings: pid → (bound_pid, hmat_entry_index)
    _bindings: Dict[int, Tuple[int, int]]

    def assign_pid(pid, target_id, instance_id) -> CCI_RETURN_CODE
    def clear_pid(pid, target_id, instance_id) -> CCI_RETURN_CODE
    def set_drt(drt_index, start_entry, entries) -> CCI_RETURN_CODE
    def get_drt(drt_index, start_entry, num_entries) -> Optional[Tuple[List, int]]
    def bind_pid(pid, target_pid, hmat_entry_index) -> CCI_RETURN_CODE
    def get_binding(pid) -> Optional[Tuple[int, int]]
    def get_identify_info() -> SwitchIdentifyInfo
```

---

## 7. CCI Commands — Opcodes & Payloads

| Command | Opcode | Request | Response |
|---------|--------|---------|----------|
| Identify PBR Switch | `0x5700` | None | `{gae_support_map, num_drts, num_rgts, routing_caps}` |
| Configure PID Assignment | `0x5704` | `{operation, entries[{pid, target_id, instance_id}]}` | None |
| Get PID Binding | `0x5705` | `{pid}` | `{pid, bound_pid, hmat_entry_index}` |
| Configure PID Binding | `0x5706` | `{pid, target_pid, hmat_entry_index}` | None |
| Get DRT | `0x5708` | `{drt_index, start_entry, num_entries}` | `{drt_index, start_entry, assoc_rgt, entries[{entry_type, routing_target}]}` |
| Set DRT | `0x5709` | `{drt_index, start_entry, entries[{entry_type, routing_target}]}` | None |

> **FM Workflow**:
> 1. `Identify PBR Switch (5700h)` — discover capabilities
> 2. `Configure PID Assignment (5704h)` — assign PIDs to physical ports
> 3. `Set DRT (5709h)` — program DPID → egress port routing
> 4. *(Optional)* `Configure PID Binding (5706h)` — bind PIDs for host-visible topology
> 5. Traffic flows automatically through `PbrSwitchRouter`

---

## 8. FM Integration — Socket.IO API

The `FabricManagerSocketIoServer` exposes these new events (same JSON over Socket.IO as all other FM commands):

### `pbr:identify`
```json
// Request: (no payload)
// Response:
{ "error": "", "result": { "gaeSupportMap": 0, "numDrts": 1, "numRgts": 0, "routingCaps": 0 } }
```

### `pbr:configurePid`
```json
// Request:
{ "operation": 0, "entries": [{ "pid": 291, "targetId": 2, "instanceId": 0 }] }
// Response:
{ "error": "", "result": "SUCCESS" }
```

### `pbr:getPidBinding`
```json
// Request:
{ "pid": 291 }
// Response:
{ "error": "", "result": { "pid": 291, "boundPid": 4095, "hmatEntryIndex": 0 } }
```

### `pbr:configurePidBinding`
```json
// Request:
{ "pid": 291, "targetPid": 1110, "hmatEntryIndex": 0 }
// Response:
{ "error": "", "result": "SUCCESS" }
```

### `pbr:getDrt`
```json
// Request:
{ "drtIndex": 0, "startEntry": 291, "numEntries": 1 }
// Response:
{ "error": "", "result": { "drtIndex": 0, "startEntry": 291, "associatedRgtIndex": 0,
  "entries": [{ "entryType": "PHYSICAL_PORT", "routingTarget": 2 }] } }
```

### `pbr:setDrt`
```json
// Request:
{ "drtIndex": 0, "startEntry": 291,
  "entries": [{ "entryType": "PHYSICAL_PORT", "routingTarget": 2 }] }
// Response:
{ "error": "", "result": "SUCCESS" }
```

---

## 9. Modified Files Summary

| File | Status | Purpose |
|------|--------|---------|
| `opencis/cxl/transport/packet_constants.py` | Modified | `SYSTEM_PAYLOAD_TYPE.PBR = 5` |
| `opencis/cxl/transport/fields.py` | Modified | Added `PbrHeader` field layout |
| `opencis/cxl/transport/packets.py` | Modified | Added `PbrBasePacket` composite |
| `opencis/cxl/transport/mixin.py` | Modified | Added `is_pbr()` to `BasePacketMixin` |
| `opencis/cxl/transport/generate_py_fallback.py` | **New** | Generates pure-Python `packet_structs.py` |
| `opencis/cxl/transport/pbr_packets.py` | **New** | `PbrBasePacket` with `create()` / `encapsulate()` |
| `opencis/cxl/transport/packet_structs.py` | Generated | Pure-Python drop-in for Cython extension |
| `opencis/cxl/component/hdm_decoder.py` | Modified | Added `PbrHdmDecoder` + `PbrHdmDecoderManager` |
| `opencis/cxl/component/pbr_switch_manager.py` | **New** | PID table, DRT, bindings — full control-plane state |
| `opencis/cxl/component/pbr_switch_router.py` | **New** | Async data-plane engine (encap/decap/route) |
| `opencis/cxl/cci/common.py` | Modified | Added 6 PBR opcode entries to `CCI_FM_API_COMMAND_OPCODE` |
| `opencis/cxl/cci/fabric_manager/pbr_switch/__init__.py` | **New** | Package exports |
| `opencis/cxl/cci/fabric_manager/pbr_switch/identify_pbr_switch.py` | **New** | Opcode 0x5700 |
| `opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_assignment.py` | **New** | Opcode 0x5704 |
| `opencis/cxl/cci/fabric_manager/pbr_switch/get_pid_binding.py` | **New** | Opcode 0x5705 |
| `opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_binding.py` | **New** | Opcode 0x5706 |
| `opencis/cxl/cci/fabric_manager/pbr_switch/get_drt.py` | **New** | Opcode 0x5708 |
| `opencis/cxl/cci/fabric_manager/pbr_switch/set_drt.py` | **New** | Opcode 0x5709 |
| `opencis/cxl/component/mctp/mctp_cci_api_client.py` | Modified | 6 new PBR async client methods |
| `opencis/cxl/component/fabric_manager/socketio_server.py` | Modified | 6 new `pbr:*` Socket.IO handlers |
| `opencis/apps/cxl_switch.py` | Modified | `enable_pbr` flag + PBR command registration |
| `tests/test_pbr_data_plane.py` | **New** | Integration tests (2 tests) |
| `tests/test_pbr_switch_command_set.py` | **New** | CCI command unit tests (45 tests) |
| `pbr_cli_test.py` | **New** | Standalone FM command smoke-test |

---

## 10. Testing

### 10.1 Unit + Integration Tests (no running switch needed)

```bash
# All 47 PBR tests
py -m pytest tests/test_pbr_data_plane.py tests/test_pbr_switch_command_set.py -v

# Expected output:
# tests/test_pbr_data_plane.py::test_pbr_data_plane_routing          PASSED
# tests/test_pbr_data_plane.py::test_pbr_end_to_end_address_routing  PASSED
# tests/test_pbr_switch_command_set.py::... (45 tests)                PASSED
# 47 passed
```

### 10.2 All 6 Commands via CLI (standalone switch)

**Step 1** — Start the switch in PBR mode (edit your env config or use Python directly):

```python
# run_pbr_switch.py
import asyncio
from opencis.apps.cxl_switch import CxlSwitch, CxlSwitchConfig
from opencis.cxl.component.physical_port_manager import PortConfig, PORT_TYPE

async def main():
    config = CxlSwitchConfig(
        port_configs=[
            PortConfig(PORT_TYPE.USP),
            PortConfig(PORT_TYPE.DSP),
            PortConfig(PORT_TYPE.DSP),
        ],
        mctp_host="0.0.0.0",
        mctp_port=8100,
        enable_pbr=True,     # ← enables PBR command registration
    )
    switch = CxlSwitch(config, device_configs=[])
    await switch.run()

asyncio.run(main())
```

```bash
# Terminal 1 — start switch
py run_pbr_switch.py
```

**Step 2** — In a second terminal, run the smoke-test:

```bash
# Terminal 2 — run all 6 PBR commands
py pbr_cli_test.py --mctp-host 127.0.0.1 --mctp-port 8100
```

**Expected output:**
```
PBR FM CCI Command Smoke-Test
Connecting to MCTP endpoint at 127.0.0.1:8100 …
Connected.

────────────────────────────────────────────────────────────
1. Identify PBR Switch  [5700h]
  ✓ PASS  GAE Support Map : 0x0000000000000000
  ✓ PASS  Num DRTs        : 1
  ✓ PASS  Num RGTs        : 0
  ✓ PASS  Routing Caps    : 0x00

────────────────────────────────────────────────────────────
2. Configure PID Assignment — ASSIGN  [5704h]
  ✓ PASS  PID 0x123 → target_id=2 assigned successfully

────────────────────────────────────────────────────────────
3. Get PID Binding  [5705h]
  ✓ PASS  PID         : 0x123
  ✓ PASS  Bound PID   : 0xfff
  ✓ PASS  HMAT index  : 0

────────────────────────────────────────────────────────────
4. Configure PID Binding  [5706h]
  ✓ PASS  PID 0x123 bound to target PID 0x456

────────────────────────────────────────────────────────────
5. Set DRT  [5709h]
  ✓ PASS  DRT[0][0x123] → Physical Port 2 programmed

────────────────────────────────────────────────────────────
6. Get DRT  [5708h]
  ✓ PASS  DRT Index   : 0
  ✓ PASS  Start Entry : 0x123
  ✓ PASS  Entry[0x123] type=PHYSICAL_PORT  target=2

────────────────────────────────────────────────────────────
Results: 6 passed, 0 failed
────────────────────────────────────────────────────────────
```

### 10.3 Via Socket.IO (FM CLI with QEMU host)

Using any Socket.IO client (e.g. `socket.io-client` in Node.js, or the `python-socketio` library):

```python
import socketio
sio = socketio.SimpleClient()
sio.connect("http://localhost:8200")

# Identify
result = sio.call("pbr:identify")
print(result)

# Assign PID 0x123 to port 2
result = sio.call("pbr:configurePid", {
    "operation": 0,
    "entries": [{"pid": 0x123, "targetId": 2, "instanceId": 0}]
})

# Program DRT
result = sio.call("pbr:setDrt", {
    "drtIndex": 0,
    "startEntry": 0x123,
    "entries": [{"entryType": "PHYSICAL_PORT", "routingTarget": 2}]
})
```

---

## 11. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Pure-Python `packet_structs.py` fallback | No MSVC available; Cython `.pyd` auto-takes-precedence when compiled |
| `_inner_packet` stash on `PbrBasePacket` | Zero-copy decapsulation — avoids re-parsing when the packet was just constructed |
| `enable_pbr=False` default on `CxlSwitchConfig` | Backward-compatible — existing VSM topologies unchanged |
| PBR commands registered in `_initialize_mctp_endpoint()` | Follows exact same pattern as VCS, MLD commands — no new abstractions |
| `PbrHdmDecoderManager` separate from `PbrSwitchManager` | Separation of concerns: HDM decoder is data-plane (address→DPID), switch manager is control-plane (CCI state) |
