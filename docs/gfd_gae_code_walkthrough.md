# GFD & GAE — Complete Code Walkthrough, Integration Steps & Commissioning Workflow
### opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## Table of Contents

1. [What is GFD? What is GAE? (Plain-English)](#1-what-is-gfd-what-is-gae-plain-english)
2. [Why Do They Exist?](#2-why-do-they-exist)
3. [Architecture Map (All Components)](#3-architecture-map-all-components)
4. [GFD Deep-Dive — Device Side](#4-gfd-deep-dive--device-side)
5. [PBR Switch — Control Plane](#5-pbr-switch--control-plane)
6. [PBR Switch — Data Plane Router](#6-pbr-switch--data-plane-router)
7. [GAE Deep-Dive — Host Side Proxy](#7-gae-deep-dive--host-side-proxy)
8. [GFD Commissioning Commands Workflow (Full Reference)](#8-gfd-commissioning-commands-workflow-full-reference)
9. [Host to GFD CCI Proxy Flow (via GAE)](#9-host-to-gfd-cci-proxy-flow-via-gae)
10. [Integration Steps — How to Wire GFD + Switch + FM in Code](#10-integration-steps--how-to-wire-gfd--switch--fm-in-code)
11. [All CCI Commands: Requirement + Code](#11-all-cci-commands-requirement--code)
12. [Requirement Traceability Table](#12-requirement-traceability-table)
13. [Common Questions from Teammates](#13-common-questions-from-teammates)

---

## 1. What is GFD? What is GAE? (Plain-English)

### GFD — Generic Fabric Device

> **Think of GFD as a "network card" in the CXL world.**

A GFD is a device that plugs into a **PBR (Port-Based Routing) switch** via a DSP (Downstream Port).
Instead of being addressed by BDF (Bus:Device:Function) like a normal PCIe device, it is addressed
by a **PID — a 12-bit Port Identifier** that the Fabric Manager assigns.

**Key facts about GFD:**
- Exposes only **CXL.io** (no CXL.mem, no HDM decoder)
- Has a **4 KB BAR-0** MMIO register block for host reads/writes
- Config-space: `cache_capable=0`, `mem_capable=0` — IO-only
- CCI Identify returns `component_type=GFD (0x04)` so the FM recognises it
- Connects to the switch over **TCP** using `SwitchConnectionClient`

### GAE — Generic Access Endpoint

> **Think of GAE as the "management portal" on the HOST side of the switch.**

A GAE lives on the **Host-Edge USP** (Upstream Port) of the PBR switch. It lets a HOST:
1. Discover what GFDs are attached to the switch (Identify GAE)
2. Send CCI management commands **to** the GFD even though the GFD is hidden behind the switch
   (Proxy GFD Mgmt Cmd)

The GAE acts as an **async proxy** — it receives a wrapped CCI command from the host, forwards
it to the GFD's executor via the DSP CCI tunnel, and returns the result via a thread_id poll.

---

## 2. Why Do They Exist?

### Why GFD?

CXL 4.0 adds **PBR (Port-Based Routing)** which routes by PID instead of memory address, enabling:
- Fabric-wide addressing without depending on host memory maps
- Multi-host shared-fabric topologies
- IO-only devices that don't need memory semantics

GFD = the "endpoint" on the PBR fabric. CXL.io-only because:
- PBR routing is done by PID, not by MMIO address
- No HDM decoder needed — the switch's DRT (DPID Routing Table) does all routing

### Why GAE?

The GFD's CCI mailbox is reachable only via the switch DSP `cci_fifo`.
The host has no direct physical connection to the GFD's mailbox.
The GAE gives the host a **well-known CCI target on the USP** and internally proxies
commands to the GFD through the switch fabric.

---

## 3. Architecture Map (All Components)

```
  +-----------------------------------------------------------------------+
  |  Fabric Manager Process (FM)                                          |
  |                                                                       |
  |  MctpConnectionManager (port 8100)  <--- Switch connects here        |
  |  MctpCciApiClient                                                     |
  |    +- identify_pbr_switch()        # Step 1 of commissioning         |
  |    +- configure_pid_assignment()   # Step 2                          |
  |    +- set_drt()                    # Step 3                          |
  |    +- get_pid_binding()            # Steps 4, 6                      |
  |    +- configure_pid_binding()      # Step 5                          |
  |    +- identify_gae()              # GAE discovery                    |
  |    +- proxy_gfd_mgmt()            # GAE -> GFD proxy                 |
  |    +- get_proxy_thread_status()   # poll proxy result                |
  |    +- fabric_crawl_out()          # direct DSP tunnel to GFD         |
  |                                                                       |
  |  FabricManagerSocketIoServer (port 8200) <- CLI                      |
  |  FmSmbusMctpServer (port 8301)           <- QEMU SMBus/MCTP CCI      |
  +-----------------------------------------------------------------------+
            | MCTP/TCP (port 8100)
            v
  +-----------------------------------------------------------------------+
  |  PBR Switch                                                           |
  |                                                                       |
  |  MctpConnectionClient ---> MctpCciExecutor                           |
  |                                   |                                   |
  |                             CciExecutor                               |
  |                          (PBR + GAE + FabricCrawlOut registered)     |
  |                                   |                                   |
  |                  +----------------+----------------+                  |
  |                  |                                 |                  |
  |           PbrSwitchManager                   GaeManager              |
  |         (DRT + PIDs + bindings)           (proxy threads)            |
  |                  |                                 |                  |
  |           PbrSwitchRouter                DspCciTunnel[port N]        |
  |         (data plane TLP routing)        (cci_fifo to GFD)            |
  +-----------------------------------------------------------------------+
            | TCP (port 8000)      | DSP cci_fifo
            v                     v
  +----------------------------------+  +--------------------------------+
  |  GFD Process                     |  |                                |
  |  SwitchConnectionClient          |  |  GFD CCI (in-process)          |
  |  CxlGfdDevice                    |  |  CciExecutor                   |
  |    +- CxlIoManager (BAR-0 MMIO)  |  |    +- IdentifyCommand          |
  |    +- CxlMemManager (stub)        |  |       (component_type=0x04)   |
  |    +- CciExecutor (Identify)      |  |                                |
  |  GfdMmioRegisters (4 KB)          |  |  GfdMmioRegisters (4 KB)       |
  +----------------------------------+  +--------------------------------+
```

### File to Component Map

| Component | File | Role |
|-----------|------|------|
| `CxlGfdDevice` | [cxl_gfd_device.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/device/cxl_gfd_device.py) | GFD device (IO + CCI + BAR-0) |
| `SwitchConnectionClient` | [switch_connection_client.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/switch_connection_client.py) | TCP connect from GFD to switch |
| `PbrSwitchManager` | [pbr_switch_manager.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/pbr_switch_manager.py) | DRT + PID assignment state |
| `PbrSwitchRouter` | [pbr_switch_router.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/pbr_switch_router.py) | Data plane TLP routing via DRT |
| `GaeManager` | [gae_manager.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/gae_manager.py) | GAE proxy thread registry |
| `MctpCciExecutor` | [mctp_cci_executor.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/mctp/mctp_cci_executor.py) | Switch-side CCI dispatcher |
| `MctpCciApiClient` | [mctp_cci_api_client.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/mctp/mctp_cci_api_client.py) | FM-side typed API for all CCI commands |
| PBR CCI commands | [pbr_switch/](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/) | 6 PBR switch command handlers |
| GAE CCI commands | [gae/](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/) | 5 GAE proxy command handlers |

---

## 4. GFD Deep-Dive — Device Side

### 4.1 Startup Flow

```
SwitchConnectionClient._run()
  +- asyncio.open_connection(switch_host, 8000)
  +- Send SidebandConnectionRequestPacket(port_index=N)
  +- Receive CONNECTION_ACCEPT
  +- Create CxlPacketProcessor (splits TCP stream into per-type asyncio.Queues)
        +- mmio_fifo  ->  CxlIoManager  ->  BAR-0 reads/writes
        +- cfg_fifo   ->  Config-space probing (BAR-0 size, etc.)
        +- cci_fifo   ->  CciExecutor  ->  IdentifyCommand (type=GFD)
        +- cxl_mem_fifo -> CxlMemManager (stub, drops everything)
```

### 4.2 Config Space: `mem_capable=0`

```python
# cxl_gfd_device.py  _init_device()
capability_options = DvsecCxlCapabilityOptions(
    cache_capable=0,    # GFD has no CXL.cache
    mem_capable=0,      # GFD has no HDM decoder -- no host-managed memory
    hdm_count=0,        # zero HDM decoders
)
```

`mem_capable=0` means no MMIO window for memory ops, no HDM decoder.
All host-to-GFD traffic goes via **PID-based PBR routing**, not address-based HBR routing.
This is the key difference from SLD (`mem_capable=1`).

### 4.3 CCI Identify: `component_type=GFD (0x04)`

```python
# cxl_gfd_device.py  _init_device()
identity = IdentifyResponsePayload(
    vendor_id=EEUM_VID,
    device_id=SW_GFD_DID,
    component_type=IdentifyComponentType.GFD,   # 0x04
)
self._cci_executor.register_command(IdentifyCommand.OPCODE, IdentifyCommand(identity))
```

When the FM sends CCI Identify (opcode `0x0001`) to the GFD's DSP port,
the GFD's `CciExecutor` returns `component_type=0x04`. The FM now knows a GFD
is on that DSP port and can start the 6-step commissioning sequence.

---

## 5. PBR Switch — Control Plane

### 5.1 PID and DRT Concepts

**PID (Port Identifier):** 12-bit number (0x000–0xFFE) assigned by FM. `0xFFF` = unassigned.

**DRT (DPID Routing Table):** Flat array of 4096 entries, array index IS the DPID value.
```
DRT[0x010] = { entry_type=PHYSICAL_PORT, routing_target=1 }
  --> TLPs with DPID=0x010 route to physical port 1
```

### 5.2 MctpCciExecutor — Command Dispatch

```python
# mctp_cci_executor.py  _process_incoming_requests()
packet = await self._mctp_connection.controller_to_ep.get()
command_opcode = cci_message.cci_msg_header.command_opcode

if command_opcode in [GET_LD_INFO, GET_LD_ALLOCATIONS, SET_LD_ALLOCATIONS]:
    # MLD path -- forward to downstream MLD device via cci_fifo
    ...
else:
    # PBR (5700h-5709h) / GAE (5800h-580Bh) / FabricCrawlOut (5701h)
    request  = CciRequest(opcode=command_opcode, payload=...)
    response = await self._cci_executor.execute_command(request)
    await self._send_response(response, message_tag)
```

### 5.3 DSP CCI Tunnel — Switch to GFD Bridge

```python
# mctp_cci_executor.py  __init__()
for port_index, port_config in enumerate(port_configs):
    if port_config.type == PORT_TYPE.DSP:
        tunnel = DspCciTunnel(
            cci_fifo=cxl_conn.cci_fifo,    # DSP port's CCI queue
            port_index=port_index,
        )
        self._tunnel_registry.register(port_index, tunnel)
```

The tunnel serialises a `CciRequest`, puts it on `cci_fifo.host_to_target`, and waits for
the GFD's `CciExecutor` to process it and reply on `cci_fifo.target_to_host`.

---

## 6. PBR Switch — Data Plane Router

### 6.1 Routing Logic

```python
# pbr_switch_router.py  _route_packet()
if base_packet.is_pbr():
    dpid = pbr_packet.pbr_header.dpid

    # DRT lookup
    drt_result = self._pbr_switch_manager.get_drt(0, dpid, 1)
    drt_entry  = drt_result[0][0]

    # Validate -- drop if not PHYSICAL_PORT
    if drt_entry.entry_type != DrtEntryType.PHYSICAL_PORT:
        return   # drop silently

    # Forward to egress port
    egress_port = drt_entry.routing_target
    await self._port_fifos[egress_port].host_to_target.put(inner_packet)

else:
    # HBR packet -- need HDM decoder to get DPID, then encapsulate
    address = packet.get_address()
    dpid    = self._hdm_decoder_manager.get_dpid(address)
    pbr_pkt = PbrBasePacket.encapsulate(spid=ingress_port_id, dpid=dpid, ...)
    await self._route_packet(ingress_port_id, pbr_pkt, egress_direction)
```

---

## 7. GAE Deep-Dive — Host Side Proxy

### 7.1 GaeManager State

```python
# gae_manager.py
class GaeManager:
    _vppbs: List[GaeVppbInfo]            # G-FAM vPPBs (empty for simple GFD)
    _gfd_tunnel:   Optional[DspCciTunnel]  # production: across switch cci_fifo
    _gfd_executor: Optional[CciExecutor]   # test: direct in-process call
    _proxy_threads: Dict[int, ProxyThreadEntry]  # active proxy ops
    _next_thread_id: int
```

### 7.2 Proxy Thread Lifecycle

```
Host: ProxyGfdMgmt(gfd_opcode=X, payload=P)
         |
         v
  ProxyGfdMgmtCommand._execute()
    parse: gfd_opcode=X, gfd_payload=P
    gae_manager.start_proxy(X, P)
      alloc thread_id=1
      asyncio.create_task(_run())
        _run(): DspCciTunnel.send_and_wait(CciRequest(X, P))
                -> cci_fifo.host_to_target.put(req)
                <- cci_fifo.target_to_host.get()  [GFD replies]
        _run(): entry.completed = True
    return ProxyGfdMgmtResponse(thread_id=1)
         |
         v
Host: GetProxyThreadStatus(thread_id=1)
    gae_manager.get_proxy_status(1)
    -> {completed=True, return_code, payload}
```

---

## 8. GFD Commissioning Commands Workflow (Full Reference)

This section documents every command the FM must issue, in order, to commission a GFD.
All commands go through `MctpCciApiClient` which sends them over MCTP/TCP to the switch.

```
+---------+        MCTP/TCP         +--------+    TCP/cci_fifo    +-----+
|   FM    | ----------------------> | Switch | ----------------> | GFD |
|         | <---------------------- |        | <---------------- |     |
+---------+                         +--------+                   +-----+
```

---

### Command 1 — Identify PBR Switch (`5700h`)

**API method:** `MctpCciApiClient.identify_pbr_switch()`

**Purpose:** Learn switch capabilities. Must be called first. `num_drts` MUST be >= 1.

**Wire format:**
```
Request:  [opcode=0x5700]  payload = empty (0 bytes)

Response: [opcode=0x5700, rc=SUCCESS]
  Byte 0x00  len=8   gae_support_map  (bit N = VCS N has GAE)
  Byte 0x08  len=1   num_drts         (number of DRT tables, >= 1)
  Byte 0x09  len=1   num_rgts         (Routing Group Tables)
  Byte 0x0A  len=1   routing_caps     (random/CA/vendor bits)
  Total: 12 bytes
```

**Code flow:**
```python
# FM side: mctp_cci_api_client.py
rc, info = await api_client.identify_pbr_switch()
assert rc == CCI_RETURN_CODE.SUCCESS
assert info.num_drts >= 1
print(f"Switch has {info.num_drts} DRT table(s), GAE on VCS bitmask {info.gae_support_map:#018x}")

# Switch side: identify_pbr_switch.py  _execute()
info = self._pbr_switch_manager.get_identify_info()
# get_identify_info() sets num_drts = len(_drt_tables) dynamically
payload = IdentifyPbrSwitchResponsePayload(
    gae_support_map=info.gae_support_map,
    num_drts=info.num_drts,
    num_rgts=info.num_rgts,
).dump()
```

**Expected output:**
```
num_drts=1, num_rgts=0, gae_support_map=0x0000000000000001
```

---

### Command 2 — Configure PID Assignment (`5704h`) — ASSIGN

**API method:** `MctpCciApiClient.configure_pid_assignment(request)`

**Purpose:** Assign PID `0x010` to DSP port 1 (where GFD is connected).
Does NOT update the DRT — data plane is still dark.

**Wire format:**
```
Request:  [opcode=0x5704]
  Byte 0x00  operation=0x00  (0b000 = ASSIGN, 0b001 = CLEAR)
  Byte 0x01  reserved
  Byte 0x02-0x03  num_targets = 1
  --- PID Assignment Entry (5 bytes each, Table 7-124) ---
  Byte 0x04-0x05  pid        = 0x0010  (bits [11:0])
  Byte 0x06-0x07  target_id  = 0x0001  (DSP port 1)
  Byte 0x08       instance_id= 0x00

Response: [opcode=0x5704, rc=SUCCESS]  payload = empty
```

**Code flow:**
```python
# FM side
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentRequestPayload, PidAssignmentEntry, PidAssignmentOperation
)
request = ConfigurePidAssignmentRequestPayload(
    operation=PidAssignmentOperation.ASSIGN,
    entries=[PidAssignmentEntry(pid=0x010, target_id=1, instance_id=0)],
)
rc, _ = await api_client.configure_pid_assignment(request)
assert rc == CCI_RETURN_CODE.SUCCESS

# Switch side: configure_pid_assignment.py  _execute()
for entry in payload.entries:
    rc = self._pbr_switch_manager.assign_pid(entry.pid, entry.target_id, entry.instance_id)
    if rc != CCI_RETURN_CODE.SUCCESS:
        return CciResponse(return_code=rc)

# pbr_switch_manager.py  assign_pid()
# Guard: reject duplicate PID to different target
if pid in self._pid_assignments:
    if self._pid_assignments[pid].target_id != target_id:
        return CCI_RETURN_CODE.INVALID_INPUT   # duplicate PID, different target
self._pid_assignments[pid] = PidAssignment(pid, target_id, instance_id)
```

> **NOTE:** `assign_pid()` does NOT touch DRT. FM must call Set DRT (command 3) to enable routing.

---

### Command 3 — Set DRT (`5709h`)

**API method:** `MctpCciApiClient.set_drt(request)`

**Purpose:** Program `DRT[0][0x010] = PHYSICAL_PORT → port 1`.
After this, the data plane is live for DPID=0x010.

**Wire format:**
```
Request:  [opcode=0x5709]
  Byte 0x00       drt_index   = 0x00
  Byte 0x01       reserved
  Byte 0x02-0x03  start_entry = 0x0010   (starting DPID / array index)
  Byte 0x04-0x05  num_entries = 0x0001
  --- DRT Entry (2 bytes each, Table 7-133) ---
  Byte 0x06       entry_type    bits[1:0] = 0b01 (PHYSICAL_PORT)
                  reserved      bits[7:2] = 0
  Byte 0x07       routing_target = 0x01  (physical port 1)

Response: [opcode=0x5709, rc=SUCCESS]  payload = empty
```

**Code flow:**
```python
# FM side
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import SetDrtRequestPayload
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType

request = SetDrtRequestPayload(
    drt_index=0,
    start_entry=0x010,
    entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=1)],
)
rc, _ = await api_client.set_drt(request)
assert rc == CCI_RETURN_CODE.SUCCESS

# Switch side: set_drt.py / pbr_switch_manager.py  set_drt()
for i, entry in enumerate(entries):
    if entry.entry_type == DrtEntryType.RESERVED:
        return CCI_RETURN_CODE.INVALID_INPUT   # spec: reject RESERVED (0b11)
    table.entries[start_entry + i] = entry   # index IS the DPID
```

**After this command:** `PbrSwitchRouter` will route any TLP with DPID=0x010 to port 1 (GFD).

---

### Command 4 — Get PID Binding (`5705h`) — Verify Unbound

**API method:** `MctpCciApiClient.get_pid_binding(request)`

**Purpose:** Confirm vPPB (vcs=0, vppb=0) is unbound (pid=0xFFF) before binding.

**Wire format:**
```
Request:  [opcode=0x5705]
  Byte 0x00  vcs_id  = 0x00
  Byte 0x01  vppb_id = 0x00

Response: [opcode=0x5705, rc=SUCCESS]
  Byte 0x00-0x01  bound_pid = 0x0FFF   (0xFFF = PID_UNASSIGNED = unbound)
  Byte 0x02       reserved
  --- HMAT info (if bound) ---
```

**Code flow:**
```python
# FM side
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import GetPidBindingRequestPayload
request = GetPidBindingRequestPayload(vcs_id=0, vppb_id=0)
rc, resp = await api_client.get_pid_binding(request)
assert rc == CCI_RETURN_CODE.SUCCESS
assert resp.bound_pid == 0xFFF,  f"Expected unbound (0xFFF), got {resp.bound_pid:#05x}"

# Switch side: pbr_switch_manager.py  get_pid_binding()
binding = self._pid_bindings.get((vcs_id, vppb_id))
if binding is None:
    return PidBinding(pid=PID_UNASSIGNED)   # 0xFFF sentinel
return binding
```

---

### Command 5 — Configure PID Binding (`5706h`) — BIND

**API method:** `MctpCciApiClient.configure_pid_binding(request)`

**Purpose:** Bind vPPB (vcs=0, vppb=0) to PID `0x010`.
This is a **background command** — FM gets `BACKGROUND_COMMAND_STARTED` immediately.

**Wire format:**
```
Request:  [opcode=0x5706]
  Byte 0x00  operation = 0x00  (0b000 = BIND, 0b001 = UNBIND)
  Byte 0x01  vcs_id    = 0x00
  Byte 0x02  vppb_id   = 0x00
  Byte 0x03  reserved
  Byte 0x04-0x05  pid = 0x0010
  --- HMAT info (latency/bandwidth, optional) ---

Response: [opcode=0x5706, rc=BACKGROUND_COMMAND_STARTED, bo_flag=1]
          payload = empty
```

**Code flow:**
```python
# FM side
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingRequestPayload, PidBindingOperation
)
from opencis.cxl.component.pbr_switch_manager import HmatInfo

request = ConfigurePidBindingRequestPayload(
    operation=PidBindingOperation.BIND,
    vcs_id=0,
    vppb_id=0,
    pid=0x010,
    hmat=HmatInfo(),   # zero values = no HMAT constraints
)
rc, _ = await api_client.configure_pid_binding(request, wait_for_completion=False)
assert rc == CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED

# Switch side: configure_pid_binding.py  -- CciBackgroundCommand
# Immediately returns BACKGROUND_COMMAND_STARTED, background task runs:
rc = self._pbr_switch_manager.configure_pid_binding(BIND, vcs_id, vppb_id, pid, hmat)
# pbr_switch_manager.py:
#   self._pid_bindings[(vcs_id, vppb_id)] = PidBinding(pid=pid, hmat=hmat)
```

> **Why background?** The spec (SS7.7.13.7) mandates this. In real hardware, vPPB binding
> may involve hot-plug sequencing and ACPI HMAT updates. We model this correctly with
> `CciBackgroundCommand` even though our simulator completes instantly.

---

### Command 6 — Get PID Binding (`5705h`) — Verify Bound

**Purpose:** Confirm binding completed. Same API as command 4.

```python
# FM side
rc, resp = await api_client.get_pid_binding(GetPidBindingRequestPayload(vcs_id=0, vppb_id=0))
assert rc == CCI_RETURN_CODE.SUCCESS
assert resp.bound_pid == 0x010,  f"Expected bound (0x010), got {resp.bound_pid:#05x}"
print(f"vPPB (vcs=0, vppb=0) is now bound to PID {resp.bound_pid:#05x}")
```

---

### Optional Command — Get DRT (`5708h`) — Verify DRT Entry

**API method:** `MctpCciApiClient.get_drt(request)`

**Purpose:** Read back the DRT to verify Set DRT succeeded.

**Wire format:**
```
Request:  [opcode=0x5708]
  Byte 0x00  drt_index   = 0x00
  Byte 0x01  reserved
  Byte 0x02-0x03  start_entry = 0x0010
  Byte 0x04-0x05  num_entries = 0x0001

Response: [opcode=0x5708, rc=SUCCESS]
  Byte 0x00  rgt_associated = 0x00
  Byte 0x01-0x02  num_entries = 1
  --- DRT Entry (2 bytes) ---
  Byte 0x03  entry_type = 0x01 (PHYSICAL_PORT)
  Byte 0x04  routing_target = 0x01  (port 1)
```

**Code flow:**
```python
# FM side
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import GetDrtRequestPayload
request = GetDrtRequestPayload(drt_index=0, start_entry=0x010, num_entries=1)
rc, resp = await api_client.get_drt(request)
assert rc == CCI_RETURN_CODE.SUCCESS
entry = resp.entries[0]
assert entry.entry_type == DrtEntryType.PHYSICAL_PORT
assert entry.routing_target == 1
print(f"DRT[0][0x010] = PHYSICAL_PORT -> port {entry.routing_target}")
```

---

### Full Commissioning Sequence — One-Shot Script

```python
"""
GFD Commissioning Script — complete E2E sequence
Assumes:
  - switch is running and connected to FM
  - GFD is connected to switch DSP port 1
  - api_client = MctpCciApiClient (already connected)
"""
import asyncio
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_assignment import (
    ConfigurePidAssignmentRequestPayload, PidAssignmentEntry, PidAssignmentOperation
)
from opencis.cxl.cci.fabric_manager.pbr_switch.set_drt import SetDrtRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.get_drt import GetDrtRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.get_pid_binding import GetPidBindingRequestPayload
from opencis.cxl.cci.fabric_manager.pbr_switch.configure_pid_binding import (
    ConfigurePidBindingRequestPayload, PidBindingOperation
)
from opencis.cxl.component.pbr_switch_manager import DrtEntry, DrtEntryType, HmatInfo

GFD_PID      = 0x010   # 12-bit PID to assign to the GFD
GFD_PORT     = 1       # DSP port index the GFD is connected to
VCS_ID       = 0
VPPB_ID      = 0
DRT_INDEX    = 0


async def commission_gfd(api_client):
    print("=== Step 1: Identify PBR Switch ===")
    rc, info = await api_client.identify_pbr_switch()
    assert rc == CCI_RETURN_CODE.SUCCESS, f"Identify failed: {rc}"
    assert info.num_drts >= 1, f"Switch has no DRT tables"
    print(f"  num_drts={info.num_drts}, gae_support_map={info.gae_support_map:#018x}")

    print("=== Step 2: Assign PID 0x010 to port 1 ===")
    assign_req = ConfigurePidAssignmentRequestPayload(
        operation=PidAssignmentOperation.ASSIGN,
        entries=[PidAssignmentEntry(pid=GFD_PID, target_id=GFD_PORT, instance_id=0)],
    )
    rc, _ = await api_client.configure_pid_assignment(assign_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"ConfigurePidAssignment failed: {rc}"
    print(f"  PID {GFD_PID:#05x} assigned to port {GFD_PORT}")

    print("=== Step 3: Set DRT[0][0x010] = PHYSICAL_PORT -> port 1 ===")
    set_drt_req = SetDrtRequestPayload(
        drt_index=DRT_INDEX,
        start_entry=GFD_PID,
        entries=[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=GFD_PORT)],
    )
    rc, _ = await api_client.set_drt(set_drt_req)
    assert rc == CCI_RETURN_CODE.SUCCESS, f"SetDrt failed: {rc}"
    print(f"  DRT[{DRT_INDEX}][{GFD_PID:#05x}] -> PHYSICAL_PORT:{GFD_PORT}")

    print("=== Step 4: Verify vPPB is unbound ===")
    get_bind_req = GetPidBindingRequestPayload(vcs_id=VCS_ID, vppb_id=VPPB_ID)
    rc, resp = await api_client.get_pid_binding(get_bind_req)
    assert rc == CCI_RETURN_CODE.SUCCESS
    assert resp.bound_pid == 0xFFF, f"Expected unbound, got {resp.bound_pid:#05x}"
    print(f"  vPPB({VCS_ID},{VPPB_ID}) bound_pid={resp.bound_pid:#05x} (UNBOUND confirmed)")

    print("=== Step 5: Bind vPPB (vcs=0, vppb=0) to PID 0x010 ===")
    bind_req = ConfigurePidBindingRequestPayload(
        operation=PidBindingOperation.BIND,
        vcs_id=VCS_ID,
        vppb_id=VPPB_ID,
        pid=GFD_PID,
        hmat=HmatInfo(),
    )
    rc, _ = await api_client.configure_pid_binding(bind_req, wait_for_completion=False)
    assert rc == CCI_RETURN_CODE.BACKGROUND_COMMAND_STARTED, f"Bind unexpected rc: {rc}"
    print(f"  ConfigurePidBinding returned BACKGROUND_COMMAND_STARTED (expected)")

    print("=== Step 6: Verify vPPB is now bound ===")
    rc, resp = await api_client.get_pid_binding(get_bind_req)
    assert rc == CCI_RETURN_CODE.SUCCESS
    assert resp.bound_pid == GFD_PID, f"Expected {GFD_PID:#05x}, got {resp.bound_pid:#05x}"
    print(f"  vPPB({VCS_ID},{VPPB_ID}) bound_pid={resp.bound_pid:#05x} (BOUND)")

    print("=== Commissioning COMPLETE — data plane is live ===")
    print(f"  TLPs with DPID={GFD_PID:#05x} will route to port {GFD_PORT} (GFD)")
```

---

## 9. Host to GFD CCI Proxy Flow (via GAE)

After commissioning, the host can manage the GFD via the GAE without a direct connection:

```
+--------+    5809h     +-----+   DspCciTunnel/cci_fifo   +-----+
|  Host  | -----------> | GAE | -------------------------> | GFD |
|        |              |     |                            |     |
|        | <----------- |     | <------------------------- |     |
|        |  thread_id=1 |     |   CCI response             |     |
+--------+              +-----+                            +-----+
    |
    | 580Ah  (poll for completion)
    | --------> GAE: get_proxy_status(1)
    | <-------- {completed=True, return_code=SUCCESS, payload=<GFD response bytes>}
```

**Code flow:**
```python
# FM/host sends: Proxy GFD Mgmt Cmd (5809h) wrapping GFD Identify (0x0001)
rc, proxy_resp = await api_client.proxy_gfd_mgmt(
    gfd_opcode=0x0001,   # Identify (GFD CCI opcode)
    gfd_payload=b"",
)
assert rc == CCI_RETURN_CODE.SUCCESS
thread_id = proxy_resp.thread_id
print(f"Proxy thread started: thread_id={thread_id}")

# Poll for completion
import asyncio
while True:
    rc, status = await api_client.get_proxy_thread_status(thread_id)
    if status and status.completed:
        print(f"GFD response: rc={status.gfd_return_code}, payload={status.gfd_payload.hex()}")
        break
    await asyncio.sleep(0.01)
```

**Alternative: Fabric Crawl Out (`5701h`)** — direct DSP port tunnel, synchronous:
```python
# FM side -- tunnels directly to GFD via DSP port's cci_fifo
rc, crawl_resp = await api_client.fabric_crawl_out(
    target_port=1,           # DSP port where GFD is connected
    gfd_opcode=0x0001,       # GFD CCI opcode to run
    gfd_payload=b"",
)
assert rc == CCI_RETURN_CODE.SUCCESS
print(f"GFD Identify via FabricCrawlOut: {crawl_resp.gfd_payload.hex()}")
```

---

## 10. Integration Steps — How to Wire GFD + Switch + FM in Code

### Step 1 — Prerequisites

```bash
# Clone and install
git clone https://github.com/mnsbharadwaj/opencis-core.git
git checkout smbus_dual_port
pip install -e .

# Verify tests pass
python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py -v
```

### Step 2 — Set Up PbrSwitchManager (Switch Side)

The `PbrSwitchManager` is the central state store. Create it once and inject it into all PBR
CCI command handlers:

```python
from opencis.cxl.component.pbr_switch_manager import (
    PbrSwitchManager, PidTarget, PidTargetType
)

# Create manager with 1 DRT table, 1 GFD DSP port target
pbr_manager = PbrSwitchManager(
    num_drts=1,
    num_rgts=0,
    pid_targets=[
        PidTarget(
            target_id=1,
            target_type=PidTargetType.DOWNSTREAM_EDGE_PORT,
            instance_id=0,
            vcs_id=0,
            physical_port_id=1,
        )
    ],
    label="PbrSwitch",
)
```

### Step 3 — Register PBR CCI Commands in CciExecutor

```python
from opencis.cxl.component.cci_executor import CciExecutor
from opencis.cxl.cci.fabric_manager.pbr_switch import (
    IdentifyPbrSwitchCommand, ConfigurePidAssignmentCommand,
    GetPidBindingCommand, ConfigurePidBindingCommand,
    GetDrtCommand, SetDrtCommand,
)

cci_executor = CciExecutor(label="SwitchCCI")

pbr_commands = [
    IdentifyPbrSwitchCommand(pbr_manager),
    ConfigurePidAssignmentCommand(pbr_manager),
    GetPidBindingCommand(pbr_manager),
    ConfigurePidBindingCommand(pbr_manager),
    GetDrtCommand(pbr_manager),
    SetDrtCommand(pbr_manager),
]
for cmd in pbr_commands:
    cci_executor.register_command(cmd.OPCODE, cmd)
```

### Step 4 — Set Up GaeManager and Register GAE CCI Commands

```python
from opencis.cxl.component.gae_manager import GaeManager, GaeVppbInfo
from opencis.cxl.cci.fabric_manager.gae import (
    IdentifyGaeCommand, GetPidAccessVectorsCommand,
    ProxyGfdMgmtCommand, GetProxyThreadStatusCommand, CancelProxyThreadCommand,
)

# Empty vPPBs = simple GFD (no G-FAM)
gae_manager = GaeManager(vppbs=[], label="GAE")

gae_commands = [
    IdentifyGaeCommand(gae_manager),
    GetPidAccessVectorsCommand(gae_manager),
    ProxyGfdMgmtCommand(gae_manager),
    GetProxyThreadStatusCommand(gae_manager),
    CancelProxyThreadCommand(gae_manager),
]
for cmd in gae_commands:
    cci_executor.register_command(cmd.OPCODE, cmd)
```

### Step 5 — Set Up GFD Device

```python
import asyncio
from opencis.cxl.device.cxl_gfd_device import CxlGfdDevice
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.switch_connection_client import SwitchConnectionClient
from opencis.cxl.component.common import CXL_COMPONENT_TYPE

# In production: SwitchConnectionClient connects to switch over TCP
switch_client = SwitchConnectionClient(
    port_index=1,
    component_type=CXL_COMPONENT_TYPE.DSP,
    host="127.0.0.1",
    port=8000,
)

# CxlGfdDevice takes the CxlConnection from the client
gfd_device = CxlGfdDevice(
    transport_connection=switch_client.get_cxl_connection(),
    port_index=1,
    serial_number="0000000000000001",
    label="GFD:Port1",
)

# Run them as concurrent asyncio tasks
async def run_gfd():
    await asyncio.gather(
        switch_client.run(),
        gfd_device.run(),
    )
```

### Step 6 — Bind GAE to GFD (Test Mode: Direct Executor)

For unit tests without a real TCP switch, bind the GFD's executor directly to GaeManager:

```python
# Test/in-process mode: no TCP, no DspCciTunnel
gae_manager.set_gfd_executor(gfd_device._cci_executor)

# Production mode: bind via DspCciTunnel after GFD connects
from opencis.cxl.component.dsp_cci_tunnel import DspCciTunnel
tunnel = DspCciTunnel(cci_fifo=gfd_cxl_connection.cci_fifo, port_index=1)
gae_manager.set_gfd_tunnel(tunnel)
```

### Step 7 — Set Up FM API Client and Run Commissioning

```python
from opencis.cxl.component.mctp.mctp_connection import MctpConnection
from opencis.cxl.component.mctp.mctp_cci_api_client import MctpCciApiClient

# The MctpConnection is shared between FM and switch
mctp_connection = MctpConnection()
api_client = MctpCciApiClient(mctp_connection)

# Run commissioning
async def main():
    await asyncio.gather(
        switch_client.run(),
        gfd_device.run(),
        api_client.run(),
        commission_gfd(api_client),  # the 6-step commissioning script from section 8
    )

asyncio.run(main())
```

### Step 8 — Running the Test Suite

```bash
# PBR commissioning commands (single-port)
python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py -v

# Expected output:
# test_smbus_mctp_identify         PASSED
# test_smbus_mctp_configure_pid    PASSED
# test_smbus_mctp_get_pid_binding  PASSED
# test_smbus_mctp_configure_binding PASSED
# test_smbus_mctp_get_drt          PASSED
# test_smbus_mctp_set_drt          PASSED
# 7 passed in 0.28s

# Run with packet dump visible (no -s flag needed)
python -m pytest tests/test_smbus_mctp_server_pbr_cmds.py::test_smbus_mctp_set_drt -v
```

### Step 9 — Using SMBus/MCTP Path (QEMU Integration)

The `FmSmbusMctpServer` listens on port 8301 for QEMU's SMBus MCTP client:

```python
from opencis.cxl.component.mctp.fm_smbus_mctp_server import FmSmbusMctpServer

# Wire it to the same MctpCciApiClient as the FM CLI
server = FmSmbusMctpServer(
    host="0.0.0.0",
    port=8301,
    mctp_client=api_client,   # forwarded to FM CLI path
    fm_i2c_addr=0x10,
)
await server.run()
# QEMU connects and sends SMBus MCTP frames
# Server depacketizes, calls api_client.send_raw_cci(), repacketizes response
```

---

## 11. All CCI Commands: Requirement + Code

### PBR Switch Commands

| Opcode | Name | Spec | Key Requirement | Code |
|--------|------|------|-----------------|------|
| `5700h` | Identify PBR Switch | SS7.7.13.1 | `num_drts>=1`; derived from `len(_drt_tables)` | [identify_pbr_switch.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/identify_pbr_switch.py) |
| `5704h` | Configure PID Assignment | SS7.7.13.5 | Reject duplicate PID to different target; NOT update DRT | [configure_pid_assignment.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_assignment.py) |
| `5705h` | Get PID Binding | SS7.7.13.6 | Returns 0xFFF for unbound vPPB | [get_pid_binding.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/get_pid_binding.py) |
| `5706h` | Configure PID Binding | SS7.7.13.7 | Background command; BIND/UNBIND vPPB to PID | [configure_pid_binding.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_binding.py) |
| `5708h` | Get DRT | SS7.7.13.9 | Read DRT; array index IS the DPID | [get_drt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/get_drt.py) |
| `5709h` | Set DRT | SS7.7.13.9 | Write DRT; reject RESERVED entry_type (0b11) | [set_drt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/pbr_switch/set_drt.py) |

### GAE Commands

| Opcode | Name | Spec | Key Requirement | Code |
|--------|------|------|-----------------|------|
| `5800h` | Identify GAE | SS7.7.14.1 | Reports G-FAM vPPBs (0 for simple GFD) | [identify_gae.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/identify_gae.py) |
| `5802h` | Get PID Access Vectors | SS7.7.14.3 | Bitmask of accessible PIDs per vPPB | [get_pid_access_vectors.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/get_pid_access_vectors.py) |
| `5809h` | Proxy GFD Mgmt Cmd | SS7.7.14.10 | Async-forwards CCI to GFD; returns thread_id | [proxy_gfd_mgmt.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/proxy_gfd_mgmt.py) |
| `580Ah` | Get Proxy Thread Status | SS7.7.14.11 | Returns completion flag + GFD response payload | [get_proxy_thread_status.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/get_proxy_thread_status.py) |
| `580Bh` | Cancel Proxy Thread | SS7.7.14.12 | Cancels asyncio.Task by thread_id | [cancel_proxy_thread.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/cancel_proxy_thread.py) |

---

## 12. Requirement Traceability Table

| Req ID | Spec Ref | Requirement | Implementation | Status |
|--------|----------|-------------|----------------|--------|
| GFD-REQ-001 | SS7.7.13.1 | Switch MUST report `num_drts >= 1` | `get_identify_info()` sets `num_drts=len(_drt_tables)` | Done |
| GFD-REQ-002 | SS7.7.13.5 | FM MUST assign PID before programming DRT | `assign_pid()` does NOT touch DRT | Done |
| GFD-REQ-003 | SS7.7.13.5 | Reject duplicate PID to different target | Guard in `assign_pid()` | Done |
| GFD-REQ-004 | SS7.7.13.9 | DRT entries MUST NOT use RESERVED type | `set_drt()` returns INVALID_INPUT if RESERVED | Done |
| GFD-REQ-005 | SS7.7.13.6 | GetPidBinding MUST return 0xFFF for unbound | Returns `PidBinding(pid=0xFFF)` when not in map | Done |
| GFD-REQ-006 | SS7.7.13.7 | ConfigurePidBinding is a background command | Implemented as `CciBackgroundCommand` | Done |
| GFD-REQ-007 | SS7.7.13.9 | Router MUST use DRT for egress port selection | `_route_packet()` calls `get_drt(0, dpid, 1)` | Done |
| GFD-REQ-008 | SS7.7.13 | `num_drts` in Identify MUST match actual count | `len(self._drt_tables)` computed dynamically | Done |
| GFD-REQ-009 | SS7.7.13 | CCI over MCTP/TCP transport | `MctpCciExecutor` + `FmSmbusMctpServer` | Done |
| GFD-REQ-010 | SS7.7.14 | GAE MUST proxy CCI to GFD on host's behalf | `ProxyGfdMgmtCommand` + `GaeManager.start_proxy()` | Done |
| GFD-REQ-011 | SS7.7.14.10 | Proxy MUST be async; host polls | `start_proxy()` returns thread_id; host uses 580Ah | Done |
| GFD-REQ-012 | SS7.7.14.12 | Host MUST cancel proxy thread | `cancel_proxy()` calls `entry.task.cancel()` | Done |
| GFD-REQ-013 | SS7.7.13 | GFD config-space: `mem_capable=0` | `DvsecCxlCapabilityOptions(mem_capable=0)` | Done |
| GFD-REQ-014 | SS7.7.13 | GFD CCI Identify returns `component_type=0x04` | `IdentifyResponsePayload(component_type=GFD)` | Done |
| GFD-REQ-015 | SS7.7.13 | Router drops packets not in DRT | `_route_packet()` returns early if INVALID | Done |
| GFD-REQ-016 | SS7.7.14.1 | Identify GAE returns G-FAM vPPB count | `IdentifyGaeCommand` reads `gae_manager.get_vppbs()` | Done |
| GFD-REQ-017 | SS7.7.13 | PBR router encapsulates HBR with SPID/DPID | `PbrBasePacket.encapsulate()` in HBR branch | Done |

---

## 13. Common Questions from Teammates

### Q: What is the difference between GFD and SLD?

| Property | SLD | GFD |
|----------|-----|-----|
| Routing method | HBR (address-based) | PBR (PID-based, 12-bit) |
| `mem_capable` | 1 | 0 |
| `cache_capable` | 1 | 0 |
| HDM decoder | Yes | No |
| CXL.mem | Yes | No |
| CXL.io BAR | Optional | BAR-0 (4 KB) mandatory |
| CCI `component_type` | 0x01 or 0x02 | 0x04 (GFD) |
| Addressed by | BDF + MMIO address | PID (FM-assigned 12-bit) |

### Q: Why is ConfigurePidBinding a background command?

The spec (SS7.7.13.7) mandates it. In physical hardware, vPPB binding involves
hot-plug sequencing and ACPI HMAT table updates which can take hundreds of milliseconds.
We model this correctly — the switch returns `BACKGROUND_COMMAND_STARTED` immediately
while the actual state change happens asynchronously.

### Q: Does Set DRT need to be called after Configure PID Assignment?

**Yes, always.** `assign_pid()` only updates `_pid_assignments[]` (a lookup table).
The `PbrSwitchRouter` uses only the DRT (`_drt_tables`) for routing. Until Set DRT
is called, the router has no knowledge of the PID-to-port mapping.

The commissioning sequence is always: Identify → Assign PID → **Set DRT** → Get Binding
→ Bind vPPB → Verify Bound.

### Q: What is `gae_support_map` in Identify PBR Switch?

A 64-bit bitmask where bit N = 1 means VCS N has a GAE.
- `0x0000000000000001` = VCS 0 has a GAE
- `0x0000000000000003` = VCS 0 and VCS 1 have GAEs

The host reads this to find which USP VCSs expose a GAE for proxied GFD management.

### Q: If Set DRT is done but Configure PID Binding is not, does routing work?

**Yes, for data plane.** DRT controls TLP routing independently of PID bindings.
After Set DRT, `PbrSwitchRouter` routes TLPs with DPID=0x010 to port 1.
Configure PID Binding is for vPPB-to-PID stitching (ACPI topology, HMAT).
It does not affect TLP forwarding.

### Q: What is the difference between GAE Proxy and Fabric Crawl Out?

| Feature | GAE Proxy (`5809h`) | Fabric Crawl Out (`5701h`) |
|---------|--------------------|-----------------------------|
| Initiator | Host via GAE on USP | FM directly |
| Mechanism | Async proxy thread | Synchronous tunnel |
| Result delivery | Poll with `580Ah` | Inline in response |
| Spec section | SS7.7.14.10 | SS7.7.13.2 |
| API method | `proxy_gfd_mgmt()` | `fabric_crawl_out()` |
| Use case | Host-initiated mgmt | FM diagnostic/commissioning |

### Q: How does pytest show packet dump output without `-s`?

Two changes were made:
1. All packet-printing calls use `_p()` which writes to `sys.__stdout__`
2. `pyproject.toml` sets `addopts = "--capture=sys"` so `sys.__stdout__` bypasses
   pytest's stdout capture (capture=sys only replaces `sys.stdout`, not the original fd)

---

*Document version: 2.0 — June 2026*
*Branch: opencis-core `smbus_dual_port`*
*Spec reference: CXL Specification Revision 4.0 Version 1.0, Section 7.7.13-7.7.14*
