# GFD & GAE — Deep Explanation for the Team
## Can We Do MMIO? What Commands Are Missing? Is GAE Hardware? QEMU Impact?
### opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## PART 1: Can We Do MMIO From the Host With These 13 Commands?

### Short Answer
**YES. MMIO (MemRead / MemWrite) from the host to the GFD BAR-0 works completely
with the 13 commands already implemented.** MMIO is on the data plane. CCI commands
(our 13) are on the control plane. They are completely separate paths.

### How MMIO Actually Works (Data Plane — Zero CCI Commands Needed)

```
HOST OS
  │
  │  CPU issues MemRead to BAR-0 address (e.g. 0xFE00_0000)
  │
  ▼
PCIe Root Complex
  │
  │  CXL.io TLP: MRd64 header [addr=0xFE00_0000, len=4B, tag=0x01]
  │
  ▼
USP of PBR Switch (port 0, host side)
  │
  │  PbrSwitchRouter._process_port_host_ingress()
  │  Packet is HBR (address-based, not PBR yet)
  │
  │  Step 1: Extract address from CxlIoMemReqPacket.get_address() = 0xFE00_0000
  │  Step 2: PbrHdmDecoderManager.get_dpid(0xFE00_0000) = 0x010
  │          (HDM decoder maps address range → DPID)
  │  Step 3: PbrBasePacket.encapsulate(spid=0, dpid=0x010, inner_packet=MRd)
  │          Now it is a PBR packet with DPID=0x010
  │  Step 4: DRT lookup: DRT[0][0x010] = PHYSICAL_PORT → port 1
  │  Step 5: Forward to port_fifos[1].host_to_target
  │
  ▼
GFD on DSP port 1
  │
  │  SwitchConnectionClient delivers packet from host_to_target
  │  CxlIoManager receives MRd64, reads GfdMmioRegisters[offset]
  │  Returns CxlIo Completion with data
  │
  ▼
Response travels back: GFD → port_fifos[1].target_to_host → Router → USP → Host

HOST OS: MemRead returns 4 bytes of data from GFD BAR-0
```

### What the 6 Commissioning Commands Do for MMIO

The commissioning commands set up the routing tables that the data plane uses.
Without them, MMIO packets arrive at the switch but get dropped.

```
Command               What it sets up                 Required for MMIO?
──────────────────    ────────────────────────────    ─────────────────
pbr:identify          Learn num_drts (sanity check)   No (but always run first)
pbr:configurePid      PID→port mapping in switch      No (DRT is what matters)
pbr:setDrt            DRT[0][0x010]=PHYSICAL_PORT→1  YES ← MMIO routing fails without this
pbr:getPidBinding     Verify unbound                   No (verification only)
pbr:configurePidBinding  vPPB→PID binding (ACPI/OS)  Yes (for OS enumeration)
pbr:getPidBinding     Verify bound                     No (verification only)
```

**The minimum for raw MMIO to work:** just `pbr:setDrt`. That's it.
Everything else is for proper OS enumeration, ACPI topology, and FM auditing.

---

## PART 2: Detailed Explanation of Every Missing Command
### Why Not Implemented, Why It Doesn't Matter for Simple Device

---

### ❌ `0x5707` — Get PID Binding Snapshot

**What the spec says:**
Return the binding state (PID, HMAT info) of ALL vPPBs inside a VCS in a single
response, instead of querying them one by one.

**Why we didn't implement it:**
We have `Get PID Binding` (0x5705) which reads one vPPB at a time.
For a simple GFD with one vPPB (vcs=0, vppb=0), calling 0x5705 once is sufficient.
Snapshot is a performance optimization for large fabrics with 256+ vPPBs.

**Why it doesn't matter for simple device:**
Our GFD has exactly ONE vPPB. There is nothing to snapshot.

**Impact:** None for single-GFD testing. Would matter in a 64-GFD fabric where
the FM needs to audit all bindings quickly without 64 round trips.

---

### ❌ `0x5702` / `0x5703` — Get/Set PID Interrupt Config

**What the spec says:**
Each PID can be configured with an MSI-X interrupt vector. When a fabric event
occurs for that PID (e.g. GFD plugged in, error, hot-plug), the switch raises
that interrupt to the host.

**Why we didn't implement it:**
This requires the simulator to inject MSI-X interrupts into the simulated host.
Our current host simulation doesn't have an MSI-X table model — it uses
CCI polling (the FM calls Get PID Binding to check state changes instead of
waiting for an interrupt).

**Why it doesn't matter for simple device:**
Polling works fine for commissioning. We call `Get PID Binding` to verify.
In real hardware, a production OS driver would set up MSI-X and wait for
the interrupt instead of polling. For our simulator tests, polling is equivalent.

**Impact on MMIO:** Zero. Interrupts are for events (hot-plug, error).
MMIO read/write is unaffected by interrupt configuration.

---

### ❌ `0x570A` / `0x570B` — Get/Set RGT (Routing Group Table)

**What the spec says:**
The RGT enables **multicast routing**. A single DPID can resolve to a *group* of
physical ports, and the switch fans-out the packet to all of them.
Example: DPID=0xFFF could broadcast to all GFDs simultaneously.

**Why we didn't implement it:**
`PbrSwitchRouter` currently handles only `DrtEntryType.PHYSICAL_PORT` (unicast).
`DrtEntryType.RGT_INDEX` is defined in the enum but the router drops any DRT
entry that is not `PHYSICAL_PORT`. The `PbrSwitchManager` has no RGT table.

```python
# pbr_switch_router.py line 110-114
if drt_entry.entry_type != DrtEntryType.PHYSICAL_PORT:
    logger.warning("Drop: DPID DRT entry is not PHYSICAL_PORT")
    return   # ← multicast would need to fan out here instead
```

**Why it doesn't matter for simple device:**
We have ONE GFD on ONE port. Multicast has no meaning with a single target.
All our DRT entries are `PHYSICAL_PORT`.

**Impact on MMIO:** Zero for unicast. Would be needed for broadcast fabric management.

---

### ❌ `0x570C` / `0x570D` — Get/Clear PID Interrupt Status

**What the spec says:**
A bitmask of which PIDs have pending unacknowledged events. The FM reads this
after receiving an MSI-X interrupt to find which PID triggered it, then clears
that PID's interrupt status after handling.

**Why we didn't implement it:**
Requires the interrupt infrastructure (0x5702/03 above) first. Without MSI-X
injection, there are no pending interrupt statuses to read or clear.

**Why it doesn't matter for simple device:**
Without interrupts, there is nothing to read or clear. The FM knows state
changes by issuing explicit CCI queries.

**Impact on MMIO:** Zero. These are pure control-plane event management commands.

---

### ❌ `0x570E` / `0x570F` — Get/Clear PID Event Records

**What the spec says:**
A per-PID event log ring buffer. Records timestamped events (GFD connection,
disconnect, link error, hot-plug) for each active PID.

**Why we didn't implement it:**
Requires a per-PID event ring buffer in `PbrSwitchManager`. The generic
event log at opcode `0x0100` (Get Event Records) exists for the switch itself,
but per-PID logging needs a separate indexed structure.

**Why it doesn't matter for simple device:**
Our GFD never fails, never hot-unplugs, never generates link errors in the
simulator. There are no events to log. Real hardware needs this for diagnostics.

**Impact on MMIO:** Zero. MMIO is unaffected by event logging.

---

### ❌ `0x5801` — Get PID Interrupt Vector (GAE)

**What the spec says:**
From the HOST side (via GAE), read what MSI-X vector the GAE will use to notify
the host when a specific PID's event fires.

**Why we didn't implement it:**
Same reason as 0x5702 — no MSI-X model in the simulator.

**Why it doesn't matter for simple device:**
Host can manage the GFD via the proxy path (0x5809) without needing interrupts.

---

### ❌ `0x5803`–`0x5808` — Fast IDT Commands (6 commands)

**What the spec says:**
Fast IDT (Interrupt Dispatch Table) is a **hardware accelerator inside the GAE
USP port logic** that provides ultra-low latency interrupt routing.

Instead of the software path:
```
GFD event → MSI-X → host ISR → read interrupt status → call Get PID interrupt status
```

Fast IDT does:
```
GFD event → IDT entry lookup → direct MSI-X to host CPU → host ISR (no CCI needed)
```

The 6 commands configure:
- IDT capabilities (what the hardware can do)
- Fast Segment entries (PID ranges mapped to MSI-X vectors)
- DPID-level interrupt entries

**Why we didn't implement it:**
Fast IDT is a pure hardware acceleration feature. Our software router
(`PbrSwitchRouter`) processes packets in Python asyncio — there is no equivalent
of a hardware IDT lookup engine. Implementing it in software would not model the
real behavior (latency is the point; in simulation all paths have similar latency).

**Why it doesn't matter for simple device:**
A simple GFD device doesn't need sub-microsecond interrupt latency in simulation.
The proxy path (0x5809 + 0x580A) achieves the same result with software polling.

**Impact on MMIO:** Completely unrelated. Fast IDT is interrupt acceleration only.

---

### ❌ `0x580C`–`0x580E` — G-FAM Region Commands (3 commands)

**What the spec says:**
G-FAM (Global Fabric Attach Memory) allows GFD devices to expose **shared memory**
accessible from multiple hosts simultaneously. These 3 commands let the host:
- `0x580C` Get G-FAM Region Info — read what memory regions the GFD offers
- `0x580D` Set G-FAM Region Config — configure size and access permissions
- `0x580E` Get G-FAM Extent List — read dynamic capacity extents (like NVMe DCD)

**Why we didn't implement it:**
Our `CxlGfdDevice` has `mem_capable=0` — it is IO-only. G-FAM requires:
1. `mem_capable=1` on the GFD (needs HDM decoder)
2. A shared memory backing store accessible from multiple host connections
3. ACPI HMAT updates (latency/BW tables) for each host that mounts the region
4. `PbrSwitchRouter` changes to route CXL.mem (not just CXL.io) TLPs to GFDs
5. `GaeManager` to maintain a region table per GFD

This is a completely new device class (G-FAM GFD vs IO-only GFD). Our GFD is
intentionally IO-only (CXL.io only, BAR-0 only) as the starting point.

**Why it doesn't matter for simple device:**
A simple IO-only GFD has no memory regions to expose. G-FAM is for fabric-attached
pooled memory — the "memory server" use case.

**Impact on MMIO:** The BAR-0 CXL.io MMIO works without G-FAM. G-FAM would add
a *second* type of MMIO — CXL.mem mapped ranges — on top of CXL.io BAR-0.

---

## PART 3: If We Move to QEMU-Enumerated Device — What Changes?

### What "QEMU-Enumerated" Means
When QEMU boots a CXL device, the host OS actually enumerates it:
1. BIOS/UEFI probes PCIe config space (BDF assignment)
2. OS assigns BARs (MMIO address ranges)
3. ACPI provides SRAT/HMAT tables for memory topology
4. OS driver calls CCI Identify and registers the device in `/sys/bus/cxl/`

For this to work for a GFD, the PBR switch's vPPB binding (CCI command 0x5706)
must complete BEFORE ACPI tables are generated, because the OS needs to know
which memory ranges map to which NUMA node.

### Commands Already Enough for QEMU GFD IO-Only (BAR-0 only)

```
Current 13 commands cover:
  ✅ Commission the switch before QEMU boots (all 6 steps)
  ✅ Set DRT so MMIO BAR-0 addresses route to GFD
  ✅ Bind vPPB so OS enumeration sees the GFD
  ✅ FM can tunnel CCI commands to GFD (FabricCrawlOut 0x5701)
  ✅ Host can proxy CCI to GFD via GAE (0x5809/0x580A)
  ✅ ACPI HMAT latency/BW can be set via hmat fields in 0x5706 payload

For QEMU IO-only GFD: ZERO extra commands needed.
```

### Commands Needed If QEMU Device Needs Interrupts (MSI-X from GFD)

```
Add:
  ❌ 0x5702 Set PID Interrupt Config  -- assign MSI-X vector to PID
  ❌ 0x5703 Get PID Interrupt Config  -- read back assignment
  ❌ 0x570C Get PID Interrupt Status  -- which PID fired after ISR
  ❌ 0x570D Clear PID Interrupt Status -- acknowledge after handling
  ❌ 0x5801 Get PID Interrupt Vector  -- host reads from GAE side

  Total: 5 new commands for interrupt-driven host driver
```

### Commands Needed If QEMU Device Has G-FAM (CXL.mem via GFD)

```
This is the "memory server" GFD with mem_capable=1.

Add:
  ❌ 0x580C Get G-FAM Region Info
  ❌ 0x580D Set G-FAM Region Config
  ❌ 0x580E Get G-FAM Extent List

Plus significant code changes:
  - CxlGfdDevice: mem_capable=1, add HDM decoder
  - PbrSwitchRouter: route CXL.mem TLPs (not just CXL.io)
  - GaeManager: add GFamRegionTable
  - ACPI DSDT: expose G-FAM memory range as NUMA node

  Total: 3 new CCI commands + major device architecture change
```

### Commands Needed for Advanced Fabric (Multi-Host, Multi-GFD)

```
Add:
  ❌ 0x570A Get RGT  -- multicast routing for broadcast commissioning
  ❌ 0x570B Set RGT  -- configure multicast groups
  ❌ 0x5707 Get PID Binding Snapshot -- audit all bindings quickly
  ❌ 0x5803-08 Fast IDT -- if low-latency interrupts matter

  Total: 8 more commands for full-scale fabric
```

### QEMU Impact Summary

| QEMU Scenario | Extra Commands Needed | Effort |
|---------------|----------------------|--------|
| IO-only GFD (BAR-0 MMIO, polling) | **0** — works today | None |
| IO-only GFD + MSI-X interrupts | 5 commands | Medium |
| G-FAM GFD (shared memory) | 3 commands + major arch change | Large |
| Multi-host fabric + multicast | 8 more commands | Large |

---

## PART 4: Is GAE a Hardware Block? What Do We Need to Know?

### Yes — GAE is a Hardware Block in the Switch USP

In real CXL hardware, the **GAE lives inside the switch chip** on the
Host-Edge USP (Upstream Port toward the host). It is a logic block inside
the switch ASIC — not a separate chip, not a separate PCIe function.

```
PBR Switch ASIC (single chip)
┌─────────────────────────────────────────────────┐
│                                                   │
│  USP Port 0 (host-facing)                         │
│  ┌──────────────────────────────────────────┐    │
│  │  CXL.io Config Space Handler             │    │
│  │  CXL.io MMIO Handler (BAR-0 of switch)   │    │
│  │  ┌──────────────────────────────────┐    │    │
│  │  │  GAE Logic Block                 │    │    │
│  │  │  - CCI Mailbox (registers)       │    │    │
│  │  │  - Proxy Thread State Registers  │    │    │
│  │  │  - G-FAM Region Table (SRAM)     │    │    │
│  │  │  - Fast IDT (hardware table)     │    │    │
│  │  │  - PID Access Vector Registers   │    │    │
│  │  └──────────────────────────────────┘    │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  PBR Routing Fabric (crossbar)                    │
│  DRT SRAM (index=DPID, value=port+type)           │
│  RGT SRAM (multicast bitmasks)                    │
│  PID Assignment Table (SRAM)                      │
│  PID Binding Table (SRAM)                         │
│                                                   │
│  DSP Port 1 (GFD-facing)                          │
│  ┌──────────────────────────────────────────┐    │
│  │  CXL.io forwarding engine                │    │
│  │  cci_fifo (CCI mailbox relay to GFD)     │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
└─────────────────────────────────────────────────┘
```

### How We Model It in Software

Since GAE is hardware inside the switch, in our simulator it lives inside the
switch process — not the GFD process, not the FM process:

```
Our Software Model:
┌─────────────────────────────────────────────────┐
│  Switch Python Process                           │
│                                                  │
│  MctpCciExecutor                                 │
│    registers PBR commands (5700h-5709h)          │
│    registers GAE commands (5800h-580Bh)   ← GAE  │
│                                                  │
│  GaeManager                               ← GAE  │
│    _vppbs[]          (G-FAM vPPB list)           │
│    _proxy_threads{}  (active proxies)            │
│    _gfd_tunnel       (DspCciTunnel to GFD)       │
│    _gfd_executor     (test-mode direct call)     │
│                                                  │
│  PbrSwitchManager                                │
│    _drt_tables[]     (routing state)             │
│    _pid_assignments  (PID→port map)              │
│    _pid_bindings     (vPPB→PID map)              │
│                                                  │
│  PbrSwitchRouter                                 │
│    _route_packet()   (data plane forwarding)     │
│                                                  │
└─────────────────────────────────────────────────┘
```

### What the CXL Spec Requires the GAE to Know

The GAE hardware (and thus our `GaeManager`) must track these things:

| What GAE Tracks | Our Implementation | Spec Section |
|----------------|-------------------|-------------|
| List of vPPBs with G-FAM support | `GaeManager._vppbs[]` (empty for IO-only GFD) | §7.7.14.1 |
| PID Access Vectors (which host can access which PID) | `get_pid_access_vectors()` returns GMV/VTV bitmasks | §7.7.14.3 |
| Active proxy threads | `GaeManager._proxy_threads{}` | §7.7.14.10-12 |
| G-FAM region table | **NOT IMPLEMENTED** (mem_capable=0 GFD) | §7.7.14.13 |
| Fast IDT (interrupt dispatch) | **NOT IMPLEMENTED** (no IRQ model) | §7.7.14.4-9 |
| PID interrupt vectors | **NOT IMPLEMENTED** (no MSI-X model) | §7.7.14.2 |

### Key Insight: GAE CCI Mailbox is on the USP

The host sends CCI commands (like `ProxyGfdMgmt 0x5809`) to the **switch's USP port**,
not to the GFD. The USP has a CCI mailbox register block (like a normal CXL device
mailbox). The GAE logic reads from that mailbox and processes the command.

```
Host writes to switch USP CCI mailbox:
  Opcode=0x5809 (Proxy GFD Mgmt)
  Payload = {gfd_opcode=0x0001, gfd_payload=[]}

Switch GAE logic:
  1. Read from CCI mailbox
  2. MctpCciExecutor.execute_command(opcode=0x5809)
  3. ProxyGfdMgmtCommand._execute()
  4. GaeManager.start_proxy(gfd_opcode=0x0001, payload=[])
  5. asyncio.create_task(_run()) → DspCciTunnel.send_and_wait(CciRequest)
  6. cci_fifo.host_to_target.put(req) → GFD CciExecutor
  7. GFD responds on cci_fifo.target_to_host
  8. GAE stores result in proxy_threads[1].response
  Return: {thread_id=1}

Host polls:
  Opcode=0x580A (Get Proxy Thread Status)
  Payload = {thread_id=1}
  Response = {completed=True, gfd_return_code=0, gfd_response_payload=[...]}
```

### Why This Design Matters for Our Simulator

In real hardware:
- The GAE CCI mailbox is a set of **memory-mapped registers** on the USP MMIO BAR
- The host writes to those registers using **PCIe Config Writes or MMIO Writes**
- The GAE reads from those registers using hardware FSMs

In our simulator:
- We use TCP (MCTP over TCP) as the transport instead of PCIe register writes
- `MctpConnectionManager` receives the CCI packet over TCP
- `MctpCciExecutor` dispatches to `ProxyGfdMgmtCommand`
- The end result is identical to real hardware from the FM's perspective

This means our GAE is **functionally correct** even though the physical transport
(TCP vs register writes) is different. This is by design — the simulator models
behavior, not physical register access.

---

## PART 5: Complete Picture — One Page Summary

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    CXL 4.0 GFD + GAE: What We Have                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  CONTROL PLANE (CCI commands):                                               │
│    ✅ All 6 commissioning commands          → Switch is set up               │
│    ✅ GAE proxy commands (5809/580A/580B)   → Host can manage GFD via proxy  │
│    ✅ FabricCrawlOut (5701)                 → FM can tunnel CCI to GFD       │
│    ✅ GAE Identify + PID Access Vectors     → Host can discover GFD caps     │
│                                                                              │
│  DATA PLANE (MMIO — no CCI involved):                                        │
│    ✅ PbrSwitchRouter routes by DRT         → DPID→port forwarding works     │
│    ✅ HBR→PBR encapsulation                 → Host address → DPID mapping    │
│    ✅ CxlGfdDevice handles MRd/MWr         → BAR-0 reads/writes work        │
│    ✅ BAR-0 = 4KB GfdMmioRegisters         → Host can read/write GFD regs   │
│                                                                              │
│  WHAT DOES NOT WORK:                                                         │
│    ❌ GFD-to-host MSI-X interrupts          (no IRQ injection model)         │
│    ❌ Multicast routing to multiple GFDs   (no RGT implementation)           │
│    ❌ G-FAM shared memory from GFD          (mem_capable=0 only)             │
│    ❌ Fast IDT hardware interrupt table    (simulation limitation)           │
│    ❌ Per-PID event log                     (no ring buffer)                 │
│    ❌ Bulk vPPB snapshot                    (not needed for 1 vPPB)          │
│                                                                              │
│  FOR QEMU ENUMERATED DEVICE (IO-only GFD):                                  │
│    ✅ Zero extra commands needed            → All 13 are sufficient          │
│    ✅ OS sees GFD as PCIe device with BAR-0 → MMIO works                    │
│    ✅ HMAT latency/BW set in 0x5706 payload → NUMA topology visible          │
│                                                                              │
│  GAE IS HARDWARE INSIDE SWITCH USP:                                          │
│    ✅ We implement it as software in switch process (GaeManager)             │
│    ✅ Proxy thread model matches spec (async, thread_id, poll/cancel)        │
│    ❌ G-FAM region table not implemented   (mem_capable=0 GFD)               │
│    ❌ Fast IDT not modelled                 (hardware accelerator)            │
│    ❌ MSI-X vector table not modelled       (no host IRQ injection)          │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

*Document version: 1.0 — June 2026*
*Branch: `smbus_dual_port`*
*Spec: CXL 4.0 Rev 1.0, §7.7.13–§7.7.14*
