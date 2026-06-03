# GFD & GAE — Complete CCI Command Implementation Status
### Gap Analysis · opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## Summary

The CXL 4.0 specification defines **~30 CCI commands** across the PBR Switch FM API (§7.7.13)
and GAE command set (§7.7.14). This document maps every spec-required command to its
implementation status in opencis-core, with a clear breakdown of what is **DONE**,
what is **STUB / PARTIAL**, and what is **NOT IMPLEMENTED**.

```
TOTAL COMMANDS IN SPEC:   30
  ✅ Fully Implemented:   13
  🟡 Stub / Partial:       0
  ❌ Not Implemented:     17
```

---

## Part 1: PBR Switch FM API Commands (§7.7.13)

CXL Spec Rev 4.0 defines the following commands in the PBR Switch FM API block (opcode range 57xxh):

| Opcode   | Spec Command Name              | Status | Handler File | Notes |
|----------|-------------------------------|--------|-------------|-------|
| `0x5700` | Identify PBR Switch           | ✅ Done | [identify_pbr_switch.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/identify_pbr_switch.py) | Returns num_drts, gae_support_map |
| `0x5701` | Fabric Crawl Out              | ✅ Done | [fabric_crawl_out.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/fabric_crawl_out.py) | FM direct tunnel to GFD via DSP cci_fifo |
| `0x5702` | Get PID Interrupt Config      | ❌ Not Implemented | — | Interrupt vector config for PID events |
| `0x5703` | Set PID Interrupt Config      | ❌ Not Implemented | — | Write interrupt vector for PIDs |
| `0x5704` | Configure PID Assignment      | ✅ Done | [configure_pid_assignment.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_assignment.py) | ASSIGN / CLEAR PID → port mapping |
| `0x5705` | Get PID Binding               | ✅ Done | [get_pid_binding.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/get_pid_binding.py) | Returns bound PID or 0xFFF |
| `0x5706` | Configure PID Binding         | ✅ Done | [configure_pid_binding.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_binding.py) | Background command, BIND/UNBIND |
| `0x5707` | Get PID Binding Snapshot      | ❌ Not Implemented | — | Bulk read of all vPPB bindings at once |
| `0x5708` | Get DRT                       | ✅ Done | [get_drt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/get_drt.py) | Read DRT entries by DPID range |
| `0x5709` | Set DRT                       | ✅ Done | [set_drt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/set_drt.py) | Write DRT entries, rejects RESERVED |
| `0x570A` | Get RGT                       | ❌ Not Implemented | — | Read Routing Group Table (multicast) |
| `0x570B` | Set RGT                       | ❌ Not Implemented | — | Write RGT for multicast routing |
| `0x570C` | Get PID Interrupt Status      | ❌ Not Implemented | — | Read pending interrupt status per PID |
| `0x570D` | Clear PID Interrupt Status    | ❌ Not Implemented | — | Acknowledge/clear PID interrupt status |
| `0x570E` | Get PID Event Records         | ❌ Not Implemented | — | Read event log per PID |
| `0x570F` | Clear PID Event Records       | ❌ Not Implemented | — | Clear event log per PID |

### PBR Switch Summary
```
Implemented:     7 / 16   (0x5700, 0x5701, 0x5704, 0x5705, 0x5706, 0x5708, 0x5709)
Not Implemented: 9 / 16   (0x5702, 0x5703, 0x5707, 0x570A–0x570F)
```

---

## Part 2: GAE Command Set (§7.7.14)

CXL Spec Rev 4.0 defines the following commands in the GAE block (opcode range 58xxh):

| Opcode   | Spec Command Name              | Status | Handler File | Notes |
|----------|-------------------------------|--------|-------------|-------|
| `0x5800` | Identify GAE                  | ✅ Done | [identify_gae.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/identify_gae.py) | Returns G-FAM vPPB list |
| `0x5801` | Get PID Interrupt Vector      | ❌ Not Implemented | — | Read IRQ vector assigned to a PID |
| `0x5802` | Get PID Access Vectors        | ✅ Done | [get_pid_access_vectors.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/get_pid_access_vectors.py) | GMV and VTV bitmasks per PID |
| `0x5803` | Get Fast IDT Capabilities     | ❌ Not Implemented | — | Read Fast IDT hardware capabilities |
| `0x5804` | Set Fast IDT Configuration    | ❌ Not Implemented | — | Configure Fast IDT mode |
| `0x5805` | Get Fast Segment Entries      | ❌ Not Implemented | — | Read Fast IDT segment table |
| `0x5806` | Set Fast Segment Entries      | ❌ Not Implemented | — | Write Fast IDT segment table |
| `0x5807` | Get IDT DPID Entries          | ❌ Not Implemented | — | Read IDT (Interrupt Dispatch Table) per DPID |
| `0x5808` | Set IDT DPID Entries          | ❌ Not Implemented | — | Write IDT entries for DPID routing |
| `0x5809` | Proxy GFD Management Command  | ✅ Done | [proxy_gfd_mgmt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/proxy_gfd_mgmt.py) | Async proxy; returns thread_id |
| `0x580A` | Get Proxy Thread Status       | ✅ Done | [get_proxy_thread_status.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/get_proxy_thread_status.py) | Poll proxy completion + GFD response |
| `0x580B` | Cancel Proxy Thread           | ✅ Done | [cancel_proxy_thread.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/cancel_proxy_thread.py) | Cancels asyncio task by thread_id |
| `0x580C` | Get G-FAM Region Info         | ❌ Not Implemented | — | Read Global Fabric Attach memory region |
| `0x580D` | Set G-FAM Region Config       | ❌ Not Implemented | — | Configure G-FAM region boundaries |
| `0x580E` | Get G-FAM Extent List         | ❌ Not Implemented | — | Read dynamic capacity extent list |

### GAE Summary
```
Implemented:     6 / 15   (0x5800, 0x5802, 0x5809, 0x580A, 0x580B + FabricCrawlOut 0x5701)
Not Implemented: 9 / 15   (0x5801, 0x5803–0x5808, 0x580C–0x580E)
```

---

## Part 3: Not-Implemented Groups Explained

### Group 1 — PID Interrupt / Event Commands (4 commands)
**Opcodes:** `0x5702`, `0x5703`, `0x570C`, `0x570D`

These commands configure and manage **interrupts per PID**. When a GFD or fabric event occurs,
the switch can raise an MSI-X interrupt to the host. The host uses these commands to:
- Assign an IRQ vector to a PID (`Set PID Interrupt Config`)
- Read back the assignment (`Get PID Interrupt Config`)
- Check which PIDs have pending events (`Get PID Interrupt Status`)
- Acknowledge handled events (`Clear PID Interrupt Status`)

**Why not implemented:** The simulator currently uses polling (the test suite calls
`get_pid_binding` to verify state changes). MSI-X interrupt injection into a simulated
host environment requires additional host-side infrastructure.

**Impact:** GFD management works via polling. Event-driven host drivers cannot be tested.

---

### Group 2 — PID Event Log Commands (2 commands)
**Opcodes:** `0x570E`, `0x570F`

Per-PID event logs allow the FM to:
- Read the event record ring buffer for a specific PID (`Get PID Event Records`)
- Clear acknowledged events (`Clear PID Event Records`)

**Why not implemented:** The generic event log infrastructure (`GET_EVENT_RECORDS` at 0x0100)
exists but per-PID event logs require a separate PID-indexed ring buffer in `PbrSwitchManager`.

---

### Group 3 — PID Binding Snapshot (1 command)
**Opcode:** `0x5707`

Returns the **complete binding state** of all vPPBs in one response — a bulk alternative to
calling `Get PID Binding` (0x5705) once per vPPB.

**Why not implemented:** The single-vPPB `Get PID Binding` (0x5705) is sufficient for the
commissioning flow. Snapshot is a performance optimization for large VCS configurations
with many vPPBs.

**Impact:** FM must iterate and call 0x5705 N times to audit full binding state.

---

### Group 4 — Routing Group Table (RGT) Commands (2 commands)
**Opcodes:** `0x570A`, `0x570B`

The RGT enables **multicast routing** — a single DPID can fan-out to multiple physical ports.
Required for:
- Fabric-wide broadcast (e.g. GFAM memory range announcements)
- Multi-target routing for redundant GFD paths

**Why not implemented:** Current PBR implementation only supports `PHYSICAL_PORT` unicast routing.
`DrtEntryType.RGT_INDEX` is defined but the RGT table in `PbrSwitchManager` has no entries.

**Impact:** All DRT entries must be `PHYSICAL_PORT`. Multicast not supported.

---

### Group 5 — Fast IDT Commands (6 commands)
**Opcodes:** `0x5803`, `0x5804`, `0x5805`, `0x5806`, `0x5807`, `0x5808`

Fast IDT (Interrupt Dispatch Table) is a GAE hardware feature for **low-latency interrupt
routing** from GFDs to the host. It provides:
- A hardware-managed dispatch table mapping DPIDs to IRQ vectors
- Segment-based coalescing of interrupts
- Sub-microsecond host notification of GFD events

**Why not implemented:** Fast IDT requires MSI-X hardware simulation and a host-side
IRQ injection model. These are advanced features beyond the basic GFD data/control plane.

**Impact:** GFD-to-host interrupt notification must use software polling only.

---

### Group 6 — G-FAM Region Commands (2 commands)
**Opcodes:** `0x580C`, `0x580D`, `0x580E`

G-FAM (Global Fabric Attach Memory) is the shared memory pool aspect of GFA.
These commands configure and query the global memory regions that GFDs can expose
to multiple hosts simultaneously.

**Why not implemented:** The current GFD implementation is IO-only (`mem_capable=0`).
G-FAM requires a GFD with `mem_capable=1` and shared HDM region management — a
significant extension to the current `CxlGfdDevice`.

**Impact:** Multi-host shared memory via GFD is not available. This is the largest
architectural gap for a complete CXL 4.0 PBR implementation.

---

## Part 4: Full Implementation Roadmap

### Priority 1 — High Value, Moderate Effort

| Opcode | Command | Why Prioritize | Estimated Effort |
|--------|---------|----------------|-----------------|
| `0x5707` | Get PID Binding Snapshot | Needed for FM audits with many vPPBs | Small — iterate existing `_pid_bindings` dict |
| `0x570A` | Get RGT | Needed for multicast/broadcast testing | Medium — add RGT table to PbrSwitchManager |
| `0x570B` | Set RGT | Pair with Get RGT | Medium — wire to PbrSwitchRouter fan-out |
| `0x5801` | Get PID Interrupt Vector | Required for host IRQ-driven GFD drivers | Medium — add IRQ vector map to GaeManager |

### Priority 2 — Medium Value, Higher Effort

| Opcode | Command | Why Prioritize | Estimated Effort |
|--------|---------|----------------|-----------------|
| `0x5702` | Get PID Interrupt Config | Required for full MSI-X flow | Medium — add config store to PbrSwitchManager |
| `0x5703` | Set PID Interrupt Config | Pair with Get | Medium |
| `0x570C` | Get PID Interrupt Status | Event-driven host drivers | Large — interrupt injection into sim |
| `0x570D` | Clear PID Interrupt Status | Pair with Get | Large |
| `0x570E` | Get PID Event Records | Per-PID audit log | Medium — per-PID ring buffer |
| `0x570F` | Clear PID Event Records | Pair with Get | Small — add clear method |

### Priority 3 — G-FAM / Fast IDT (Large architectural change)

| Opcode | Command | Why Prioritize | Estimated Effort |
|--------|---------|----------------|-----------------|
| `0x5803` | Get Fast IDT Capabilities | MSI-X acceleration | Large — new hardware model |
| `0x5804` | Set Fast IDT Configuration | Pair with Get | Large |
| `0x5805` | Get Fast Segment Entries | IDT segment table | Large |
| `0x5806` | Set Fast Segment Entries | Pair with Get | Large |
| `0x5807` | Get IDT DPID Entries | Per-DPID IRQ dispatch | Large |
| `0x5808` | Set IDT DPID Entries | Pair with Get | Large |
| `0x580C` | Get G-FAM Region Info | Shared memory via GFD | Very Large — needs mem_capable GFD |
| `0x580D` | Set G-FAM Region Config | Pair with Get | Very Large |
| `0x580E` | Get G-FAM Extent List | Dynamic capacity for GFD | Very Large |

---

## Part 5: What Needs to Be Added Per Command

### `0x5707` — Get PID Binding Snapshot

**Spec requirement:** Return the PID binding for ALL vPPBs in a VCS in one response.

**What needs to be added:**
```python
# New handler file: cci/fabric_manager/pbr_switch/get_pid_binding_snapshot.py
class GetPidBindingSnapshotCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING_SNAPSHOT  # 0x5707

    async def _execute(self, request: CciRequest) -> CciResponse:
        vcs_id = request.payload[0]
        # Return all (vcs_id, vppb_id) → pid mappings in one response
        bindings = self._pbr_switch_manager.get_all_pid_bindings(vcs_id)
        return CciResponse(payload=serialize_binding_list(bindings))

# New method in pbr_switch_manager.py:
def get_all_pid_bindings(self, vcs_id: int) -> List[PidBinding]:
    return [
        binding for (vcs, _), binding in self._pid_bindings.items()
        if vcs == vcs_id
    ]
```

**Register in:** `MctpCciExecutor.__init__()` and `socketio_server.py` as `pbr:getPidBindingSnapshot`.

---

### `0x570A` / `0x570B` — Get/Set RGT

**Spec requirement:** Read/write the Routing Group Table for multicast PID routing.

**What needs to be added:**
```python
# New field in pbr_switch_manager.py PbrSwitchManager:
self._rgt_tables: List[RgtTable] = [
    RgtTable(num_ports=num_ports) for _ in range(num_rgts)
]

# RgtTable: each entry maps an RGT index to a set of physical port bitmasks
@dataclass
class RgtEntry:
    port_bitmask: int = 0   # bit N=1 means include physical port N in fanout

# New handler files:
# cci/fabric_manager/pbr_switch/get_rgt.py -- reads _rgt_tables[drt_index].entries
# cci/fabric_manager/pbr_switch/set_rgt.py -- writes _rgt_tables[drt_index].entries

# Update PbrSwitchRouter._route_packet():
elif drt_entry.entry_type == DrtEntryType.RGT_INDEX:
    rgt_entry = self._pbr_switch_manager.get_rgt_entry(drt_entry.routing_target)
    for port_idx, bit in enumerate(rgt_entry.port_bitmask bits):
        if bit:
            await self._port_fifos[port_idx].host_to_target.put(packet_copy)
```

---

### `0x5702` / `0x5703` — Get/Set PID Interrupt Config

**Spec requirement:** Each PID can have an MSI-X interrupt vector assigned.
When a GFD event fires, the switch raises that IRQ to the host.

**What needs to be added:**
```python
# New field in pbr_switch_manager.py:
self._pid_irq_config: Dict[int, PidIrqConfig] = {}  # pid -> (vector, enable_mask)

@dataclass
class PidIrqConfig:
    interrupt_vector: int = 0    # MSI-X table index
    enable_mask: int = 0         # which event types trigger IRQ

# New handler files:
# cci/fabric_manager/pbr_switch/get_pid_interrupt_config.py
# cci/fabric_manager/pbr_switch/set_pid_interrupt_config.py

# Host-side IRQ injection (future):
# Requires host simulation to accept MSI-X writes from switch
```

---

### `0x5801` — Get PID Interrupt Vector (GAE)

**Spec requirement:** Read the IRQ vector currently assigned to a PID, from the GAE's
perspective (host-facing).

**What needs to be added:**
```python
# New handler file: cci/fabric_manager/gae/get_pid_interrupt_vector.py
class GetPidInterruptVectorCommand(CciForegroundCommand):
    OPCODE = CCI_GAE_COMMAND_OPCODE.GET_PID_INTERRUPT_VECTOR  # 0x5801

    async def _execute(self, request: CciRequest) -> CciResponse:
        pid = struct.unpack_from("<H", request.payload, 0)[0] & 0x0FFF
        vector = self._gae_manager.get_pid_irq_vector(pid)
        return CciResponse(payload=struct.pack("<H", vector))

# New method in gae_manager.py:
def get_pid_irq_vector(self, pid: int) -> int:
    return self._pid_irq_vectors.get(pid, 0xFFFF)  # 0xFFFF = no vector assigned
```

---

### `0x580C`–`0x580E` — G-FAM Region Commands

**Spec requirement:** Expose GFD shared memory regions to multiple hosts simultaneously.
Requires `mem_capable=1` on the GFD.

**What needs to be added (large scope):**
1. `CxlGfdDevice` must be extended to optionally support `mem_capable=1`
2. `GaeManager` needs a `GFamRegionTable` data structure
3. Three new command handlers for Get/Set region config and extent lists
4. `PbrSwitchRouter` must handle GFD memory TLPs differently from IO TLPs
5. Host-side ACPI HMAT updates for multi-host visibility

---

## Part 6: Opcode Cross-Reference Table (Complete)

```
Opcode  | Name                        | Impl | Spec Section
--------|-----------------------------|----- |-------------
0x5700  | Identify PBR Switch         | ✅   | §7.7.13.1
0x5701  | Fabric Crawl Out            | ✅   | §7.7.13.2
0x5702  | Get PID Interrupt Config    | ❌   | §7.7.13.3
0x5703  | Set PID Interrupt Config    | ❌   | §7.7.13.4
0x5704  | Configure PID Assignment    | ✅   | §7.7.13.5
0x5705  | Get PID Binding             | ✅   | §7.7.13.6
0x5706  | Configure PID Binding       | ✅   | §7.7.13.7
0x5707  | Get PID Binding Snapshot    | ❌   | §7.7.13.8
0x5708  | Get DRT                     | ✅   | §7.7.13.9
0x5709  | Set DRT                     | ✅   | §7.7.13.9
0x570A  | Get RGT                     | ❌   | §7.7.13.10
0x570B  | Set RGT                     | ❌   | §7.7.13.10
0x570C  | Get PID Interrupt Status    | ❌   | §7.7.13.11
0x570D  | Clear PID Interrupt Status  | ❌   | §7.7.13.12
0x570E  | Get PID Event Records       | ❌   | §7.7.13.13
0x570F  | Clear PID Event Records     | ❌   | §7.7.13.14
0x5800  | Identify GAE                | ✅   | §7.7.14.1
0x5801  | Get PID Interrupt Vector    | ❌   | §7.7.14.2
0x5802  | Get PID Access Vectors      | ✅   | §7.7.14.3
0x5803  | Get Fast IDT Capabilities   | ❌   | §7.7.14.4
0x5804  | Set Fast IDT Configuration  | ❌   | §7.7.14.5
0x5805  | Get Fast Segment Entries    | ❌   | §7.7.14.6
0x5806  | Set Fast Segment Entries    | ❌   | §7.7.14.7
0x5807  | Get IDT DPID Entries        | ❌   | §7.7.14.8
0x5808  | Set IDT DPID Entries        | ❌   | §7.7.14.9
0x5809  | Proxy GFD Mgmt Command      | ✅   | §7.7.14.10
0x580A  | Get Proxy Thread Status     | ✅   | §7.7.14.11
0x580B  | Cancel Proxy Thread         | ✅   | §7.7.14.12
0x580C  | Get G-FAM Region Info       | ❌   | §7.7.14.13
0x580D  | Set G-FAM Region Config     | ❌   | §7.7.14.14
0x580E  | Get G-FAM Extent List       | ❌   | §7.7.14.15
--------|-----------------------------|----- |-------------
TOTAL   |                             |13/30 |
```

---

## Part 7: What Works End-to-End Today

Despite 17 missing commands, the core GFD use case is **fully functional**:

```
What you CAN do today:
  ✅ Commission a GFD (6-step workflow: Identify → PID → DRT → Verify → Bind → Verify)
  ✅ Route TLPs from host to GFD via DRT (PbrSwitchRouter)
  ✅ Read/write GFD BAR-0 MMIO registers from the host
  ✅ Send CCI commands from FM directly to GFD (FabricCrawlOut 0x5701)
  ✅ Send CCI commands from host to GFD via GAE proxy (0x5809 + 0x580A)
  ✅ Cancel proxy threads (0x580B)
  ✅ Discover GAE capabilities (0x5800 Identify GAE)
  ✅ Query PID access vectors (0x5802)
  ✅ Test all 13 implemented commands via FmSmbusMctpServer (port 8301)
  ✅ Issue all CLI commands via FM SocketIO server (port 8200)

What you CANNOT do today:
  ❌ Multicast routing (needs RGT 0x570A/0x570B)
  ❌ GFD interrupt-driven host notification (needs 0x5702-0x5703, 0x570C-0x570D)
  ❌ Per-PID event logs (needs 0x570E-0x570F)
  ❌ Bulk vPPB binding audit (needs 0x5707 Snapshot)
  ❌ Fast IDT interrupt acceleration (needs 0x5803-0x5808)
  ❌ G-FAM shared memory (needs 0x580C-0x580E + mem_capable GFD)
```

---

*Document version: 1.0 — June 2026*
*Branch: `smbus_dual_port`*
*Spec reference: CXL Specification Revision 4.0 Version 1.0, §7.7.13–§7.7.14*
