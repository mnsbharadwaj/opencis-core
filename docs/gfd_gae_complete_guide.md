# GFD / GAE Integration Guide — OpenCIS Core

> **CXL 4.0 Specification Rev 4.0 Version 1.0**
> §7.7.13 (PBR Switch / GFD) · §7.7.14 (GAE)

> **Scope**: This is the single, definitive integration document for the
> Generic Fabric Device (GFD) and Global Access Endpoint (GAE) subsystems
> within the OpenCIS emulator. It covers architecture, every source file,
> complete code-flow walkthroughs, CCI command reference, data structures,
> test coverage, a step-by-step guide for adding new features, and a
> troubleshooting FAQ.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Deep-Dive](#2-architecture-deep-dive)
3. [File Index (All Source Files)](#3-file-index)
4. [End-to-End Code Flows](#4-end-to-end-code-flows)
5. [Component Deep-Dives](#5-component-deep-dives)
6. [CCI Command Reference](#6-cci-command-reference)
7. [Data Structures & Wire Formats](#7-data-structures--wire-formats)
8. [Test Coverage Map](#8-test-coverage-map)
9. [Adding a New Feature (Step-by-Step)](#9-adding-a-new-feature)
10. [Troubleshooting & FAQ](#10-troubleshooting--faq)

---

## 1. System Overview

### 1.1 What is a GFD?

A **Generic Fabric Device** (GFD) is a CXL fabric device that attaches to a
PBR (Port-Based Routing) switch DSP (Downstream Switch Port). Unlike
traditional CXL Type-1/2/3 devices, a GFD:

- Has **no BAR** visible to the host.
- Has **no PCIe config space** enumerable by the host.
- Communicates **exclusively via CCI** (Component Command Interface) over
  the `cci_fifo` channel.
- Is managed by the **Fabric Manager (FM)** or the **Host** through the GAE.

A GFD is therefore a pure management-plane entity. It responds to CCI
commands (Identify, vendor-specific, etc.) but never participates in PCIe
enumeration or memory-mapped I/O.

### 1.2 What is the GAE?

The **Global Access Endpoint** (GAE) resides on the **USP (Upstream Switch
Port)** of the PBR switch — NOT on the GFD. It is the host's entry point
for sending CCI commands to fabric devices it cannot directly reach.

The GAE provides:

- **Proxy GFD Management** (opcode `0x5809`): Forwards CCI commands to a GFD
  attached on a DSP port.
- **Proxy Thread Status** (opcode `0x580A`): Polls completion of forwarded commands.
- **Cancel Proxy Thread** (opcode `0x580B`): Cancels in-flight forwarded commands.
- **Identify GAE** (opcode `0x5800`): Reports GAE capabilities (vPPB/G-FAM info).
- **Get PID Access Vectors** (opcode `0x5802`): Returns GMV/VTV bitmasks per PID.

### 1.3 Key Architectural Principle: GAE is on the Switch USP, NOT on the GFD

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CXL FABRIC                                   │
│                                                                      │
│  ┌──────────┐     TCP :8000     ┌──────────────────────────────┐     │
│  │          │◄────────────────►│         PBR SWITCH            │     │
│  │   HOST   │  cci_fifo (USP)  │                              │     │
│  │          │  CCI over TCP    │  ┌──────────┐  ┌──────────┐  │     │
│  │ CxlSimple│                  │  │   GAE    │  │  PBR Sw  │  │     │
│  │   Host   │                  │  │ Manager  │  │ Manager  │  │     │
│  │          │                  │  └────┬─────┘  └──────────┘  │     │
│  └──────────┘                  │       │                      │     │
│                                │       │ DspCciTunnel         │     │
│                                │       ▼                      │     │
│                                │  ┌──────────┐               │     │
│                                │  │  DSP     │               │     │
│                                │  │ cci_fifo │               │     │
│                                │  └────┬─────┘               │     │
│                                └───────┼──────────────────────┘     │
│                                        │ TCP :8000                   │
│                                        ▼                             │
│                                ┌──────────────┐                      │
│                                │     GFD      │                      │
│                                │  CCI Mailbox │                      │
│                                │  (no BAR)    │                      │
│                                └──────────────┘                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.4 Two Paths to the GFD

There are exactly two paths through which CCI commands reach the GFD:

| Path | Opcode | Route |
|------|--------|-------|
| **FM → GFD** (Fabric Crawl Out) | `0x5701` | FM → MCTP :8100 → MctpCciExecutor → FabricCrawlOutCommand → DspCciTunnel → cci_fifo → GFD |
| **Host → GFD** (GAE Proxy) | `0x5809` | Host → TCP :8000 → GaeCciMailbox → ProxyGfdMgmtCommand → GaeManager → DspCciTunnel → cci_fifo → GFD |

Both paths converge at the **DspCciTunnel**, which puts a CciRequest onto
`cci_fifo.host_to_target` and reads the GFD's response from
`cci_fifo.target_to_host`.

---

## 2. Architecture Deep-Dive

### 2.1 Component Ownership

| Component | Lives On | Owner | Created In |
|-----------|----------|-------|------------|
| `CxlGfdDevice` | GFD process | `GenericFabricDevice` | `generic_fabric_device.py` |
| `GaeManager` | Switch process | `CxlSwitch` | `cxl_switch.py` L157–159 |
| `GaeCciMailbox` | Switch process | `MctpCciExecutor` | `mctp_cci_executor.py` |
| `PbrSwitchManager` | Switch process | `CxlSwitch` | `cxl_switch.py` L151 |
| `PbrSwitchRouter` | Switch process | `CxlSwitch` | `cxl_switch.py` L187–193 |
| `DspCciTunnel` | Switch process | `MctpCciExecutor` | `mctp_cci_executor.py` |
| `CxlRootPortDevice` | Host process | `CxlSimpleHost` | `cxl_simple_host.py` L69–73 |
| `CxlPacketProcessor` | Both sides | `SwitchConnectionManager` | `switch_connection_manager.py` |

### 2.2 GFD Lifecycle

```
1. GenericFabricDevice.__init__()
   └─ SwitchConnectionClient(port_index, CXL_COMPONENT_TYPE.D2)  # TCP to switch
   └─ CxlGfdDevice(transport_connection=cxl_connection)

2. GenericFabricDevice._run()
   └─ CxlGfdDevice.run()
      ├─ CciExecutor.run()           # command dispatcher
      └─ _run_cci_mailbox()          # infinite loop: read cci_fifo → dispatch → respond

3. GenericFabricDevice._stop()
   └─ CxlGfdDevice.stop()
      ├─ cci_fifo.host_to_target.put(None)  # sentinel breaks mailbox loop
      └─ CciExecutor.stop()
```

### 2.3 GAE Lifecycle

```
1. CxlSwitch.__init__()  (when enable_pbr=True)
   ├─ PbrSwitchManager()
   ├─ GaeManager(vppbs=[], label="Switch0:GAE")
   ├─ PbrSwitchRouter(...)
   └─ _initialize_mctp_endpoint()
      ├─ Register PBR commands (0x5700–0x5709)
      ├─ Register GAE commands (0x5800–0x580B)
      └─ Register port event handler  →  binds/unbinds DspCciTunnel to GaeManager

2. GaeCciMailbox (created by MctpCciExecutor when usp_connection is provided)
   └─ _run_mailbox(): reads from USP cci_fifo.host_to_target → CciExecutor → response

3. DSP port connects (GFD joins)
   └─ handle_port_event(connected=True)
      └─ gae_manager.set_gfd_tunnel(tunnel)   # enables proxy forwarding

4. DSP port disconnects (GFD leaves)
   └─ handle_port_event(connected=False)
      └─ gae_manager.set_gfd_tunnel(None)     # disables proxy forwarding
```

### 2.4 GFD Binding Modes in GaeManager

`GaeManager` supports two mutually exclusive GFD binding modes:

| Mode | Method | When Used | How Proxy Runs |
|------|--------|-----------|----------------|
| **Tunnel** (production) | `set_gfd_tunnel(tunnel)` | Switch connects to real GFD via TCP | `tunnel.send_and_wait(req)` over cci_fifo |
| **Executor** (unit test) | `set_gfd_executor(executor)` | Tests inject CciExecutor directly | `executor.execute_command(req)` in-process |

Tunnel mode always takes precedence. When `set_gfd_tunnel()` is called, any
previously set executor is cleared (`self._gfd_executor = None`).

---

## 3. File Index

### 3.1 Application Entry Points

| File | Lines | Purpose |
|------|-------|---------|
| `opencis/apps/generic_fabric_device.py` | 151 | GFD application wrapper. Creates `SwitchConnectionClient` + `CxlGfdDevice`. |
| `opencis/apps/cxl_simple_host.py` | 250 | Host application. Contains `gae_proxy_gfd_mgmt()`, `gae_get_proxy_status()`, `gae_cancel_proxy()`. |
| `opencis/apps/cxl_switch.py` | 340 | PBR switch application. Creates `PbrSwitchManager`, `GaeManager`, `PbrSwitchRouter`. Registers all PBR+GAE CCI commands. |

### 3.2 Core Device / Component Files

| File | Lines | Purpose |
|------|-------|---------|
| `opencis/cxl/device/cxl_gfd_device.py` | 257 | `CxlGfdDevice`: CCI-only device. `_run_cci_mailbox()` loop. No BAR/PCIe. |
| `opencis/cxl/component/gae_cci_mailbox.py` | 196 | `GaeCciMailbox`: Host→GAE CCI mailbox on switch USP. Reads from USP cci_fifo. |
| `opencis/cxl/component/gae_manager.py` | 331 | `GaeManager`: GAE state owner. Proxy thread registry. GFD binding (tunnel/executor). |
| `opencis/cxl/component/pbr_switch_manager.py` | 420 | `PbrSwitchManager`: DRT tables, PID assignments, PID bindings, switch identity info. |
| `opencis/cxl/component/pbr_switch_router.py` | — | `PbrSwitchRouter`: Data-plane packet router. Uses DRT to route by DPID. |
| `opencis/cxl/device/root_port_device.py` | — | `CxlRootPortDevice`: Host-side root port. `gae_command()` sends CCI over cci_fifo. |
| `opencis/cxl/component/cxl_packet_processor.py` | — | `CxlPacketProcessor`: TCP serialization/deserialization. R-type and USP-type CCI transport. |

### 3.3 PBR Switch CCI Commands (`opencis/cxl/cci/fabric_manager/pbr_switch/`)

| File | Opcode | Command | Type |
|------|--------|---------|------|
| `identify_pbr_switch.py` | `0x5700` | Identify PBR Switch | Foreground |
| `configure_pid_assignment.py` | `0x5704` | Configure PID Assignment | Foreground |
| `get_pid_binding.py` | `0x5705` | Get PID Binding | Foreground |
| `configure_pid_binding.py` | `0x5706` | Configure PID Binding | **Background** |
| `get_drt.py` | `0x5708` | Get DRT | Foreground |
| `set_drt.py` | `0x5709` | Set DRT | Foreground |
| `__init__.py` | — | Package re-exports | — |

### 3.4 GAE CCI Commands (`opencis/cxl/cci/fabric_manager/gae/`)

| File | Opcode | Command | Type |
|------|--------|---------|------|
| `identify_gae.py` | `0x5800` | Identify GAE | Foreground |
| `get_pid_access_vectors.py` | `0x5802` | Get PID Access Vectors | Foreground |
| `proxy_gfd_mgmt.py` | `0x5809` | Proxy GFD Management Cmd | Foreground |
| `get_proxy_thread_status.py` | `0x580A` | Get Proxy Thread Status | Foreground |
| `cancel_proxy_thread.py` | `0x580B` | Cancel Proxy Thread | Foreground |
| `fabric_crawl_out.py` | `0x5701` | Fabric Crawl Out | Foreground |
| `__init__.py` | — | Package re-exports | — |

### 3.5 Test Files

| File | Lines | What It Tests |
|------|-------|---------------|
| `tests/test_gfd_device.py` | 246 | GFD lifecycle, CCI Identify, mailbox dispatch, unknown opcodes, no-BAR assertions |
| `tests/test_gae_proxy_management.py` | 644 | GaeManager unit tests + ProxyGfdMgmt/GetStatus/Cancel command handlers + DspCciTunnel E2E |
| `tests/test_host_gae_proxy.py` | 470 | GaeCciMailbox routing + CxlRootPortDevice.gae_command() + full 3-command host flow |
| `tests/test_gae_tcp_integration.py` | 364 | Real TCP E2E: Host→Switch→GAE→GFD with production CxlPacketProcessor |

---

## 4. End-to-End Code Flows

### 4.1 Flow A: Host → GAE Proxy → GFD (Production TCP Path)

This is the full production path exercised by `test_gae_tcp_integration.py`:

```
Step 1: Host initiates proxy command
  CxlSimpleHost.gae_proxy_gfd_mgmt(gfd_opcode=0x0001)
    → ProxyGfdMgmtRequestPayload(gfd_opcode=0x0001, gfd_payload=b"").dump()
    → CxlRootPortDevice.gae_command(opcode=0x5809, payload=<bytes>)
      → CciMessagePacket.create(opcode=0x5809, category=REQUEST)
      → cci_fifo.host_to_target.put(req_msg)

Step 2: R-type CxlPacketProcessor serializes to TCP
  CxlPacketProcessor(R)._process_outgoing_packets()
    → cci_fifo.host_to_target.get()
    → CciPayloadPacket.create(req_msg)          # wraps with SystemHeader
    → writer.write(bytes(wire_packet))           # sends over TCP

Step 3: USP-type CxlPacketProcessor deserializes from TCP
  CxlPacketProcessor(USP)._process_incoming_packets()
    → PacketReader reads SystemHeader → payload_type == CCI_MCTP → is_cci()
    → cci_fifo.host_to_target.put(inner_msg)     # puts on USP-side queue

Step 4: GaeCciMailbox dispatches on the switch
  GaeCciMailbox._run_mailbox()
    → cci_fifo.host_to_target.get()              # reads from USP queue
    → CciRequest(opcode=0x5809, payload=<bytes>)
    → CciExecutor.execute_command(request)
    → ProxyGfdMgmtCommand._execute(request)
      → ProxyGfdMgmtRequestPayload.parse(payload)
      → gae_manager.start_proxy(gfd_opcode=0x0001, gfd_request_payload=b"")

Step 5: GaeManager spawns async proxy task
  GaeManager.start_proxy()
    → tid = _alloc_thread_id()                   # returns 1
    → entry = ProxyThreadEntry(thread_id=1, gfd_opcode=0x0001)
    → asyncio.create_task(_run())
      → _run():
          if has_tunnel:
            resp = await self._gfd_tunnel.send_and_wait(CciRequest(0x0001, b""))
          entry.response = resp
          entry.completed = True
    → return tid  (=1)

Step 6: ProxyGfdMgmtCommand returns thread_id to host
  ProxyGfdMgmtCommand._execute() returns:
    → ProxyGfdMgmtResponsePayload(thread_id=1).dump()
    → CciResponse(payload=<2 bytes>)

Step 7: Response travels back to host (reverse of Steps 2-3)
  GaeCciMailbox → CciMessagePacket.create(opcode=0x5809, category=RESPONSE)
    → cci_fifo.target_to_host.put(resp_msg)
    → CxlPacketProcessor(USP) wraps → TCP → CxlPacketProcessor(R) unwraps
    → cci_fifo.target_to_host.put(resp_msg)
    → CxlRootPortDevice.gae_command() reads → returns (SUCCESS, bytes)
    → CxlSimpleHost.gae_proxy_gfd_mgmt() returns Result(thread_id=1)

Step 8: Proxy task runs asynchronously on the switch
  DspCciTunnel.send_and_wait(CciRequest(0x0001, b""))
    → CciMessagePacket.create(opcode=0x0001, category=REQUEST)
    → cci_fifo.host_to_target.put(req_msg)       # DSP-side queue
    → GFD _run_cci_mailbox() reads it
    → CciExecutor → IdentifyCommand._execute()
    → CciResponse(payload=<Identify response bytes>)
    → cci_fifo.target_to_host.put(resp_msg)
    → DspCciTunnel._drain_responses() resolves future
    → entry.response = <CciResponse>
    → entry.completed = True

Step 9: Host polls for status
  CxlSimpleHost.gae_get_proxy_status(thread_id=1)
    → GetProxyThreadStatusRequestPayload(thread_id=1).dump()
    → CxlRootPortDevice.gae_command(opcode=0x580A, payload=<bytes>)
    → [same TCP path as above]
    → GetProxyThreadStatusCommand._execute()
      → gae_manager.get_proxy_status(1)
      → entry.completed=True, entry.return_code=SUCCESS
      → GetProxyThreadStatusResponsePayload(
            thread_id=1, completed=True, gfd_return_code=0,
            gfd_response_payload=<Identify bytes>
        ).dump()
    → Host receives completed=True with GFD Identify payload
```

### 4.2 Flow B: FM → GFD via Fabric Crawl Out

```
Step 1: FM sends CCI via MCTP
  FM CLI → MctpConnectionClient → TCP :8100 → MctpCciExecutor
    → FabricCrawlOutCommand.create_cci_request(target_port=1, gfd_opcode=0x0001)
      → EmbeddedCciCommand(opcode=0x0001, payload=b"")
      → FabricCrawlOutRequestPayload(target_port=1, embedded_cmd=<above>)

Step 2: FabricCrawlOutCommand executes on the switch
  FabricCrawlOutCommand._execute(request)
    → FabricCrawlOutRequestPayload.parse(request.payload)
    → tunnel_registry.get(target_port=1)         # looks up DspCciTunnel for port 1
    → tunnel.send_and_wait(embedded_cmd.to_cci_request())

Step 3: DspCciTunnel forwards to GFD
  [Same as Flow A Step 8]

Step 4: Response wrapped back in FabricCrawlOut response
  → EmbeddedCciResponse.from_cci_response(gfd_response)
  → FabricCrawlOutResponsePayload(embedded_resp=<above>)
  → CciResponse(payload=<Fabric Crawl Out response bytes>)
  → Returns to FM via MCTP
```

### 4.3 Flow C: GFD CCI Mailbox (Device Side)

```
CxlGfdDevice._run_cci_mailbox()                    # cxl_gfd_device.py L143
  while True:
    packet = await cci_fifo.host_to_target.get()    # blocks until request arrives
    if packet is None: break                        # sentinel = shutdown

    # Normalize packet to CciMessagePacket
    if isinstance(packet, CciMessagePacket):
      cci_msg = packet
    elif hasattr(packet, "get_cci_message"):
      cci_msg = packet.get_cci_message()
    else:
      cci_msg = CciMessagePacket(bytearray(bytes(packet)))

    opcode = cci_msg.cci_msg_header.command_opcode
    tag    = cci_msg.cci_msg_header.message_tag
    payload = cci_msg.get_payload()

    # Dispatch
    request = CciRequest(opcode=opcode, payload=payload)
    response = await cci_executor.execute_command(request)

    # Build response
    resp_msg = CciMessagePacket.create(
      data=response.payload or b"",
      message_category=RESPONSE,
      opcode=opcode,
      message_tag=tag,                              # echo the tag
      return_code=int(response.return_code),
    )
    await cci_fifo.target_to_host.put(resp_msg)
```

### 4.4 Flow D: GAE Proxy 3-Command Lifecycle

The host uses three commands in sequence to proxy a CCI command to the GFD:

```
┌─────────────────────────────────────────────────────────────────────┐
│  HOST                    SWITCH (GAE)                  GFD         │
│                                                                     │
│  1. ProxyGfdMgmt ────►  GaeManager.start_proxy() ──►  CCI cmd     │
│     (0x5809)            returns thread_id=N           (async)      │
│                                                                     │
│  2. GetProxyStatus ───► GaeManager.get_proxy_status(N)             │
│     (0x580A)            returns {completed, rc, payload}           │
│                         (poll until completed=True)                │
│                                                                     │
│  3. CancelProxy ──────► GaeManager.cancel_proxy(N)                 │
│     (0x580B)            cancels task, marks ABORTED                │
│     (optional)          idempotent if already complete             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Component Deep-Dives

### 5.1 CxlGfdDevice (`cxl_gfd_device.py`)

**Purpose**: The actual CXL GFD device implementation. Pure CCI-mailbox;
no BAR, no PCIe config space.

**Key Design Decisions**:
- Only `cci_fifo` from the `CxlConnection` is used. `mmio_fifo` and `cfg_fifo`
  are intentionally ignored.
- The GFD registers `IdentifyCommand` at init with `component_type=GFD (0x04)`.
- `_run_cci_mailbox()` is an infinite loop that reads from `cci_fifo.host_to_target`,
  dispatches to `CciExecutor`, and writes to `cci_fifo.target_to_host`.
- Shutdown via sentinel: `_stop()` puts `None` on the FIFO to break the loop.

**Instance Variables** (set in `__init__`, L83–117):
```python
self._port_index: int
self._serial_number: str
self._cci_fifo       = transport_connection.cci_fifo
self._cci_executor   = CciExecutor(label=label)
```

**What was removed** (compared to the old wrong implementation):
- `CxlIoManager` — PCIe config + MMIO BAR (GFD has no BAR)
- `CxlMemManager` — CXL.mem stub (GFD is CCI-only)
- `GfdMmioRegisters` — BAR-0 register file
- `CxlType3SldConfigSpace` — PCIe config space
- `PciComponent`, `BarEntry` — PCIe constructs

### 5.2 GaeManager (`gae_manager.py`)

**Purpose**: Central state owner for the GAE on one PBR switch. Manages proxy
threads and GFD binding.

**Instance Variables** (L123–147):
```python
self._label: str
self._vppbs: List[GaeVppbInfo]                      # empty for simple-device GFD
self._gfd_tunnel = None                              # DspCciTunnel (production)
self._gfd_executor: Optional[CciExecutor] = None     # direct (tests)
self._proxy_threads: Dict[int, ProxyThreadEntry] = {}
self._next_thread_id: int = 1
```

**Key Methods**:

| Method | Purpose |
|--------|---------|
| `set_gfd_tunnel(tunnel)` | Bind production DspCciTunnel; clears executor |
| `set_gfd_executor(executor)` | Bind test-mode CciExecutor directly |
| `has_gfd_binding()` | True if either tunnel or executor is bound |
| `start_proxy(opcode, payload)` | Spawn async task → DspCciTunnel or CciExecutor |
| `get_proxy_status(tid)` | Return ProxyThreadEntry or None |
| `cancel_proxy(tid)` | Cancel asyncio task, mark ABORTED |
| `purge_completed_threads()` | Remove completed entries from registry |
| `get_vppbs()` | Return vPPB list (for Identify GAE) |
| `get_vppb_count()` | Return count of vPPBs |

**Proxy Thread Lifecycle**:
```
start_proxy(opcode, payload)
  → alloc tid (monotonically increasing, never reused)
  → create ProxyThreadEntry(tid, opcode)
  → asyncio.create_task(_run)
    _run:
      if tunnel:  resp = await tunnel.send_and_wait(req)
      else:       resp = await executor.execute_command(req)
      entry.response = resp
      entry.return_code = int(resp.return_code)
      entry.completed = True
  → return tid
```

### 5.3 GaeCciMailbox (`gae_cci_mailbox.py`)

**Purpose**: Bridge between the host (via USP cci_fifo) and the switch-side
CciExecutor that has GAE commands registered.

**Design**: Mirrors `CxlGfdDevice._run_cci_mailbox()` exactly, but lives on
the switch side. It handles four packet types for robustness:

1. `CciMessagePacket` — in-process Queue path
2. `CciRequestPacket` — TCP path (from PacketReader)
3. Objects with `get_cci_message()` — `CciPayloadPacket`
4. Raw bytes fallback — wraps into `CciMessagePacket`

**Key Flow** (`_run_mailbox()`, L93–172):
```python
while True:
    packet = await cci_fifo.host_to_target.get()
    if packet is None: break
    # normalize to (opcode, tag, payload)
    request = CciRequest(opcode=opcode, payload=payload)
    response = await cci_executor.execute_command(request)
    resp_msg = CciMessagePacket.create(...)
    await cci_fifo.target_to_host.put(resp_msg)
```

### 5.4 PbrSwitchManager (`pbr_switch_manager.py`)

**Purpose**: Manages PBR switch runtime state: DRT tables, PID assignments,
PID bindings, and switch identity info.

**Key Concepts**:

**DRT (DPID Routing Table)**:
- Flat array of 4096 entries, indexed by DPID (12-bit PID value).
- NOT a port↔vPPB map. Maps DPID → egress physical port.
- FM must call `set_drt()` AFTER `assign_pid()` — PID assignment does NOT
  auto-populate the DRT.

**Instance Variables** (L205–221):
```python
self._switch_info: PbrSwitchInfo          # identity/capability info
self._drt_tables: List[DrtTable]          # one or more DRT tables
self._pid_targets: List[PidTarget]        # available targets
self._pid_assignments: Dict[int, PidAssignment]  # PID → target
self._pid_bindings: Dict[Tuple[int,int], PidBinding]  # (vcs, vppb) → binding
```

**FM Workflow**:
```
1. Configure PID Assignment (0x5704) — assigns PID to a target
2. Set DRT (0x5709) — programs DRT[drt_index][pid] = {type, port}
3. Switch HW uses DRT to route incoming TLPs by DPID
```

### 5.5 CxlSwitch PBR/GAE Wiring (`cxl_switch.py`)

The `CxlSwitch.__init__()` method wires everything together when
`enable_pbr=True`:

```python
# L151: Create PBR switch manager
self._pbr_switch_manager = PbrSwitchManager()

# L157-159: Create GAE manager
self._gae_manager = GaeManager(vppbs=[], label="Switch0:GAE")

# L187-193: Create PBR data-plane router
self._pbr_switch_router = PbrSwitchRouter(...)

# L228-229: Fix gae_support_map = 0x01 (bit 0 = VCS 0 has GAE)
self._pbr_switch_manager.get_identify_info().gae_support_map = 0x01

# L254-272: Register PBR + GAE CCI commands
commands.extend([
    IdentifyPbrSwitchCommand(self._pbr_switch_manager),
    ConfigurePidAssignmentCommand(self._pbr_switch_manager),
    GetPidBindingCommand(self._pbr_switch_manager),
    ConfigurePidBindingCommand(self._pbr_switch_manager),
    GetDrtCommand(self._pbr_switch_manager),
    SetDrtCommand(self._pbr_switch_manager),
    IdentifyGaeCommand(self._gae_manager),
    GetPidAccessVectorsCommand(self._gae_manager),
    ProxyGfdMgmtCommand(self._gae_manager),
    GetProxyThreadStatusCommand(self._gae_manager),
    CancelProxyThreadCommand(self._gae_manager),
])

# L286-292: DSP port event handler — binds/unbinds GFD tunnel
if event.connected and self._gae_manager is not None:
    tunnel = self._mctp_cci_executor.get_tunnel(event.port_id)
    if tunnel is not None:
        self._gae_manager.set_gfd_tunnel(tunnel)
elif not event.connected and self._gae_manager is not None:
    self._gae_manager.set_gfd_tunnel(None)
```

### 5.6 CxlSimpleHost GAE Methods (`cxl_simple_host.py`)

The host exposes three high-level GAE methods:

```python
async def gae_proxy_gfd_mgmt(self, gfd_opcode, gfd_payload=b"", timeout=5.0) -> Result:
    """Send Proxy GFD Mgmt (0x5809). Returns Result(thread_id)."""

async def gae_get_proxy_status(self, thread_id, timeout=5.0) -> Result:
    """Send Get Proxy Thread Status (0x580A). Returns Result(dict)."""

async def gae_cancel_proxy(self, thread_id, timeout=5.0) -> Result:
    """Send Cancel Proxy Thread (0x580B). Returns Result("SUCCESS")."""
```

All three delegate to `CxlRootPortDevice.gae_command()`, which builds a
`CciMessagePacket`, puts it on `cci_fifo.host_to_target`, and waits for the
response on `cci_fifo.target_to_host`.

The host also exposes **HostMgr (port 8300) wrappers** that allow external
tools to call these methods via JSON-RPC:
- `HOST_GAE_PROXY_GFD_MGMT`
- `HOST_GAE_GET_PROXY_STATUS`
- `HOST_GAE_CANCEL_PROXY`

### 5.7 DspCciTunnel and DspTunnelRegistry

The `DspCciTunnel` (in `dsp_cci_tunnel.py`) provides the bridge between
switch-side command handlers and the GFD's `cci_fifo`:

```python
class DspCciTunnel:
    def __init__(self, cci_fifo, port_index):
        self._cci_fifo = cci_fifo
        self._port_index = port_index

    async def send_and_wait(self, request: CciRequest) -> CciResponse:
        # Build CciMessagePacket
        req_msg = CciMessagePacket.create(...)
        await self._cci_fifo.host_to_target.put(req_msg)
        # Wait for response
        resp_msg = await self._cci_fifo.target_to_host.get()
        return CciResponse(...)
```

The `DspTunnelRegistry` (in `fabric_crawl_out.py` L189–205) is a simple
dict-backed registry `{port_index: DspCciTunnel}` used by
`FabricCrawlOutCommand` to look up the correct tunnel for a target DSP port.

---

## 6. CCI Command Reference

### 6.1 PBR Switch Commands (§7.7.13)

#### 6.1.1 Identify PBR Switch — `0x5700`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.IDENTIFY_PBR_SWITCH` |
| **Type** | Foreground |
| **Input Payload** | None |
| **Handler** | `IdentifyPbrSwitchCommand(pbr_switch_manager)` |
| **Logic** | Calls `pbr_switch_manager.get_identify_info()`, builds response |

**Output Payload** (`IdentifyPbrSwitchResponsePayload`, 12 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 8 | `gae_support_map` (bitmask, bit pos = VCS ID) |
| `0x08` | 1 | `num_drts` (must be > 0) |
| `0x09` | 1 | `num_rgts` |
| `0x0A` | 1 | Reserved |
| `0x0B` | 1 | Dynamic Routing Mode Capabilities (bit field) |

#### 6.1.2 Configure PID Assignment — `0x5704`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_ASSIGNMENT` |
| **Type** | Foreground |
| **Handler** | `ConfigurePidAssignmentCommand(pbr_switch_manager)` |
| **Logic** | Iterates entries, calls `assign_pid()` or `clear_pid()` per entry. Fails fast. |
| **Output Payload** | None |

**Input Payload** (`ConfigurePidAssignmentRequestPayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 1 | Operation (`000b`=Assign, `001b`=Clear) |
| `0x01` | 1 | Reserved |
| `0x02` | 2 | Number of Targets |
| `0x04` | N×5 | PID Assignment Entry list |

**PID Assignment Entry** (`PidAssignmentEntry`, 5 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | PID (Bits[11:0]) |
| `0x02` | 2 | Target ID |
| `0x04` | 1 | Instance ID |

> **Important**: This command does NOT update DRT entries. The FM must
> separately call Set DRT (`0x5709`) after assigning PIDs.

#### 6.1.3 Get PID Binding — `0x5705`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.GET_PID_BINDING` |
| **Type** | Foreground |
| **Handler** | `GetPidBindingCommand(pbr_switch_manager)` |
| **Logic** | Calls `pbr_switch_manager.get_pid_binding(vcs, vppb)` |

**Input** (2 bytes): `target_vcs`, `target_vppb`

**Output** (`GetPidBindingResponsePayload`, 24 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | PID (Bits[11:0]); `0xFFF` if unbound |
| `0x02` | 2 | Reserved |
| `0x04` | 8 | Latency Entry Base Unit (HMAT) |
| `0x0C` | 2 | Latency Entry (HMAT) |
| `0x0E` | 8 | BW Entry Base Unit (HMAT) |
| `0x16` | 2 | BW Entry (HMAT) |

#### 6.1.4 Configure PID Binding — `0x5706`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.CONFIGURE_PID_BINDING` |
| **Type** | **Background** (uses `ProgressCallback`) |
| **Handler** | `ConfigurePidBindingCommand(pbr_switch_manager)` |
| **Logic** | Calls `pbr_switch_manager.configure_pid_binding()` with HMAT info |
| **Output Payload** | None |

**Input** (`ConfigurePidBindingRequestPayload`, 28 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 1 | Operation (`000b`=Bind, `001b`=Unbind) |
| `0x01` | 1 | Target VCS ID |
| `0x02` | 1 | Target vPPB index |
| `0x03` | 1 | Reserved |
| `0x04` | 2 | PID (Bits[11:0]) |
| `0x06` | 2 | Reserved |
| `0x08` | 8 | Latency Entry Base Unit |
| `0x10` | 2 | Latency Entry |
| `0x12` | 8 | BW Entry Base Unit |
| `0x1A` | 2 | BW Entry |

> This is a **background** command because binding requires link-state
> transitions. The FM polls Background Operation Status to know when it
> completes.

#### 6.1.5 Get DRT — `0x5708`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.GET_DRT` |
| **Type** | Foreground |
| **Handler** | `GetDrtCommand(pbr_switch_manager)` |
| **Logic** | Calls `pbr_switch_manager.get_drt(index, start, count)` |

**Input** (6 bytes): `drt_index`, `num_entries`, `start_entry`

**Output** (`GetDrtResponsePayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 1 | DRT Index |
| `0x01` | 1 | Reserved |
| `0x02` | 2 | Number of Entries returned |
| `0x04` | 2 | Start Entry |
| `0x06` | 1 | Associated RGT Index |
| `0x07` | 1 | Reserved |
| `0x08` | N×2 | DRT Entry list |

**DRT Entry** (2 bytes each):
| Byte | Field |
|------|-------|
| 0 | Bits[1:0] = Entry Type (`00`=Invalid, `01`=Physical Port, `10`=RGT Index) |
| 1 | Routing Target (port number OR RGT entry index) |

#### 6.1.6 Set DRT — `0x5709`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.SET_DRT` |
| **Type** | Foreground |
| **Handler** | `SetDrtCommand(pbr_switch_manager)` |
| **Logic** | Calls `pbr_switch_manager.set_drt(index, start, entries)` |
| **Output** | None |

**Input** (`SetDrtRequestPayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 1 | DRT Index |
| `0x01` | 1 | Reserved |
| `0x02` | 2 | Number of Entries |
| `0x04` | 2 | Start Entry |
| `0x06` | N×2 | DRT Entry list |

### 6.2 GAE Commands (§7.7.14)

#### 6.2.1 Identify GAE — `0x5800`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_GAE_COMMAND_OPCODE.IDENTIFY_GAE` |
| **Type** | Foreground |
| **Handler** | `IdentifyGaeCommand(gae_manager)` |
| **Logic** | Calls `gae_manager.get_vppbs()` |

**Output** (`IdentifyGaeResponsePayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Number of vPPBs with Global Memory Support |
| `0x02` | 2 | Reserved |
| `0x04` | N×4 | vPPB Global Memory Support Info list |

**vPPB Entry** (4 bytes each):
| Byte | Field |
|------|-------|
| 0 | vPPB ID |
| 1 | Bit[0] = Global Memory Support (G-FAM) |
| 2-3 | Reserved |

> For a simple-device GFD: num_vppbs = 0, list is empty.

#### 6.2.2 Get PID Access Vectors — `0x5802`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_GAE_COMMAND_OPCODE.GET_PID_ACCESS_VECTORS` |
| **Type** | Foreground |
| **Handler** | `GetPidAccessVectorsCommand(gae_manager)` |
| **Logic** | For simple-device GFD: returns GMV=0, VTV=0 |

**Input** (2 bytes): PID (Bits[11:0])

**Output** (`GetPidAccessVectorsResponsePayload`, 20 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 8 | GMV (Global Memory Vector) — bitmask by VCS ID |
| `0x08` | 8 | VTV (Valid Target Vector) — bitmask by vPPB index |
| `0x10` | 2 | PID (echoed) |
| `0x12` | 2 | Reserved |

#### 6.2.3 Proxy GFD Management Command — `0x5809`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_GAE_COMMAND_OPCODE.PROXY_GFD_MGMT_CMD` |
| **Type** | Foreground (but spawns an async background task) |
| **Handler** | `ProxyGfdMgmtCommand(gae_manager)` |
| **Logic** | Calls `gae_manager.start_proxy()` → returns thread_id |

**Input** (`ProxyGfdMgmtRequestPayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | GFD Command Opcode (the CCI opcode to forward) |
| `0x02` | 2 | GFD Command Payload Length |
| `0x04` | N | GFD Command Payload |

**Output** (`ProxyGfdMgmtResponsePayload`, 2 bytes):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Proxy Thread ID |

**Error Handling**:
- No GFD binding → `INTERNAL_ERROR`
- Invalid/short payload → `INVALID_INPUT`
- `start_proxy()` returns 0 → `INTERNAL_ERROR`

#### 6.2.4 Get Proxy Thread Status — `0x580A`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_GAE_COMMAND_OPCODE.GET_PROXY_THREAD_STATUS` |
| **Type** | Foreground |
| **Handler** | `GetProxyThreadStatusCommand(gae_manager)` |
| **Logic** | Calls `gae_manager.get_proxy_status(thread_id)` |

**Input** (2 bytes): Proxy Thread ID

**Output** (`GetProxyThreadStatusResponsePayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Proxy Thread ID (echoed) |
| `0x02` | 1 | Status: Bit[0] = Completed |
| `0x03` | 1 | Reserved |
| `0x04` | 2 | GFD Return Code (valid only when Completed=1) |
| `0x06` | 2 | Reserved |
| `0x08` | N | GFD Response Payload (present only when Completed=1) |

#### 6.2.5 Cancel Proxy Thread — `0x580B`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_GAE_COMMAND_OPCODE.CANCEL_PROXY_THREAD` |
| **Type** | Foreground |
| **Handler** | `CancelProxyThreadCommand(gae_manager)` |
| **Logic** | Calls `gae_manager.cancel_proxy(thread_id)` |
| **Output** | None |

**Input** (2 bytes): Proxy Thread ID

**Behavior**:
- Already completed → SUCCESS (idempotent)
- In-flight → cancels asyncio task, marks ABORTED, returns SUCCESS
- Unknown thread_id → INVALID_INPUT

#### 6.2.6 Fabric Crawl Out — `0x5701`

| Field | Value |
|-------|-------|
| **Opcode** | `CCI_FM_API_COMMAND_OPCODE.FABRIC_CRAWL_OUT` |
| **Type** | Foreground |
| **Handler** | `FabricCrawlOutCommand(tunnel_registry)` |
| **Logic** | Looks up DspCciTunnel by target_port, forwards embedded CCI command |

**Input** (`FabricCrawlOutRequestPayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 1 | Target Port Number (DSP port index) |
| `0x01` | 1 | Reserved |
| `0x02` | 2 | Embedded Command Size |
| `0x04` | N | Embedded CCI Command (Table 7-117) |

**Embedded CCI Command** (`EmbeddedCciCommand`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Command Opcode |
| `0x02` | 2 | Payload Length |
| `0x04` | N | Command Payload |

**Output** (`FabricCrawlOutResponsePayload`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Embedded Response Size |
| `0x02` | 2 | Reserved |
| `0x04` | N | Embedded CCI Response (Table 7-119) |

**Embedded CCI Response** (`EmbeddedCciResponse`):
| Offset | Len | Field |
|--------|-----|-------|
| `0x00` | 2 | Return Code |
| `0x02` | 2 | Reserved |
| `0x04` | N | Response Payload |

---

## 7. Data Structures & Wire Formats

### 7.1 PBR Switch Manager Structures

```python
# Constants
PID_MAX         = 0xFFF    # 12-bit PID space
PID_UNASSIGNED  = 0xFFF    # sentinel
DRT_TABLE_SIZE  = 4096     # 2^12 entries per DRT

# DRT Entry Type (Table 7-133)
class DrtEntryType(IntEnum):
    INVALID       = 0b00   # no routing — drop
    PHYSICAL_PORT = 0b01   # route to physical port
    RGT_INDEX     = 0b10   # route via RGT entry
    RESERVED      = 0b11   # invalid

@dataclass
class DrtEntry:
    entry_type: DrtEntryType    # 2 bits
    routing_target: int         # 8 bits (port number or RGT index)

@dataclass
class DrtTable:
    associated_rgt_index: int = 0
    entries: List[DrtEntry]     # 4096 entries

@dataclass
class PidTarget:
    target_id: int
    target_type: PidTargetType  # FABRIC_PORT, HOST_EDGE_PORT, DOWNSTREAM_EDGE_PORT
    instance_id: int
    vcs_id: int
    physical_port_id: int
    pid: int = PID_UNASSIGNED

@dataclass
class PidAssignment:
    pid: int
    target_id: int
    instance_id: int

class PidBindingOperation(IntEnum):
    BIND   = 0b000
    UNBIND = 0b001

@dataclass
class HmatInfo:
    latency_entry_base_unit: int = 0   # 8 bytes
    latency_entry: int = 0             # 2 bytes
    bw_entry_base_unit: int = 0        # 8 bytes
    bw_entry: int = 0                  # 2 bytes

@dataclass
class PidBinding:
    pid: int = PID_UNASSIGNED
    hmat: HmatInfo

@dataclass
class PbrSwitchInfo:
    gae_support_map: int = 0            # 8-byte bitmask
    num_drts: int = 1
    num_rgts: int = 0
    random_supported: bool = False
    congestion_avoidance_supported: bool = False
    advanced_ca_supported: bool = False
    vendor_routing_mode1_supported: bool = False
    vendor_routing_mode2_supported: bool = False
```

### 7.2 GAE Manager Structures

```python
@dataclass
class GaeVppbInfo:
    vppb_id: int = 0
    global_memory_support: bool = False   # G-FAM capable
    pid: int = 0xFFF                      # PID_UNASSIGNED

@dataclass
class ProxyThreadEntry:
    thread_id: int
    gfd_opcode: int
    task: Optional[asyncio.Task] = None
    response: Optional[CciResponse] = None
    completed: bool = False
    return_code: int = int(CCI_RETURN_CODE.SUCCESS)
```

### 7.3 CCI Opcode Enumerations

**PBR Switch Opcodes** (`CCI_FM_API_COMMAND_OPCODE`):
| Name | Value | Spec Section |
|------|-------|--------------|
| `IDENTIFY_PBR_SWITCH` | `0x5700` | §7.7.13.1 |
| `FABRIC_CRAWL_OUT` | `0x5701` | §7.7.13.2 |
| `CONFIGURE_PID_ASSIGNMENT` | `0x5704` | §7.7.13.5 |
| `GET_PID_BINDING` | `0x5705` | §7.7.13.6 |
| `CONFIGURE_PID_BINDING` | `0x5706` | §7.7.13.7 |
| `GET_DRT` | `0x5708` | §7.7.13.9 |
| `SET_DRT` | `0x5709` | §7.7.13.10 |

**GAE Opcodes** (`CCI_GAE_COMMAND_OPCODE`):
| Name | Value | Spec Section |
|------|-------|--------------|
| `IDENTIFY_GAE` | `0x5800` | §7.7.14.1 |
| `GET_PID_ACCESS_VECTORS` | `0x5802` | §7.7.14.3 |
| `PROXY_GFD_MGMT_CMD` | `0x5809` | §7.7.14.10 |
| `GET_PROXY_THREAD_STATUS` | `0x580A` | §7.7.14.11 |
| `CANCEL_PROXY_THREAD` | `0x580B` | §7.7.14.12 |

---

## 8. Test Coverage Map

### 8.1 test_gfd_device.py (246 lines, 6 tests)

| Test | What It Validates | Key Assertions |
|------|-------------------|----------------|
| `test_gfd_starts_and_stops` | GFD lifecycle (RUNNING → STOPPED) | `wait_for_ready()` completes within 5s |
| `test_gfd_cci_identify` | CCI Identify via cci_fifo | `component_type == GFD (0x04)`, tag echo, SUCCESS |
| `test_gfd_cci_mailbox_dispatch` | Full cci_fifo round-trip | Tag echo for 4 different tags (0, 1, 127, 255) |
| `test_gfd_cci_unknown_opcode` | Unknown opcode handling | `return_code == UNSUPPORTED`, no crash |
| `test_gfd_cci_executor_accessible` | `get_cci_executor()` helper | Executor is not None, IdentifyCommand registered |
| `test_gfd_no_bar` | Spec-correctness: no BAR | No `get_bar_size`, `get_registers`, `_gfd_registers`, `_cxl_io_manager` |

**Setup Pattern**:
```python
conn = CxlConnection()
gfd = GenericFabricDevice(test_mode=True, cxl_connection=conn, port_index=1)
# CCI requests sent via: conn.cci_fifo.host_to_target.put(CciMessagePacket)
# GFD responses read via: conn.cci_fifo.target_to_host.get()
```

### 8.2 test_gae_proxy_management.py (644 lines, 16 tests)

#### Part 1: GaeManager Unit Tests (in-process executor mode)

| Test | What It Validates |
|------|-------------------|
| `test_gae_has_no_binding_initially` | Fresh GaeManager has no binding, zero threads |
| `test_gae_set_executor_has_binding` | `set_gfd_executor()` → `has_gfd_binding()` = True |
| `test_gae_set_tunnel_has_binding` | `set_gfd_tunnel()` → `has_gfd_binding()` = True, executor = None |
| `test_gae_start_proxy_no_binding_returns_zero` | No binding → `start_proxy()` returns 0 |
| `test_gae_start_proxy_executor_returns_thread_id` | With executor → thread_id=1, completed, SUCCESS |
| `test_gae_proxy_response_contains_identify_payload` | Proxy response contains GFD Identify with component_type=GFD |
| `test_gae_thread_id_increments` | 5 calls → tids [1,2,3,4,5], all complete |
| `test_gae_cancel_unknown_thread` | Unknown tid → INVALID_INPUT |
| `test_gae_cancel_completed_thread_is_idempotent` | Already completed → SUCCESS (idempotent) |
| `test_gae_cancel_marks_entry_aborted` | In-flight cancel → completed=True, return_code=ABORTED |
| `test_gae_purge_completed_threads` | 3 completed → purge returns 3, thread_count=0 |

#### Part 2: Command Handler Unit Tests

| Test | What It Validates |
|------|-------------------|
| `test_proxy_cmd_returns_thread_id` | `ProxyGfdMgmtCommand._execute()` returns thread_id=1 |
| `test_proxy_cmd_no_binding_returns_internal_error` | No binding → INTERNAL_ERROR |
| `test_proxy_cmd_empty_payload_returns_invalid_input` | Empty payload → INVALID_INPUT |
| `test_get_proxy_thread_status_completed` | Completed proxy → status has GFD Identify payload |
| `test_get_proxy_thread_status_unknown_thread` | Unknown tid → INVALID_INPUT |
| `test_cancel_proxy_thread_success` | In-flight cancel → ABORTED |
| `test_cancel_proxy_thread_unknown_id` | Unknown tid → INVALID_INPUT |

#### Part 3: Full E2E via DspCciTunnel + GFD cci_fifo

| Test | What It Validates |
|------|-------------------|
| `test_proxy_via_dsp_cci_tunnel_end_to_end` | Production path: GAE → DspCciTunnel → cci_fifo → GFD → response |
| `test_full_proxy_3_command_flow` | Complete 3-command flow: 0x5809→0x580A→0x580B + purge |

### 8.3 test_host_gae_proxy.py (470 lines, 6 tests)

#### Part 1: GaeCciMailbox Unit Tests

| Test | What It Validates |
|------|-------------------|
| `test_gae_cci_mailbox_routes_proxy_gfd_mgmt` | Mailbox routes 0x5809 → CciExecutor → response on cci_fifo |
| `test_gae_cci_mailbox_routes_get_proxy_status` | Mailbox routes 0x580A → returns completed status |
| `test_gae_cci_mailbox_cancel_proxy` | Mailbox routes 0x580B → entry marked ABORTED |

#### Part 2: CxlRootPortDevice.gae_command() Tests

| Test | What It Validates |
|------|-------------------|
| `test_root_port_device_gae_command_proxy` | `gae_command()` sends 0x5809 → gets thread_id=1 |
| `test_root_port_device_gae_full_3_command_flow` | Full 3-command flow via `gae_command()` |
| `test_gae_cci_mailbox_unknown_opcode_returns_unsupported` | Unknown opcode → UNSUPPORTED |

### 8.4 test_gae_tcp_integration.py (364 lines, 4 tests)

| Test | What It Validates |
|------|-------------------|
| `test_pbr_switch_gae_support_map_is_1` | `IdentifyPbrSwitch` returns `gae_support_map=0x01` |
| `test_gae_proxy_over_real_tcp` | Full TCP E2E: Host→TCP→Switch→GAE→GFD→response→TCP→Host |
| `test_r_type_cci_wrapping_adds_system_header` | `CciPayloadPacket.create()` adds SystemHeader with CCI_MCTP type |
| `test_r_type_incoming_cci_routes_to_target_to_host` | `CxlPacketProcessor(R)` routes incoming CCI to cci_fifo |

### 8.5 Coverage Matrix

| Component | Unit Tests | Integration Tests | TCP E2E |
|-----------|-----------|-------------------|---------|
| CxlGfdDevice | ✅ 5 tests | ✅ 1 test | ✅ 1 test |
| GaeManager | ✅ 11 tests | ✅ 2 tests | — |
| GaeCciMailbox | ✅ 3 tests | ✅ 2 tests | ✅ 1 test |
| ProxyGfdMgmtCommand | ✅ 3 tests | ✅ 2 tests | ✅ 1 test |
| GetProxyThreadStatusCommand | ✅ 2 tests | ✅ 2 tests | ✅ 1 test |
| CancelProxyThreadCommand | ✅ 2 tests | ✅ 2 tests | — |
| DspCciTunnel | — | ✅ 1 test | ✅ 1 test |
| CxlPacketProcessor (R CCI) | — | — | ✅ 2 tests |
| PbrSwitchManager (identity) | — | — | ✅ 1 test |

---

## 9. Adding a New Feature

### 9.1 Adding a New CCI Command to the GFD

**Example**: Add a vendor-specific "Get GFD Firmware Version" command (opcode `0xC001`).

#### Step 1: Create the command file

Create `opencis/cxl/cci/vendor_specific/get_gfd_fw_version.py`:

```python
from dataclasses import dataclass
from opencis.cxl.component.cci_executor import (
    CciRequest, CciResponse, CciForegroundCommand,
)

OPCODE = 0xC001

@dataclass
class GetGfdFwVersionResponsePayload:
    major: int = 0
    minor: int = 0
    patch: int = 0
    PAYLOAD_SIZE = 6

    def dump(self) -> bytes:
        from struct import pack
        return pack("<HHH", self.major, self.minor, self.patch)

    @classmethod
    def parse(cls, data: bytes) -> "GetGfdFwVersionResponsePayload":
        from struct import unpack_from
        return cls(*unpack_from("<HHH", data, 0))


class GetGfdFwVersionCommand(CciForegroundCommand):
    OPCODE = OPCODE

    def __init__(self, major=1, minor=0, patch=0):
        super().__init__(self.OPCODE)
        self._version = GetGfdFwVersionResponsePayload(major, minor, patch)

    async def _execute(self, _: CciRequest) -> CciResponse:
        resp = CciResponse()
        resp.payload = self._version.dump()
        return resp

    @staticmethod
    def create_cci_request() -> CciRequest:
        req = CciRequest()
        req.opcode = OPCODE
        return req

    @staticmethod
    def parse_response_payload(data: bytes) -> GetGfdFwVersionResponsePayload:
        return GetGfdFwVersionResponsePayload.parse(data)
```

#### Step 2: Register the command on the GFD

In `cxl_gfd_device.py` → `_register_cci_commands()`:

```python
from opencis.cxl.cci.vendor_specific.get_gfd_fw_version import (
    GetGfdFwVersionCommand,
)

def _register_cci_commands(self) -> None:
    # ... existing Identify registration ...
    self._cci_executor.register_command(
        GetGfdFwVersionCommand.OPCODE,
        GetGfdFwVersionCommand(major=1, minor=2, patch=3),
    )
```

#### Step 3: Test the command

In `tests/test_gfd_device.py`:

```python
@pytest.mark.asyncio
async def test_gfd_cci_get_fw_version():
    gfd, conn = _make_gfd()
    async def _run():
        run_task = asyncio.create_task(gfd.run())
        await asyncio.wait_for(gfd.wait_for_ready(), timeout=5.0)
        resp = await _send_cci_request(conn, opcode=0xC001, tag=1)
        assert resp.cci_msg_header.return_code == int(CCI_RETURN_CODE.SUCCESS)
        payload = resp.get_payload()
        from opencis.cxl.cci.vendor_specific.get_gfd_fw_version import (
            GetGfdFwVersionResponsePayload,
        )
        parsed = GetGfdFwVersionResponsePayload.parse(payload)
        assert parsed.major == 1
        assert parsed.minor == 2
        assert parsed.patch == 3
        await gfd.stop()
        await run_task
    await _run()
```

#### Step 4: Test the command via GAE proxy

The command should automatically be reachable via the GAE proxy path:

```python
@pytest.mark.asyncio
async def test_gfd_fw_version_via_proxy():
    # Same as test_full_proxy_3_command_flow but with gfd_opcode=0xC001
    ...
    tid = await gae.start_proxy(gfd_opcode=0xC001, gfd_request_payload=b"")
    await asyncio.sleep(0.1)
    entry = gae.get_proxy_status(tid)
    assert entry.completed
    parsed = GetGfdFwVersionResponsePayload.parse(entry.response.payload)
    assert parsed.major == 1
```

### 9.2 Adding a New GAE CCI Command

**Example**: Add "Get GAE Statistics" (opcode `0x5803`).

#### Step 1: Create command file in `opencis/cxl/cci/fabric_manager/gae/`

#### Step 2: Add state to `GaeManager` (e.g., `_stats: dict`)

#### Step 3: Register in `cxl_switch.py` `_initialize_mctp_endpoint()` (L264–272)

#### Step 4: Export from `opencis/cxl/cci/fabric_manager/gae/__init__.py`

#### Step 5: Add test in `tests/test_gae_proxy_management.py` or new test file

### 9.3 Adding a New PBR Switch CCI Command

Same pattern as GAE but:
- State goes in `PbrSwitchManager`
- Command handler takes `PbrSwitchManager` in constructor
- Register in `cxl_switch.py` L254–263

---

## 10. Troubleshooting & FAQ

### 10.1 "GAE ProxyGfdMgmt failed: INTERNAL_ERROR"

**Cause**: No GFD binding in `GaeManager`.

**Fix**: Ensure the GFD is connected before sending proxy commands. Check:
1. `gae_manager.has_gfd_binding()` returns True
2. For production: verify the GFD's TCP connection to the switch DSP port
   triggered the `handle_port_event(connected=True)` handler
3. For tests: call `gae_manager.set_gfd_executor(executor)` or
   `gae_manager.set_gfd_tunnel(tunnel)` before issuing proxy commands

### 10.2 "CxlPacketProcessor(R) dropping CCI packets"

**Cause**: Before the fix in `cxl_packet_processor.py`, R-type processors
had `else: break` for CCI packets, silently dropping them.

**Fix**: The R-type processor now handles CCI by:
- **Outgoing**: wrapping `CciMessagePacket` in `CciPayloadPacket` (adds
  `SystemHeader` with `CCI_MCTP` type) before writing to TCP.
- **Incoming**: recognizing `CCI_MCTP` type → putting on
  `cci_fifo.target_to_host`.

Validated by `test_r_type_cci_wrapping_adds_system_header` and
`test_r_type_incoming_cci_routes_to_target_to_host`.

### 10.3 "IdentifyPbrSwitch reports gae_support_map=0"

**Cause**: Before the fix, `gae_support_map` was left at 0 even when
`enable_pbr=True`.

**Fix**: `cxl_switch.py` L228–229 now explicitly sets:
```python
self._pbr_switch_manager.get_identify_info().gae_support_map = 0x01
```

Validated by `test_pbr_switch_gae_support_map_is_1`.

### 10.4 "GFD has BAR / PCIe config space"

**This is wrong per CXL 4.0 §7.7.13.** The GFD:
- Has NO BAR visible to the host
- Has NO PCIe config space enumerable by the host
- Communicates ONLY via CCI over cci_fifo

If you see any BAR-related code in the GFD, it is from the old (incorrect)
implementation and should be removed. See `test_gfd_no_bar` for assertions.

### 10.5 "Proxy thread never completes"

**Causes**:
1. GFD's `_run_cci_mailbox()` is not running (GFD not started)
2. `CciExecutor` on the GFD side does not have the requested opcode registered
   → returns `UNSUPPORTED`, but the proxy WILL complete
3. `DspCciTunnel._drain_responses()` is not started (tunnel not `start()`ed)
4. cci_fifo is disconnected (TCP connection dropped)

**Debug**: Check `gae_manager.get_proxy_status(tid)` — if `completed=False`
after several seconds, the response is stuck in transit.

### 10.6 "PID assignment does not enable routing"

**By design.** Configure PID Assignment (`0x5704`) only records the PID→target
mapping. You MUST also call Set DRT (`0x5709`) to program the routing table:

```
Step 1: Configure PID Assignment (0x5704) — assigns PID to target
Step 2: Set DRT (0x5709) — programs DRT[drt_index][pid] = {PHYSICAL_PORT, port_number}
Step 3: TLPs with that DPID will now be routed to the specified port
```

### 10.7 Test Mode vs Production Mode

| Aspect | Test Mode | Production Mode |
|--------|-----------|-----------------|
| GFD connection | In-process `CxlConnection()` | TCP via `SwitchConnectionClient` |
| GAE→GFD binding | `set_gfd_executor(executor)` | `set_gfd_tunnel(tunnel)` |
| CCI transport | Direct Queue put/get | TCP → `CxlPacketProcessor` → Queue |
| Created with | `GenericFabricDevice(test_mode=True, cxl_connection=conn)` | `GenericFabricDevice(host="...", port=8000)` |
| GaeCciMailbox | Reads from in-process Queue | Reads from TCP-fed Queue |

### 10.8 Common Import Patterns

```python
# GFD
from opencis.apps.generic_fabric_device import GenericFabricDevice
from opencis.cxl.device.cxl_gfd_device import CxlGfdDevice

# GAE Manager
from opencis.cxl.component.gae_manager import GaeManager, GaeVppbInfo, ProxyThreadEntry
from opencis.cxl.component.gae_cci_mailbox import GaeCciMailbox

# PBR Switch
from opencis.cxl.component.pbr_switch_manager import (
    PbrSwitchManager, DrtEntry, DrtEntryType, PID_UNASSIGNED,
    PidBinding, HmatInfo, PidBindingOperation,
)

# CCI Commands — PBR
from opencis.cxl.cci.fabric_manager.pbr_switch import (
    IdentifyPbrSwitchCommand, IdentifyPbrSwitchResponsePayload,
    ConfigurePidAssignmentCommand, ConfigurePidAssignmentRequestPayload,
    GetPidBindingCommand, GetPidBindingRequestPayload, GetPidBindingResponsePayload,
    ConfigurePidBindingCommand, ConfigurePidBindingRequestPayload,
    GetDrtCommand, GetDrtRequestPayload, GetDrtResponsePayload,
    SetDrtCommand, SetDrtRequestPayload,
)

# CCI Commands — GAE
from opencis.cxl.cci.fabric_manager.gae import (
    IdentifyGaeCommand, IdentifyGaeResponsePayload,
    GetPidAccessVectorsCommand, GetPidAccessVectorsRequestPayload,
    ProxyGfdMgmtCommand, ProxyGfdMgmtRequestPayload, ProxyGfdMgmtResponsePayload,
    GetProxyThreadStatusCommand, GetProxyThreadStatusRequestPayload,
    GetProxyThreadStatusResponsePayload,
    CancelProxyThreadCommand, CancelProxyThreadRequestPayload,
    FabricCrawlOutCommand, FabricCrawlOutRequestPayload,
    FabricCrawlOutResponsePayload,
    EmbeddedCciCommand, EmbeddedCciResponse, DspTunnelRegistry,
)

# CCI Opcodes
from opencis.cxl.cci.common import (
    CCI_FM_API_COMMAND_OPCODE,
    CCI_GAE_COMMAND_OPCODE,
    CCI_RETURN_CODE,
)

# Transport
from opencis.cxl.transport.cci_packets import CciMessagePacket, CciPayloadPacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
```

### 10.9 Quick Reference: Creating a Test GFD + GAE Setup

```python
import asyncio
from opencis.apps.generic_fabric_device import GenericFabricDevice
from opencis.cxl.component.cxl_connection import CxlConnection
from opencis.cxl.component.gae_manager import GaeManager
from opencis.cxl.component.cci_executor import CciExecutor
from opencis.cxl.cci.generic.information_and_status.identify import (
    IdentifyCommand, IdentifyComponentType, IdentifyResponsePayload,
)

# 1. Create GFD with in-process connection
conn = CxlConnection()
gfd = GenericFabricDevice(test_mode=True, cxl_connection=conn, port_index=1)

# 2. Create GAE with in-process executor binding
gae = GaeManager(vppbs=[], label="TestGAE")
gae.set_gfd_executor(gfd.get_gfd_device().get_cci_executor())

# 3. Start GFD
run_task = asyncio.create_task(gfd.run())
await gfd.wait_for_ready()

# 4. Start GFD executor (required for in-process mode)
exec_task = asyncio.create_task(gfd.get_gfd_device().get_cci_executor().run())
await gfd.get_gfd_device().get_cci_executor().wait_for_ready()

# 5. Issue proxy command
tid = await gae.start_proxy(gfd_opcode=0x0001, gfd_request_payload=b"")
await asyncio.sleep(0.1)

# 6. Check result
entry = gae.get_proxy_status(tid)
assert entry.completed
assert entry.return_code == 0  # SUCCESS

# 7. Cleanup
await gfd.stop()
```

---

## Appendix A: Spec Cross-Reference

| This Document | CXL 4.0 Rev 4.0 v1.0 |
|---------------|----------------------|
| GFD device model | §7.7.13 |
| PBR Switch commands | §7.7.13.1–§7.7.13.10 |
| GAE commands | §7.7.14.1–§7.7.14.12 |
| DRT model | §7.7.13.9 (Table 7-131–7-133) |
| PID Assignment | §7.7.13.5 (Table 7-123–7-124) |
| PID Binding | §7.7.13.7 (Table 7-127) |
| Identify PBR Switch | §7.7.13.1 (Table 7-114) |
| Identify GAE | §7.7.14.1 (Table 7-158–7-160) |
| Proxy GFD Mgmt | §7.7.14.10 (Table 7-167) |
| Fabric Crawl Out | §7.7.13.2 (Table 7-116–7-119) |

## Appendix B: File Line Count Summary

| File | Lines |
|------|-------|
| `apps/generic_fabric_device.py` | 151 |
| `apps/cxl_simple_host.py` | 250 |
| `apps/cxl_switch.py` | 340 |
| `device/cxl_gfd_device.py` | 257 |
| `component/gae_cci_mailbox.py` | 196 |
| `component/gae_manager.py` | 331 |
| `component/pbr_switch_manager.py` | 420 |
| `cci/fabric_manager/pbr_switch/__init__.py` | 35 |
| `cci/fabric_manager/pbr_switch/identify_pbr_switch.py` | 117 |
| `cci/fabric_manager/pbr_switch/configure_pid_assignment.py` | 152 |
| `cci/fabric_manager/pbr_switch/get_pid_binding.py` | 156 |
| `cci/fabric_manager/pbr_switch/configure_pid_binding.py` | 165 |
| `cci/fabric_manager/pbr_switch/get_drt.py` | 187 |
| `cci/fabric_manager/pbr_switch/set_drt.py` | 123 |
| `cci/fabric_manager/gae/__init__.py` | 69 |
| `cci/fabric_manager/gae/identify_gae.py` | 158 |
| `cci/fabric_manager/gae/get_pid_access_vectors.py` | 148 |
| `cci/fabric_manager/gae/proxy_gfd_mgmt.py` | 161 |
| `cci/fabric_manager/gae/get_proxy_thread_status.py` | 168 |
| `cci/fabric_manager/gae/cancel_proxy_thread.py` | 87 |
| `cci/fabric_manager/gae/fabric_crawl_out.py` | 282 |
| `tests/test_gfd_device.py` | 246 |
| `tests/test_gae_proxy_management.py` | 644 |
| `tests/test_host_gae_proxy.py` | 470 |
| `tests/test_gae_tcp_integration.py` | 364 |
| **Total** | **~5,700** |

---

*Document generated from source code analysis of all 25 files listed above.
Every class, method, opcode, and payload structure referenced in this document
has been verified against the actual implementation.*
