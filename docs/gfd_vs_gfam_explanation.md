# GFD vs G-FAM — What Is the Difference?
### opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## One-Line Answer

> **GFD = IO-only fabric device (BAR-0 MMIO registers only)**
> **G-FAM = fabric-attached shared memory pool (CXL.mem, multi-host capable)**

They both attach to a PBR switch via a DSP port and are both addressed by PID.
The difference is what they **expose to the host** — control registers vs shared memory.

---

## PART 1: Analogy First

Think of a PCIe add-in card:

| Analogy | GFD | G-FAM GFD |
|---------|-----|-----------|
| Like a **NIC or GPU** | Has registers (BAR-0) you read/write to control it | + Has a large frame buffer or DMA memory (BAR-1 or CXL.mem) |
| What host sees | 4 KB control register window | 4 KB control registers + GBs of shared memory |
| CXL protocol used | CXL.io only | CXL.io + CXL.mem |
| Memory accessible by | One host (BAR-0 is per-host) | **Multiple hosts simultaneously** (that's the point) |
| Use case | IO accelerator, sensor, controller | Memory server, pooled DRAM, persistent memory fabric |

---

## PART 2: Technical Difference Side by Side

| Property | GFD (our current impl) | G-FAM GFD (not yet implemented) |
|----------|----------------------|--------------------------------|
| **Spec Section** | CXL 4.0 §7.7.13 | CXL 4.0 §7.7.14 (GAE) + §7.7.13 |
| **CXL protocols** | CXL.io only | CXL.io + **CXL.mem** |
| **`mem_capable`** | `0` (no memory) | `1` (has HDM decoder) |
| **`cache_capable`** | `0` | `0` or `1` |
| **HDM decoder** | None | Required (maps addresses → memory) |
| **BAR-0** | 4 KB MMIO registers | 4 KB MMIO registers |
| **Memory window** | None | GBs of fabric-attached DRAM |
| **Accessible by** | One host at a time | **Multiple hosts simultaneously** |
| **PID routing** | DPID → port (CXL.io TLPs) | DPID → port (CXL.io + CXL.mem TLPs) |
| **GAE role** | Proxy CCI commands to GFD | Expose G-FAM regions to host, manage shared access |
| **`Identify GAE` response** | `num_vppbs_with_gm_support = 0` | `num_vppbs_with_gm_support > 0` |
| **vPPB G-FAM flag** | `global_memory_support = False` | `global_memory_support = True` |
| **ACPI tables needed** | HMAT (basic latency/BW) | HMAT + SRAT G-FAM entries + CEDT |
| **OS driver** | PCIe endpoint driver (reads BAR-0) | CXL memory driver (`cxl_mem.ko`) |

---

## PART 3: What "G-FAM" Actually Means

**G-FAM = Global Fabric Attach Memory**

"Global" = accessible from multiple hosts across the CXL fabric
"Fabric Attach" = attached via a PBR switch (not directly to one host's root complex)
"Memory" = it is DRAM (or persistent memory) exposed as a CXL.mem range

### The G-FAM Use Case (Why It Exists)

```
Traditional server memory:
  Host A → [Local DRAM]     Host B → [Local DRAM]
  Each host owns its own memory. No sharing.

G-FAM topology:
  Host A ─┐
           ├─ PBR Switch ─── G-FAM GFD ─── [Shared DRAM pool, e.g. 512 GB]
  Host B ─┘

Both Host A and Host B can access the SAME DRAM pool.
The pool is fabric-attached (not directly connected to either host's memory bus).
```

This enables:
- **Memory pooling**: Overcommit physical memory, give hosts only what they need
- **Memory tiering**: Hot data in local DRAM, cold data in G-FAM
- **Disaggregated memory**: Memory blades separate from compute blades
- **Fault tolerance**: Memory failover between hosts

---

## PART 4: How GFD Works (Our Implementation — Today)

```
Host                   PBR Switch              GFD Process
 │                          │                       │
 │  MRd64 [BAR-0 addr]      │                       │
 │─────────────────────────>│                       │
 │  (CXL.io TLP)            │                       │
 │                          │  DRT lookup:          │
 │                          │  DPID=0x010→port 1    │
 │                          │                       │
 │                          │  Forward MRd64        │
 │                          │──────────────────────>│
 │                          │                       │ CxlIoManager
 │                          │                       │ reads GfdMmioRegisters
 │                          │                       │ [offset = addr & 0xFFF]
 │                          │                       │
 │                          │  CplD [4 bytes]       │
 │                          │<──────────────────────│
 │                          │                       │
 │  CplD [4 bytes]          │                       │
 │<─────────────────────────│                       │
 │                          │                       │
 ✓ Host reads 4 bytes from GFD BAR-0 register
```

**GFD is IO-only. The GfdMmioRegisters is a simple 4 KB register file.**
No DRAM backing, no HDM decoder, no CXL.mem protocol involved at all.

```python
# cxl_gfd_device.py
capability_options = DvsecCxlCapabilityOptions(
    cache_capable=0,   # no CXL.cache
    mem_capable=0,     # NO MEMORY -- io only
    hdm_count=0,       # no HDM decoders
)
```

---

## PART 5: How G-FAM Would Work (What Needs to Change)

```
Host A                 PBR Switch              G-FAM GFD Process
 │                          │                       │
 │  MRd64 [G-FAM addr]      │                       │
 │─────────────────────────>│                       │
 │  (CXL.mem TLP             │                       │
 │   OR CXL.io to HDM BAR)  │                       │
 │                          │  DRT lookup:          │
 │                          │  DPID=0x010→port 1    │
 │                          │                       │
 │                          │  Forward CXL.mem TLP  │
 │                          │──────────────────────>│
 │                          │                       │ HDM Decoder
 │                          │                       │ resolves address
 │                          │                       │ → DRAM offset
 │                          │                       │
 │                          │                       │ [512 GB DRAM pool]
 │                          │                       │ reads 64 bytes
 │                          │                       │
 │                          │  CXL.mem Response     │
 │                          │<──────────────────────│
 │                          │                       │
 │  Data [64 bytes]         │                       │
 │<─────────────────────────│                       │
 │                          │                       │
 ✓ Host A reads 64 bytes from shared DRAM pool
 ✓ Host B can simultaneously read/write the SAME pool
```

---

## PART 6: What Needs to Change in Code for G-FAM

### Change 1 — `CxlGfdDevice`: Enable `mem_capable=1`

```python
# CURRENT (IO-only GFD):
capability_options = DvsecCxlCapabilityOptions(
    cache_capable=0,
    mem_capable=0,    # ← THIS
    hdm_count=0,
)

# G-FAM GFD needs:
capability_options = DvsecCxlCapabilityOptions(
    cache_capable=0,
    mem_capable=1,     # ← expose CXL.mem
    hdm_count=1,       # ← one HDM decoder covering the full DRAM range
)
```

### Change 2 — Add HDM Decoder to G-FAM GFD

```python
# G-FAM GFD needs an HDM decoder that maps:
#   [host_base_addr, host_base_addr + gfam_size) → internal DRAM backing
hdm_decoder = HdmDecoder(
    base=gfam_host_base,
    size=gfam_size_gb * 1024 * 1024 * 1024,
    ig=IG_256MB,
    iw=IW_1,
    target_port=0,   # the GFD itself (endpoint decoder)
)
```

### Change 3 — Add Backing DRAM File

```python
# CURRENT: empty stub
_stub_mem_component = CxlMemoryDeviceComponent(
    identity,
    decoder_count=HDM_DECODER_COUNT.DECODER_1,
    memory_file="",     # ← empty string = no backing
    label=label,
)

# G-FAM GFD needs a real backing file (like an SLD):
_gfam_mem_component = CxlMemoryDeviceComponent(
    identity,
    decoder_count=HDM_DECODER_COUNT.DECODER_1,
    memory_file="/path/to/gfam_512gb.bin",   # ← real shared DRAM file
    label=label,
)
```

### Change 4 — CxlMemManager Must Process (Not Drop)

```python
# CURRENT (stub drops all CXL.mem packets):
self._cxl_mem_manager = CxlMemManager(
    upstream_fifo=transport_connection.cxl_mem_fifo,
    label=label,   # ← drops everything (no backing)
)

# G-FAM GFD needs CxlMemManager wired to the DRAM backing:
self._cxl_mem_manager = CxlMemManager(
    upstream_fifo=transport_connection.cxl_mem_fifo,
    memory_device_component=_gfam_mem_component,  # ← real DRAM
    label=label,
)
```

### Change 5 — PbrSwitchRouter Must Route CXL.mem TLPs

```python
# CURRENT: router handles CXL.io only
# _route_packet() extracts address only from CxlIoMemReqPacket

# G-FAM needs: also handle CXL.mem packets
elif base_packet.is_cxl_mem():
    cxl_mem_packet = cast(CxlMemBasePacket, packet)
    address = cxl_mem_packet.get_address()
    dpid = self._hdm_decoder_manager.get_dpid(address)
    # then encapsulate as PBR and route to G-FAM GFD port
```

### Change 6 — GaeManager Needs G-FAM Region Table

```python
# New data structure in GaeManager:
@dataclass
class GFamRegion:
    region_id: int
    base_addr: int            # host-visible base address
    size: int                  # in bytes
    access_flags: int          # READ | WRITE | EXECUTE
    vppb_id: int              # which vPPB owns this region

class GaeManager:
    def __init__(self, ...):
        self._gfam_regions: List[GFamRegion] = []  # NEW

# New CCI commands needed:
#   0x580C GetGFamRegionInfo  → read self._gfam_regions
#   0x580D SetGFamRegionConfig → configure region base/size/access
#   0x580E GetGFamExtentList  → dynamic capacity (like NVMe DCD)
```

### Change 7 — GaeVppbInfo Must Set `global_memory_support=True`

```python
# CURRENT (simple GFD): empty list
gae_manager = GaeManager(vppbs=[])

# G-FAM: one vPPB entry with G-FAM flag
gae_manager = GaeManager(vppbs=[
    GaeVppbInfo(
        vppb_id=0,
        global_memory_support=True,   # ← tells host this vPPB has G-FAM
        pid=0x010,
    )
])

# Now Identify GAE (0x5800) returns:
# {
#   "numVppbsWithGlobalMemory": 1,
#   "vppbEntries": [{"vppbId": 0, "globalMemorySupport": true}]
# }
```

### Change 8 — ACPI Tables

For the host OS to use G-FAM memory, ACPI must advertise it:

| ACPI Table | Purpose | Change Needed |
|-----------|---------|---------------|
| SRAT | Describes NUMA topology; G-FAM as a new NUMA node | Add G-FAM memory affinity structure |
| HMAT | Latency/BW for each NUMA→NUMA path | Add G-FAM target entries |
| CEDT | CXL Early Discovery Table; lists CXL memory ranges | Add G-FAM range entry |

---

## PART 7: Identify GAE — Today vs G-FAM

### Today (IO-only GFD):
```python
# gae_manager._vppbs = []   (empty list)
# IdentifyGaeCommand._execute() returns:
{
    "num_vppbs_with_gm_support": 0,
    "vppb_entries": []
}
```
This tells the host: *"This switch has a GAE, but no G-FAM memory is available."*
The host can still use the GAE proxy to send CCI commands to the GFD.

### G-FAM GFD (future):
```python
# gae_manager._vppbs = [GaeVppbInfo(vppb_id=0, global_memory_support=True, pid=0x010)]
# IdentifyGaeCommand._execute() returns:
{
    "num_vppbs_with_gm_support": 1,
    "vppb_entries": [
        {"vppb_id": 0, "global_memory_support": True}
    ]
}
```
This tells the host: *"vPPB 0 has G-FAM memory. Use GetGFamRegionInfo (0x580C) to find
the base address and size."*

---

## PART 8: The Three G-FAM Commands Not Yet Implemented

### `0x580C` — Get G-FAM Region Info

**What it does:** Returns the list of G-FAM memory regions exposed by this vPPB.
Each region has a base address, size, and access flags.

**What host does with it:**
1. Calls `GetGFamRegionInfo(vppb_id=0)`
2. Gets back: `[{base=0x0800_0000_0000, size=512GB, access=RW}]`
3. Programs its page tables to map that address range
4. Sends CXL.mem TLPs to 0x0800_0000_0000 which route through the switch to the GFD's DRAM

### `0x580D` — Set G-FAM Region Config

**What it does:** FM configures the size, base address, and access permissions
of a G-FAM region. The host calls this to "carve out" a portion of the G-FAM pool.

**Example workflow:**
```
G-FAM GFD has 512 GB DRAM
Host A gets 256 GB: SetGFamRegionConfig(vppb=0, base=0x800_0000_0000, size=256GB, host=A)
Host B gets 256 GB: SetGFamRegionConfig(vppb=0, base=0x840_0000_0000, size=256GB, host=B)
```

### `0x580E` — Get G-FAM Extent List

**What it does:** Returns the list of Dynamic Capacity (DC) extents available in
the G-FAM pool. Works like NVMe Zoned Namespace or CXL Dynamic Capacity Devices —
the host can request extents (chunks of memory) and release them back to the pool.

**This enables true memory pooling:**
- Start: all 512 GB unallocated in pool
- Host A needs 64 GB: requests 64 GB extents
- Host A releases them later: returns to pool
- Host B requests 128 GB: gets them from pool

---

## PART 9: Summary Table

```
                    GFD (Today)              G-FAM GFD (Future)
                    ─────────────────────    ────────────────────────────────
What is it?         IO accelerator /         Shared memory pool /
                    control-plane device     disaggregated memory server

CXL protocols       CXL.io only              CXL.io + CXL.mem

mem_capable         0 (no)                   1 (yes)

BAR-0               4 KB register file       4 KB register file (same)

Memory              None                     GBs of DRAM (shared)

Multi-host?         No (BAR-0 is             YES (DRAM pool accessible
                    per-host mapped)         from multiple hosts)

GAE Identify        numVppbs = 0             numVppbs > 0

New CCI commands    None needed              0x580C, 0x580D, 0x580E

Code changes        None                     8 major changes (mem_capable,
                                             HDM decoder, backing file,
                                             CxlMemManager, router,
                                             GaeManager, vPPB flags,
                                             ACPI tables)

Commissioning       Same 6-step workflow     Same 6-step + G-FAM region setup

QEMU boot           Works with BAR-0 only    Needs CXL memory hotplug support

OS driver           Simple PCIe driver       cxl_mem.ko + NUMA integration
```

---

*Document version: 1.0 — June 2026*
*Branch: `smbus_dual_port`*
*Spec: CXL 4.0 Rev 1.0 §7.7.13 (GFD) and §7.7.14 (GAE/G-FAM)*
