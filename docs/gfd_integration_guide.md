# GFD Integration Guide — Spec-Correct Implementation
## CXL 4.0 §7.7.13 · opencis-core · June 2026
### Every line number, function, and code path documented

---

## CRITICAL: What Changed and Why

Before this document, `CxlGfdDevice` had a **BAR-0** and a **PCIe config space**,
making it look like a PCIe endpoint. **This violates CXL 4.0 §7.7.13.**

Per spec:
- GFD is a **fabric device** — the host **never** enumerates it via PCIe
- GFD has **NO BAR** — no host-visible MMIO registers
- GFD has **only a CCI mailbox** (via `cci_fifo`) for management
- FM manages GFD via **FabricCrawlOut (0x5701)** → switch → `cci_fifo`
- Host manages GFD via **GAE Proxy (0x5809)** → GAE → `DspCciTunnel` → `cci_fifo`

---

## Architecture (Spec-Correct)

```
                     TCP (port 8100)
FM Process ─────────────────────────────────────────────────────►
                     MctpConnectionManager
                          │
                          │ MCTP
                          │
                    MctpCciExecutor
                   ┌──────┴──────────────────────────────────┐
                   │  Switch Process                          │
                   │                                         │
                   │  PbrSwitchManager (DRT, PID, bindings)  │
                   │  GaeManager (proxy threads)             │
                   │                                         │
                   │  DspCciTunnel                           │
                   │    cci_fifo.host_to_target ────────────►│
                   └─────────────────────────────────────────┘
                                                              │
                                                   TCP (port 8000)
                                                              │
                   ┌─────────────────────────────────────────┘
                   │  GFD Process (spec-correct)              │
                   │                                         │
                   │  CxlGfdDevice._run_cci_mailbox()         │
                   │    ◄── cci_fifo.host_to_target           │
                   │    CciExecutor.execute_command()         │
                   │    ──► cci_fifo.target_to_host           │
                   │                                         │
                   │  NO BAR. NO PCIe config space.          │
                   │  NOT enumerable by host.                 │
                   └─────────────────────────────────────────┘
```

---

## File Reference

| File | Role | Key Classes |
|------|------|-------------|
| [`cxl_gfd_device.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/device/cxl_gfd_device.py) | GFD device (spec-correct) | `CxlGfdDevice` |
| [`generic_fabric_device.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/apps/generic_fabric_device.py) | App-level wrapper | `GenericFabricDevice` |
| [`switch_connection_client.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/switch_connection_client.py) | TCP client to switch | `SwitchConnectionClient` |
| [`cxl_connection.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/cxl_connection.py) | FIFO bundle | `CxlConnection` |
| [`dsp_cci_tunnel.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/dsp_cci_tunnel.py) | Switch→GFD CCI tunnel | `DspCciTunnel` |
| [`mctp_cci_executor.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/mctp/mctp_cci_executor.py) | Switch-side MCTP dispatcher | `MctpCciExecutor` |
| [`fabric_crawl_out.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/fabric_crawl_out.py) | FM→GFD CCI tunnel command | `FabricCrawlOutCommand` |
| [`gae_manager.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/gae_manager.py) | GAE proxy state | `GaeManager` |
| [`cci_packets.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/transport/cci_packets.py) | CCI packet types | `CciMessagePacket`, `CciPayloadPacket` |
| [`test_gfd_device.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/tests/test_gfd_device.py) | Unit tests | 6 test functions |

---

## Part 1: CxlGfdDevice — Line by Line

### File: [`cxl_gfd_device.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/device/cxl_gfd_device.py)

#### Imports (lines 1–30)

```python
# REMOVED (wrong for GFD):
# from opencis.cxl.component.cxl_io_manager import CxlIoManager
# from opencis.cxl.component.cxl_mem_manager import CxlMemManager
# from opencis.cxl.mmio.gfd_mmio_registers import GfdMmioRegisters, GFD_BAR_SIZE
# from opencis.cxl.config_space.device import CxlType3SldConfigSpace, ...
# from opencis.pci.component.mmio_manager import BarEntry, BarInfo, MEMORY_TYPE

# KEPT (correct for CCI-only GFD):
from opencis.cxl.component.cci_executor import CciExecutor, CciRequest, CciResponse
from opencis.cxl.cci.common import CCI_RETURN_CODE
from opencis.cxl.cci.generic.information_and_status.identify import (
    IdentifyCommand, IdentifyComponentType, IdentifyResponsePayload,
)
from opencis.cxl.transport.cci_packets import CciMessagePacket
from opencis.cxl.transport.packet_constants import CCI_MCTP_MESSAGE_CATEGORY
```

#### `__init__` (lines ~58–95)

```python
def __init__(
    self,
    transport_connection: CxlConnection,   # full CxlConnection bundle
    port_index: int = 0,
    serial_number: str = "0000000000000001",
    label: Optional[str] = None,
):
    # Only cci_fifo is used — mmio_fifo and cfg_fifo are intentionally ignored
    self._cci_fifo = transport_connection.cci_fifo   # ← ONLY fifo used

    # CCI executor dispatches incoming commands to registered handlers
    self._cci_executor = CciExecutor(label=label)

    # Register Identify command (component_type=GFD=0x04)
    self._register_cci_commands()
```

**What was removed from `__init__`:**

| Removed Code | Why |
|-------------|-----|
| `self._gfd_registers = GfdMmioRegisters()` | GFD has no BAR |
| `CxlIoManager(mmio_fifo=..., cfg_fifo=...)` | GFD is not PCIe endpoint |
| `CxlMemManager(upstream_fifo=cxl_mem_fifo)` | GFD is IO-only (no CXL.mem) |
| `_init_device` callback | Set up BAR and config space (both wrong) |

#### `_register_cci_commands` (lines ~98–113)

```python
def _register_cci_commands(self) -> None:
    serial_int = int(self._serial_number, 16) if self._serial_number else 1
    identity = IdentifyResponsePayload(
        vendor_id=EEUM_VID,
        device_id=SW_GFD_DID,
        sub_system_vendor_id=EEUM_VID,
        sub_system_id=0,
        serial_number=serial_int,
        max_supported_msg_size=10,
        component_type=IdentifyComponentType.GFD,   # ← 0x04
    )
    self._cci_executor.register_command(
        IdentifyCommand.OPCODE,     # ← 0x0001
        IdentifyCommand(identity, label=self._label),
    )
```

**Previously** this code lived inside `_init_device()` which was called by `CxlIoManager`.
**Now** it is called directly from `__init__`, removing the dependency on `CxlIoManager`.

#### `_run_cci_mailbox` (lines ~116–170) — THE KEY NEW FUNCTION

This is the core new function that connects GFD's `CciExecutor` to the `cci_fifo`.

```python
async def _run_cci_mailbox(self) -> None:
    while True:
        # Step 1: Read from cci_fifo.host_to_target
        #   Put there by: DspCciTunnel.send_and_wait() (line 130 of dsp_cci_tunnel.py)
        #   or: MctpCciExecutor._process_incoming_requests() (line 219 of mctp_cci_executor.py)
        packet = await self._cci_fifo.host_to_target.get()

        if packet is None:
            break  # sentinel from _stop()

        # Step 2: Extract CciMessagePacket (handles both DspCciTunnel and MctpCciExecutor formats)
        if isinstance(packet, CciMessagePacket):
            cci_msg = packet
        elif hasattr(packet, "get_cci_message"):
            cci_msg = packet.get_cci_message()
        else:
            cci_msg = CciMessagePacket(bytearray(bytes(packet)))

        # Step 3: Extract fields from message header
        opcode  = cci_msg.cci_msg_header.command_opcode   # e.g. 0x0001 (Identify)
        tag     = cci_msg.cci_msg_header.message_tag       # must be echoed in response
        payload = cci_msg.get_payload()                    # request payload bytes

        # Step 4: Dispatch to CciExecutor
        request = CciRequest(opcode=opcode, payload=payload)
        response: CciResponse = await self._cci_executor.execute_command(request)

        # Step 5: Build response CciMessagePacket
        #   Must echo: same opcode, same tag, correct return_code
        resp_msg = CciMessagePacket.create(
            data=response.payload or b"",
            message_category=CCI_MCTP_MESSAGE_CATEGORY.RESPONSE,
            opcode=opcode,
            message_tag=tag,                              # ← echoed
            return_code=int(response.return_code),
        )

        # Step 6: Put response on cci_fifo.target_to_host
        #   Read by: DspCciTunnel._drain_responses() (line 174 of dsp_cci_tunnel.py)
        await self._cci_fifo.target_to_host.put(resp_msg)
```

**Why this was missing before:**
The old `CxlGfdDevice` had `CciExecutor` but nothing to feed it from `cci_fifo`.
`DspCciTunnel.send_and_wait()` would put a request on `cci_fifo.host_to_target`
but nothing was reading it → **FabricCrawlOut always timed out**.

#### `_run` (lines ~172–186)

```python
async def _run(self):
    run_tasks = [
        create_task(self._cci_executor.run()),      # runs CciExecutor lifecycle
        create_task(self._run_cci_mailbox()),        # runs CCI packet dispatch loop
    ]
    wait_tasks = [
        create_task(self._cci_executor.wait_for_ready()),
    ]
    await gather(*wait_tasks)
    await self._change_status_to_running()
    await gather(*run_tasks)
```

**Removed from `_run`:**
- `create_task(self._cxl_io_manager.run())`
- `create_task(self._cxl_mem_manager.run())`
- `create_task(self._cxl_io_manager.wait_for_ready())`
- `create_task(self._cxl_mem_manager.wait_for_ready())`

#### `_stop` (lines ~188–192)

```python
async def _stop(self):
    await self._cci_fifo.host_to_target.put(None)  # sentinel to exit mailbox loop
    await self._cci_executor.stop()
```

---

## Part 2: How DspCciTunnel Sends CCI to GFD

### File: [`dsp_cci_tunnel.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/dsp_cci_tunnel.py)

#### `send_and_wait` (lines 106–150) — Switch → GFD

```python
async def send_and_wait(self, request: CciRequest) -> CciResponse:
    tag = await self._alloc_tag()    # allocate 0–255 cycling tag
    fut = asyncio.get_event_loop().create_future()
    self._pending[tag] = fut         # register future to receive response

    # Build CciMessagePacket to put on cci_fifo
    msg = CciMessagePacket.create(
        data=request.payload or b"",
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=request.opcode,
        message_tag=tag,
    )

    # ← GFD's _run_cci_mailbox() reads from here
    await self._cci_fifo.host_to_target.put(msg)    # LINE 130

    # Wait for response from GFD (5 second timeout)
    cci_resp_msg = await asyncio.wait_for(fut, timeout=self._timeout_s)

    rc = CCI_RETURN_CODE(cci_resp_msg.cci_msg_header.return_code)
    payload = cci_resp_msg.get_payload()
    return CciResponse(return_code=rc, payload=payload)
```

#### `_drain_responses` (lines 162–202) — Reads GFD response

```python
async def _drain_responses(self) -> None:
    while True:
        # ← GFD's _run_cci_mailbox() puts response here
        packet = await self._cci_fifo.target_to_host.get()    # LINE 174

        # Extract CciMessagePacket
        if hasattr(packet, "get_cci_message"):
            cci_msg = packet.get_cci_message()
        elif isinstance(packet, CciMessagePacket):
            cci_msg = packet
        else:
            cci_msg = CciMessagePacket(bytearray(bytes(packet)))

        tag = cci_msg.cci_msg_header.message_tag
        fut = self._pending.pop(tag, None)
        if fut is not None:
            fut.set_result(cci_msg)    # ← resolves send_and_wait()'s future
```

**Summary:** `DspCciTunnel` and `CxlGfdDevice._run_cci_mailbox()` form a pair:
- `DspCciTunnel.send_and_wait()` writes to `cci_fifo.host_to_target` (line 130)
- `GFD._run_cci_mailbox()` reads from `cci_fifo.host_to_target` → dispatches
- `GFD._run_cci_mailbox()` writes to `cci_fifo.target_to_host`
- `DspCciTunnel._drain_responses()` reads from `cci_fifo.target_to_host` (line 174)

---

## Part 3: FabricCrawlOut — FM to GFD Path

### File: [`fabric_crawl_out.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/cci/fabric_manager/gae/fabric_crawl_out.py)

#### `_execute` (lines 232–259)

```python
async def _execute(self, request: CciRequest) -> CciResponse:
    # Parse target DSP port and embedded CCI command
    req_payload = FabricCrawlOutRequestPayload.parse(request.payload)

    # Look up DspCciTunnel for target port
    tunnel = self._registry.get(req_payload.target_port)

    # Send embedded CCI command to GFD via tunnel
    cci_request = req_payload.embedded_cmd.to_cci_request()
    gfd_response = await tunnel.send_and_wait(cci_request)   # ← goes to GFD cci_fifo

    # Wrap GFD's response inside FabricCrawlOut response
    embedded = EmbeddedCciResponse.from_cci_response(gfd_response)
    resp_payload = FabricCrawlOutResponsePayload(embedded_resp=embedded)
    response = CciResponse()
    response.payload = resp_payload.dump()
    return response
```

**Full FM → GFD CCI path:**
```
FM calls: api_client.fabric_crawl_out(target_port=1, gfd_opcode=0x0001, payload=b"")
  → MctpCciApiClient.send_command(opcode=0x5701)
  → TCP → MctpConnectionManager
  → MctpCciExecutor._process_incoming_requests()
  → FabricCrawlOutCommand._execute()
  → DspCciTunnel.send_and_wait(CciRequest(opcode=0x0001))  [port=1]
  → cci_fifo.host_to_target.put(CciMessagePacket)          [line 130]
  → GFD._run_cci_mailbox() reads packet
  → CciExecutor.execute_command(CciRequest(opcode=0x0001))
  → IdentifyCommand._execute()
  → cci_fifo.target_to_host.put(CciMessagePacket response)
  → DspCciTunnel._drain_responses() resolves future         [line 174]
  → FabricCrawlOutCommand wraps response
  → TCP → FM
  → MctpCciApiClient returns response
```

---

## Part 4: GAE Proxy — Host to GFD Path

### File: [`gae_manager.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/gae_manager.py)

#### `start_proxy` (lines 175–222)

```python
async def start_proxy(self, gfd_opcode: int, gfd_request_payload: bytes) -> int:
    tid = self._alloc_thread_id()

    async def _run() -> None:
        req = CciRequest(opcode=gfd_opcode, payload=gfd_request_payload)
        if has_tunnel:
            # Production: go through DspCciTunnel → cci_fifo → GFD
            resp = await self._gfd_tunnel.send_and_wait(req)
        else:
            # Test mode: direct in-process CciExecutor call
            resp = await self._gfd_executor.execute_command(req)

    entry.task = asyncio.create_task(_run())
    return tid
```

**Full Host → GFD CCI path (via GAE):**
```
Host writes to GAE BAR (in sim: FM sends CCI via MCTP/TCP)
  → MctpCciExecutor receives opcode 0x5809 (Proxy GFD Mgmt)
  → ProxyGfdMgmtCommand._execute()
  → GaeManager.start_proxy(gfd_opcode, payload)
  → asyncio.create_task(_run())
  → DspCciTunnel.send_and_wait()           [same path as FabricCrawlOut]
  → GFD._run_cci_mailbox() processes
  → response stored in ProxyThreadEntry
Host polls 0x580A (Get Proxy Thread Status)
  → GaeManager.get_proxy_status(thread_id)
  → returns completed=True, gfd_response
```

---

## Part 5: MctpCciExecutor — Switch Side

### File: [`mctp_cci_executor.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/mctp/mctp_cci_executor.py)

#### `__init__` (lines 47–81) — DspCciTunnel creation

```python
def __init__(self, ...):
    # For each DSP port, create a DspCciTunnel wrapping that port's cci_fifo
    self._dsp_tunnels = []
    for port_index, cxl_conn in enumerate(downstream_connections):
        tunnel = DspCciTunnel(
            cci_fifo=cxl_conn.cci_fifo,    # ← this is the GFD's cci_fifo
            port_index=port_index,
        )
        self._dsp_tunnels.append(tunnel)
        self._tunnel_registry.register(port_index, tunnel)

    # FabricCrawlOut always registered — it uses the tunnel registry
    self._cci_executor.register_command(
        FabricCrawlOutCommand.OPCODE,
        FabricCrawlOutCommand(self._tunnel_registry),
    )
```

#### `_process_incoming_requests` (lines 115–226)

Key routing logic:

```python
async def _process_incoming_requests(self):
    while True:
        packet = await self._mctp_connection.controller_to_ep.get()
        ...
        port_index = cci_message.cci_header.port_index

        if port_index != 0:
            # CCI for downstream device (MLD LD-specific opcodes)
            # LINE 219: put on GFD's cci_fifo
            await self._downstream_port_connections[port_index].cci_fifo \
                .host_to_target.put(packet)
        else:
            # CCI for the switch itself (PBR, GAE commands)
            request = self._packet_to_request(cci_message)
            response = await self._cci_executor.execute_command(request)
            await self._send_response(response, ...)
```

**Note:** The `port_index != 0` branch (line 219) puts a `CciRequestPacket` subclass
on `cci_fifo.host_to_target`. `GFD._run_cci_mailbox()` handles this via the
`hasattr(packet, "get_cci_message")` branch.

#### `_process_outcoming_responses` (lines 228–250)

```python
async def _process_outcoming_responses(self, downstream_connection: CxlConnection):
    while True:
        # Read GFD response from cci_fifo.target_to_host  ← GFD puts it here
        packet = await downstream_connection.cci_fifo.target_to_host.get()

        cci_packet = packet.get_cci_message()
        cci_packet_tmc = CciPayloadPacket.create(cci_packet)
        await self._mctp_connection.ep_to_controller.put(cci_packet_tmc)
```

This is for the MLD opcode path (port_index != 0). It reads from `cci_fifo.target_to_host`
and forwards the GFD response back to the FM.

---

## Part 6: CxlConnection — FIFO Map

### File: [`cxl_connection.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/cxl_connection.py) (18 lines)

```python
@dataclass
class CxlConnection(PciConnection):    # line 14
    cxl_mem_fifo: FifoPair             # line 15 — CXL.mem (NOT used by GFD)
    cxl_cache_fifo: FifoPair           # line 16 — CXL.cache (NOT used by GFD)
    cci_fifo: FifoPair                 # line 17 — CCI mailbox (ONLY this)

# Inherited from PciConnection:
    cfg_fifo: FifoPair                 # PCIe config space (NOT used by GFD)
    mmio_fifo: FifoPair                # PCIe MMIO / BAR  (NOT used by GFD)
```

**GFD only uses `cci_fifo`.** The other 4 FIFOs are always created (default_factory)
but are never written to or read from by the spec-correct GFD.

| FIFO | Direction | Used by GFD? | Who uses it? |
|------|-----------|-------------|-------------|
| `cfg_fifo` | host↔device | ❌ No | PCIe endpoint devices (SLD, MLD) |
| `mmio_fifo` | host↔device | ❌ No | PCIe endpoint devices (BAR MMIO) |
| `cxl_mem_fifo` | host↔device | ❌ No | CXL.mem capable devices |
| `cxl_cache_fifo` | host↔device | ❌ No | CXL.cache capable devices |
| `cci_fifo` | switch↔GFD | ✅ **Yes** | **GFD `_run_cci_mailbox()`** |

---

## Part 7: SwitchConnectionClient — TCP Transport

### File: [`switch_connection_client.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/switch_connection_client.py)

#### `__init__` (lines 33–58)

```python
def __init__(self, port_index, component_type, ...):
    self._cxl_connection = CxlConnection()    # line 54: all 5 FIFOs created
```

GFD app uses:
```python
# generic_fabric_device.py line 83–86
self._sw_conn_client = SwitchConnectionClient(
    port_index,
    CXL_COMPONENT_TYPE.D2,    # D2 = generic downstream component
    host=host,
    port=port
)
self._cxl_connection = self._sw_conn_client.get_cxl_connection()
```

#### `_run` (lines 106–160)

```python
async def _run(self):
    # Retry loop → connect → create CxlPacketProcessor
    (reader, writer) = await self._connect()    # line 117

    self._packet_processor = CxlPacketProcessor(
        reader, writer,
        self._cxl_connection,       # ← packet processor routes into all 5 FIFOs
        self._component_type,
        label=f"ClientPort{self._port_index}",
    )                                           # line 149–156

    await self._packet_processor.wait_for_ready()
    await self._change_status_to_running()
    await asyncio.gather(create_task(self._packet_processor.run()))
```

**`CxlPacketProcessor` is unchanged.** It routes incoming TCP packets by packet type
into the correct FIFO. Since GFD never has MMIO/cfg packets arrive, those FIFOs
stay empty. Only `cci_fifo.host_to_target` receives packets (from the switch).

---

## Part 8: Test Guide

### File: [`test_gfd_device.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/tests/test_gfd_device.py)

#### Helper: `_make_gfd` (lines ~40–55)

```python
def _make_gfd(serial_number="0000000000000001"):
    conn = CxlConnection()
    gfd = GenericFabricDevice(
        test_mode=True,
        cxl_connection=conn,    # inject pre-built CxlConnection
        port_index=1,
        serial_number=serial_number,
    )
    return gfd, conn
```

No TCP connection needed in test mode. The `conn.cci_fifo` is used directly.

#### Helper: `_send_cci_request` (lines ~57–76)

```python
async def _send_cci_request(conn, opcode, payload=b"", tag=0):
    req = CciMessagePacket.create(
        data=payload,
        message_category=CCI_MCTP_MESSAGE_CATEGORY.REQUEST,
        opcode=opcode,
        message_tag=tag,
    )
    await conn.cci_fifo.host_to_target.put(req)    # simulates DspCciTunnel
    resp = await asyncio.wait_for(
        conn.cci_fifo.target_to_host.get(),
        timeout=5.0,
    )
    return resp
```

#### Test: `test_gfd_cci_identify`

```python
# Tests the full cci_fifo round-trip for CCI Identify
resp = await _send_cci_request(conn, opcode=IdentifyCommand.OPCODE, tag=42)

assert resp.cci_msg_header.message_tag == 42              # tag echoed
assert resp.cci_msg_header.command_opcode == 0x0001       # opcode echoed
assert resp.cci_msg_header.return_code == CCI_RETURN_CODE.SUCCESS
assert id_resp.component_type == IdentifyComponentType.GFD  # 0x04
```

#### Test: `test_gfd_no_bar`

```python
# Confirms spec compliance: GFD must have NO BAR-related attributes
assert not hasattr(device, "get_bar_size")    # no BAR size
assert not hasattr(device, "get_registers")   # no MMIO registers
assert not hasattr(device, "_gfd_registers")  # no register object
assert not hasattr(device, "_cxl_io_manager") # not a PCIe endpoint
```

### Run tests:
```bash
cd c:\Users\pavan\Desktop\cxl\opencis-core
python -m pytest tests/test_gfd_device.py -v
python -m pytest tests/test_gfd_live_switch.py -v
```

---

## Part 9: What Was Deleted

### `gfd_mmio_registers.py` — DEPRECATED

File: [`gfd_mmio_registers.py`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/mmio/gfd_mmio_registers.py)

This file defined `GfdMmioRegisters` (a 4 KB BAR-0 register block) and
`GFD_BAR_SIZE`. Since GFD has no BAR per spec, this file is no longer imported.
It is kept in the repo but marked as deprecated. It should not be imported by
any production code.

**Previously imported by:**
- `cxl_gfd_device.py` line 41 — **REMOVED**
- `tests/test_gfd_device.py` line 23 — **REMOVED**

---

## Part 10: Commissioning Flow (Unchanged)

The 6-step FM commissioning workflow does **not change** — it sends CCI to the
**switch**, not to the GFD. The switch's `PbrSwitchManager` processes these:

```
Step 1: FM → IdentifyPbrSwitch (0x5700)  → switch MctpCciExecutor → local CciExecutor
Step 2: FM → ConfigurePidAssignment (0x5704) → PbrSwitchManager.assign_pid()
Step 3: FM → SetDrt (0x5709)            → PbrSwitchManager.set_drt()
Step 4: FM → GetPidBinding (0x5705)     → returns 0xFFF (unbound)
Step 5: FM → ConfigurePidBinding (0x5706) → PbrSwitchManager.bind_pid() [background]
Step 6: FM → GetPidBinding (0x5705)     → returns 0x010 (bound)
```

After commissioning, the FM can send CCI to the GFD via FabricCrawlOut:
```
FM → FabricCrawlOut (0x5701, port=1, embedded_opcode=0x0001)
   → DspCciTunnel → cci_fifo → GFD._run_cci_mailbox() → IdentifyCommand
   → response back to FM
```

---

## Part 11: Spec Compliance Status

| Spec Requirement | Status | Implementation |
|-----------------|--------|---------------|
| GFD has NO host-visible BAR | ✅ Fixed | Removed `GfdMmioRegisters`, `CxlIoManager` |
| GFD NOT enumerable by host | ✅ Fixed | No `CxlType3SldConfigSpace` |
| GFD has CCI mailbox via cci_fifo | ✅ Fixed | `_run_cci_mailbox()` added |
| FM can send CCI to GFD via FabricCrawlOut | ✅ Works | `DspCciTunnel` → `cci_fifo` |
| Host can send CCI to GFD via GAE proxy | ✅ Works | `GaeManager` → `DspCciTunnel` |
| GFD responds with component_type=GFD (0x04) | ✅ Works | `IdentifyCommand` registered |
| GAE BAR on switch USP | ⚠️ Sim deviation | Modelled as MCTP/TCP (not real BAR) |
| G-FAM (mem_capable=1) | ❌ Not implemented | Requires major extension |

---

*Document version: 2.0 — June 2026 (spec-correct refactor)*
*Branch: `smbus_dual_port`*
*Spec: CXL 4.0 Rev 1.0 §7.7.13*
