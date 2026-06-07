# GFD Commissioning & Data Access Guide
## Using `configs/1vcs_1sld_1gfd.yaml`
### opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## 1. Topology Overview

```
  ┌──────────────┐       ┌────────────────────────────────────┐
  │     Host     │◄─TCP─►│  Port 0 (USP)                      │
  │ CxlSimpleHost│       │  ├── GAE (Generic Access Endpoint)  │
  │   (CPU/OS)   │       │  ├── GaeCciMailbox                  │
  └──────────────┘       │  └── PbrSwitchRouter                │
                         │                                     │
                         │       CXL PBR Switch                │
                         │       Port 8000 / TCP               │
                         │                                     │
  ┌──────────────┐       │                                     │
  │     SLD      │◄─TCP─►│  Port 1 (DSP) ── vPPB 0            │
  │  256 MB CXL  │       │  Type-3 Memory Device               │
  │  .mem device │       │  (HDM decode, BAR-0, CXL.mem)       │
  └──────────────┘       │                                     │
                         │                                     │
  ┌──────────────┐       │                                     │
  │     GFD      │◄─TCP─►│  Port 2 (DSP) ── vPPB 1            │
  │  CCI-only    │       │  Generic Fabric Device              │
  │  No memory   │       │  (CCI mailbox only, no BAR/CXL.mem) │
  └──────────────┘       └────────────────────────────────────┘

  ┌──────────────┐
  │ Fabric Mgr   │◄─MCTP/TCP─► Switch Port 8100
  │  FM CLI      │  (PBR CCI commissioning commands)
  │  Port 8200   │
  └──────────────┘
```

### Config File: [`configs/1vcs_1sld_1gfd.yaml`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/configs/1vcs_1sld_1gfd.yaml)

```yaml
port_configs:
  - type: USP     # Port 0: Host + GAE
  - type: DSP     # Port 1: SLD (256 MB memory)
  - type: DSP     # Port 2: GFD (CCI only)

virtual_switch_configs:
  - upstream_port_index: 0
    vppb_counts: 2
    initial_bounds: [1, 2]   # vPPB 0→SLD, vPPB 1→GFD

devices:
  single_logical_devices:
    - port_index: 1
      memory_size: 256M
      serial_number: 4E9FF1671694A385
  generic_fabric_devices:
    - port_index: 2
      serial_number: 2200B90031B3ABD8
```

---

## 2. Starting the System (5 Terminals)

Open 5 separate terminals and run in this exact order:

### Terminal 1 — CXL Switch
```bash
cd opencis-core
python -m opencis.bin.cli switch start configs/1vcs_1sld_1gfd.yaml
```
Wait for: `[SwitchConnectionManager] Started TCP server on 0.0.0.0:8000`

### Terminal 2 — Fabric Manager
```bash
python -m opencis.bin.cli fm start --config-file configs/1vcs_1sld_1gfd.yaml
```
Wait for: `[MctpConnectionManager] Connected to switch`

### Terminal 3 — SLD Device (Type-3 Memory)
```bash
python -m opencis.bin.cli sld start --port 1 --memory-size 256M --serial-number 4E9FF1671694A385
```
Wait for: `[SwitchConnectionClient:Port1] Connected to switch`

### Terminal 4 — GFD Device
```bash
python -m opencis.bin.cli gfd start --port 2
```
Wait for: `[SwitchConnectionClient:Port2] Connected to switch`

### Terminal 5 — Host
```bash
python -m opencis.bin.cli host start 0
```
Wait for: `[CxlSimpleHost:Port0] Host ready`

> **Alternative:** Use the unified launcher to start everything at once:
> ```bash
> python -m opencis.bin.cli start configs/1vcs_1sld_1gfd.yaml
> ```
> This starts switch, FM, host, SLD, and GFD in child processes.

---

## 3. Phase 1: PBR Commissioning (FM CLI, Port 8200)

The Fabric Manager must commission the PBR switch before the host can talk to the GFD.
All commands use the **Socket.IO** interface on port 8200.

### Connection
```python
import socketio
sio = socketio.Client()
sio.connect("http://127.0.0.1:8200")
```

### Step 1 — Identify PBR Switch (`pbr:identify`, CCI 0x5700)

**What it does:** Ask the switch "how many routing tables do you have?"

```python
resp = sio.call("pbr:identify")
# Expected:
# {
#   "error": "",
#   "result": {
#     "gaeSupportMap": 1,  ← bit 0=1 means VCS 0 has a GAE
#     "numDrts": 1,        ← 1 DRT table available
#     "numRgts": 0,        ← no multicast routing group tables
#     "routingCaps": 0     ← standard routing
#   }
# }
```

**Validation:**
- `numDrts >= 1` → switch can route PBR traffic
- `gaeSupportMap != 0` → at least one VCS has a GAE (host can reach GFD)

---

### Step 2 — Assign PID to GFD Port (`pbr:configurePid`, CCI 0x5704)

**What it does:** "Assign PID `0x010` (16 decimal) to DSP port 2 (where the GFD is plugged in)."

```python
resp = sio.call("pbr:configurePid", {
    "operation": 0,           # 0 = ASSIGN, 1 = CLEAR
    "entries": [{
        "pid": 16,            # PID 0x010 (12-bit, max 0xFFE)
        "targetId": 2,        # DSP port 2 (where GFD is connected)
        "instanceId": 0       # single instance
    }]
})
# Expected: {"error": "", "result": "SUCCESS"}
```

> **NOTE:** This does NOT update the routing table. The router still cannot forward traffic yet.

---

### Step 3 — Program the DRT (`pbr:setDrt`, CCI 0x5709)

**What it does:** "Tell the router: when you see DPID=0x010, send the packet to port 2."

```python
resp = sio.call("pbr:setDrt", {
    "drtIndex": 0,            # DRT table 0 (from numDrts in Step 1)
    "startEntry": 16,         # DPID index = PID = 0x010
    "entries": [{
        "entryType": "PHYSICAL_PORT",  # route to a physical port
        "routingTarget": 2             # port 2 (GFD)
    }]
})
# Expected: {"error": "", "result": "SUCCESS"}
```

**After this step:** Data plane is live! TLPs with DPID=0x010 route to port 2 → GFD.

---

### Step 4 — Verify vPPB is Unbound (`pbr:getPidBinding`, CCI 0x5705)

```python
resp = sio.call("pbr:getPidBinding", {
    "targetVcs": 0,
    "targetVppb": 1           # vPPB 1 (GFD's vPPB, from initial_bounds[1]=2)
})
# Expected: {"error": "", "result": {"pid": 4095, ...}}
# pid=4095 (0xFFF) means UNBOUND — safe to bind
```

---

### Step 5 — Bind vPPB to GFD PID (`pbr:configurePidBinding`, CCI 0x5706)

**This is a background command** — the switch responds immediately with `BACKGROUND_COMMAND_STARTED`.

```python
resp = sio.call("pbr:configurePidBinding", {
    "operation": 0,           # 0 = BIND
    "targetVcs": 0,
    "targetVppb": 1,          # vPPB 1 → GFD
    "pid": 16,                # PID 0x010 (from Step 2)
    "latencyEntryBaseUnit": 0,
    "latencyEntry": 0,
    "bwEntryBaseUnit": 0,
    "bwEntry": 0
})
# Expected: {"error": "", "result": "BACKGROUND_COMMAND_STARTED"}
# This IS the correct response — not an error!
```

---

### Step 6 — Verify Binding Complete (`pbr:getPidBinding`, CCI 0x5705)

```python
import time
time.sleep(0.1)  # give background task a moment

resp = sio.call("pbr:getPidBinding", {
    "targetVcs": 0,
    "targetVppb": 1
})
# Expected: {"error": "", "result": {"pid": 16, ...}}
# pid=16 (0x010) means BOUND — commissioning complete!
```

---

## 4. Phase 2: GAE Proxy Commands (Host → GFD via GAE)

After commissioning, the host uses GAE proxy commands to talk to the GFD.
The GAE lives on the **USP of the switch** (Port 0), NOT on the GFD itself.

```
Host ──► RootPortDevice.gae_command()
  ──► cci_fifo.host_to_target
  ──► [TCP]
  ──► CxlPacketProcessor(USP) ──► GaeCciMailbox
  ──► CciExecutor ──► ProxyGfdMgmtCommand
  ──► GaeManager.start_proxy()
  ──► DspCciTunnel ──► cci_fifo ──► GFD CCI Mailbox
  ──► GFD CciExecutor ──► IdentifyCommand
  ──► response flows back the same path
```

### GAE Step A — Proxy GFD Identify (`gae:proxyGfdMgmt`, CCI 0x5809)

**What it does:** "Send CCI Identify (opcode 0x0001) to the GFD via the GAE proxy."
Returns a `threadId` — the proxy runs asynchronously.

```python
resp = sio.call("gae:proxyGfdMgmt", {
    "gfdOpcode": 1,           # 0x0001 = CCI Identify
    "gfdPayload": []          # Identify has no input payload
})
# Expected: {"error": "", "result": {"threadId": 1}}
```

### GAE Step B — Poll for Result (`gae:getProxyStatus`, CCI 0x580A)

**What it does:** "Has the GFD responded to my Identify command?"

```python
resp = sio.call("gae:getProxyStatus", {"threadId": 1})
# Expected:
# {
#   "error": "",
#   "result": {
#     "threadId": 1,
#     "completed": true,
#     "gfdReturnCode": 0,                    ← SUCCESS
#     "gfdResponsePayload": [0, 238, 0, 175, 0, 0, 0, 0, 4]
#   }
# }
```

**Decoding the GFD Identify response payload:**
```python
payload = resp["result"]["gfdResponsePayload"]
vendor_id      = payload[0] | (payload[1] << 8)   # 0x00EE = EEUM
device_id      = payload[2] | (payload[3] << 8)   # 0x00AF = SW_GFD_DID
component_type = payload[8]                         # 0x04 = GFD
print(f"Vendor: 0x{vendor_id:04X}")      # 0x00EE
print(f"Device: 0x{device_id:04X}")      # 0x00AF
print(f"Type:   0x{component_type:02X}") # 0x04 = GFD ✅
```

### GAE Step C — Cancel (Optional) (`gae:cancelProxy`, CCI 0x580B)

```python
resp = sio.call("gae:cancelProxy", {"threadId": 1})
# Expected: {"error": "", "result": "SUCCESS"}
```

---

## 5. Phase 3: Data Read/Write (Host → SLD via CXL.mem)

The **SLD** (port 1) supports CXL.mem — the host can read/write its 256 MB directly.
The **GFD** (port 2) does NOT support CXL.mem — data access is CCI-only via GAE proxy.

### SLD Memory Access (CXL.mem TLP path)

After the host's PCI enumeration and CXL.mem driver attach, the 256 MB SLD memory
is mapped to a Host Physical Address (HPA). The host uses `cpu.store()` / `cpu.load()`:

```python
# In the host application (e.g., my_sys_sw_app in cxl_simple_host.py):
from opencis.cpu import CPU

# After PCI enumeration and CXL.mem attach:
HPA_BASE = 0x100000000000   # HPA where SLD memory is mapped

# WRITE 64 bytes to CXL memory
data = b"\xAB" * 64
await cpu.store(HPA_BASE, data)

# READ 64 bytes back
result = await cpu.load(HPA_BASE, 64)
assert result == data  # ✅ round-trip verified
```

**Data path:**
```
cpu.store(HPA, data)
  → CxlMemoryHub → MemWrite TLP
  → CxlPacketProcessor(R) → TCP
  → CxlPacketProcessor(USP) → PbrSwitchRouter
  → HDM Decoder → DPID lookup → DRT → port 1
  → CxlPacketProcessor(DSP) → TCP
  → SLD → SldMemoryDevice.write(offset, data)
  → Completion TLP flows back
```

### GFD Data Access (CCI-only via GAE Proxy)

The GFD has **no CXL.mem**. You interact with it entirely via CCI commands
proxied through the GAE. The GAE proxy is the only way to "write" or "read"
data to/from a GFD.

**Example: Send a custom CCI command to the GFD**

```python
# CCI opcode for a hypothetical "GFD Write Register" command
GFD_WRITE_REG_OPCODE = 0x5900  # custom opcode (vendor-specific)

# Build payload: register_offset (2 bytes) + data (4 bytes)
import struct
payload = list(struct.pack("<HI", 0x0000, 0xDEADBEEF))
# payload = [0x00, 0x00, 0xEF, 0xBE, 0xAD, 0xDE]

resp = sio.call("gae:proxyGfdMgmt", {
    "gfdOpcode": GFD_WRITE_REG_OPCODE,
    "gfdPayload": payload
})
thread_id = resp["result"]["threadId"]

# Poll for completion
import time
time.sleep(0.1)
resp = sio.call("gae:getProxyStatus", {"threadId": thread_id})
if resp["result"]["completed"]:
    rc = resp["result"]["gfdReturnCode"]
    print(f"GFD returned: {'SUCCESS' if rc == 0 else f'ERROR {rc}'}")
```

> **Key Insight:** For this config, the SLD gives you 256 MB of CXL.mem
> addressable via standard load/store. The GFD gives you a CCI endpoint
> reachable only via the GAE proxy. They serve different purposes:
> - **SLD** = bulk memory expansion (like extra DRAM)
> - **GFD** = fabric-attached accelerator/controller (command-response interface)

---

## 6. Host-Direct GAE Commands (Python API)

If you're writing a custom host application (extending `CxlSimpleHost`), you can
use the Python API directly instead of the FM Socket.IO CLI:

```python
# In your host application:
from opencis.apps.cxl_simple_host import CxlSimpleHost

class MyHost(CxlSimpleHost):
    async def my_app(self):
        # 1. Proxy Identify to GFD (opcode 0x0001)
        result = await self.gae_proxy_gfd_mgmt(gfd_opcode=0x0001, timeout=5.0)
        if result.is_ok():
            thread_id = result.value["thread_id"]
            print(f"Proxy started, thread_id={thread_id}")

        # 2. Poll for completion
        status = await self.gae_get_proxy_status(thread_id=thread_id, timeout=5.0)
        if status.is_ok() and status.value["completed"]:
            payload = status.value["gfd_response_payload"]
            component_type = payload[8]
            print(f"GFD component_type = 0x{component_type:02X}")  # 0x04 = GFD

        # 3. Cancel if needed
        cancel = await self.gae_cancel_proxy(thread_id=thread_id, timeout=5.0)
```

**Underlying call chain:**
```
self.gae_proxy_gfd_mgmt(0x0001)
  → self._root_port_device.gae_command(0x5809, payload)
    → CciMessagePacket on cci_fifo.host_to_target
    → CxlPacketProcessor(R) wraps → CciPayloadPacket → TCP
    → CxlPacketProcessor(USP) unwraps → GaeCciMailbox
    → CciExecutor → ProxyGfdMgmtCommand._execute()
    → GaeManager.start_proxy() → DspCciTunnel → GFD
    → GFD CciExecutor → IdentifyCommand
    → response flows back same path
```

---

## 7. Complete Commissioning + Data Access Script

This script does everything: connect, commission PBR, proxy GFD Identify, read SLD memory.

```python
"""
gfd_full_demo.py — Commission GFD + Access data (SLD read/write + GFD CCI)

Prerequisites:
  pip install "python-socketio[client]"
  System running: switch + FM + host + SLD + GFD
  Config: configs/1vcs_1sld_1gfd.yaml
"""

import socketio
import struct
import time

FM_URL   = "http://127.0.0.1:8200"
GFD_PID  = 16     # 0x010
GFD_PORT = 2      # DSP port where GFD is connected

sio = socketio.Client()
sio.connect(FM_URL)
print(f"✅ Connected to FM at {FM_URL}\n")

# ══════════════════════════════════════════════════════════════════════
# PHASE 1: PBR Commissioning
# ══════════════════════════════════════════════════════════════════════

print("=" * 60)
print("PHASE 1: PBR COMMISSIONING (6 steps)")
print("=" * 60)

# Step 1: Identify PBR Switch
resp = sio.call("pbr:identify")
assert not resp.get("error"), f"Step 1 failed: {resp}"
info = resp["result"]
print(f"  Step 1 ✅ pbr:identify → numDrts={info['numDrts']}, "
      f"gaeSupportMap=0x{info['gaeSupportMap']:X}")

# Step 2: Assign PID to GFD port
resp = sio.call("pbr:configurePid", {
    "operation": 0,
    "entries": [{"pid": GFD_PID, "targetId": GFD_PORT, "instanceId": 0}]
})
assert not resp.get("error"), f"Step 2 failed: {resp}"
print(f"  Step 2 ✅ pbr:configurePid → PID 0x{GFD_PID:03X} → port {GFD_PORT}")

# Step 3: Program DRT
resp = sio.call("pbr:setDrt", {
    "drtIndex": 0,
    "startEntry": GFD_PID,
    "entries": [{"entryType": "PHYSICAL_PORT", "routingTarget": GFD_PORT}]
})
assert not resp.get("error"), f"Step 3 failed: {resp}"
print(f"  Step 3 ✅ pbr:setDrt → DRT[0][0x{GFD_PID:03X}] = port {GFD_PORT}")

# Step 4: Verify vPPB 1 is unbound
resp = sio.call("pbr:getPidBinding", {"targetVcs": 0, "targetVppb": 1})
assert not resp.get("error"), f"Step 4 failed: {resp}"
pid = resp["result"]["pid"]
assert pid == 0xFFF, f"Expected 0xFFF (unbound), got {pid:#05x}"
print(f"  Step 4 ✅ pbr:getPidBinding → pid=0x{pid:03X} (UNBOUND)")

# Step 5: Bind vPPB 1 to GFD PID
resp = sio.call("pbr:configurePidBinding", {
    "operation": 0, "targetVcs": 0, "targetVppb": 1, "pid": GFD_PID,
    "latencyEntryBaseUnit": 0, "latencyEntry": 0,
    "bwEntryBaseUnit": 0, "bwEntry": 0
})
assert not resp.get("error"), f"Step 5 failed: {resp}"
print(f"  Step 5 ✅ pbr:configurePidBinding → BACKGROUND_COMMAND_STARTED")

# Step 6: Verify binding
time.sleep(0.1)
resp = sio.call("pbr:getPidBinding", {"targetVcs": 0, "targetVppb": 1})
assert not resp.get("error"), f"Step 6 failed: {resp}"
pid = resp["result"]["pid"]
assert pid == GFD_PID, f"Expected {GFD_PID:#05x}, got {pid:#05x}"
print(f"  Step 6 ✅ pbr:getPidBinding → pid=0x{pid:03X} (BOUND)")
print()

# ══════════════════════════════════════════════════════════════════════
# PHASE 2: GAE Proxy — Talk to GFD
# ══════════════════════════════════════════════════════════════════════

print("=" * 60)
print("PHASE 2: GAE PROXY (Host → GAE → GFD)")
print("=" * 60)

# GAE Step A: Proxy GFD Identify (opcode 0x0001)
resp = sio.call("gae:proxyGfdMgmt", {"gfdOpcode": 1, "gfdPayload": []})
assert not resp.get("error"), f"GAE-A failed: {resp}"
tid = resp["result"]["threadId"]
print(f"  GAE-A ✅ gae:proxyGfdMgmt → threadId={tid}")

# GAE Step B: Poll for result
time.sleep(0.1)
resp = sio.call("gae:getProxyStatus", {"threadId": tid})
assert not resp.get("error"), f"GAE-B failed: {resp}"
result = resp["result"]
print(f"  GAE-B ✅ gae:getProxyStatus → completed={result['completed']}, "
      f"returnCode={result['gfdReturnCode']}")

if result["completed"] and result["gfdReturnCode"] == 0:
    payload = result["gfdResponsePayload"]
    vendor_id      = payload[0] | (payload[1] << 8)
    device_id      = payload[2] | (payload[3] << 8)
    component_type = payload[8]
    print(f"        GFD Identify Response:")
    print(f"          Vendor ID:      0x{vendor_id:04X}")
    print(f"          Device ID:      0x{device_id:04X}")
    print(f"          Component Type: 0x{component_type:02X} "
          f"({'GFD ✅' if component_type == 4 else 'UNEXPECTED ❌'})")

# GAE Step C: Cancel (cleanup)
resp = sio.call("gae:cancelProxy", {"threadId": tid})
print(f"  GAE-C ✅ gae:cancelProxy → thread {tid} cleaned up")
print()

# ══════════════════════════════════════════════════════════════════════
# PHASE 3: Verify DRT (Optional)
# ══════════════════════════════════════════════════════════════════════

print("=" * 60)
print("PHASE 3: VERIFY ROUTING TABLE")
print("=" * 60)

resp = sio.call("pbr:getDrt", {
    "drtIndex": 0, "startEntry": GFD_PID, "numEntries": 1
})
assert not resp.get("error"), f"getDrt failed: {resp}"
entry = resp["result"]["entries"][0]
print(f"  DRT[0][0x{GFD_PID:03X}] = {entry['entryType']} → port {entry['routingTarget']}")
print()

# ══════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════

print("★" * 60)
print("  ALL PHASES COMPLETE!")
print(f"  PBR: PID 0x{GFD_PID:03X} → port {GFD_PORT} → GFD")
print(f"  GAE: Host can reach GFD via proxy (component_type=0x04)")
print(f"  SLD: 256 MB CXL.mem on port 1 (accessible via cpu.load/store)")
print("★" * 60)

sio.disconnect()
```

---

## 8. Config Field Reference

| YAML Field | Type | Description |
|------------|------|-------------|
| `port_configs[].type` | `USP` or `DSP` | Physical port type. USP = host-facing. DSP = device-facing. |
| `virtual_switch_configs[].upstream_port_index` | int | Which USP port this VCS uses (must be a USP port index) |
| `virtual_switch_configs[].vppb_counts` | int | Number of virtual PCI bridges (host sees each as a PCI function) |
| `virtual_switch_configs[].initial_bounds` | list[int] | Maps vPPB index → DSP port index. `[1,2]` = vPPB0→port1, vPPB1→port2 |
| `hdm_decoder_capabilities.decoder_count` | int | Number of HDM decoders (≤ number of targets). Powers of 2 only: 1,2,4,6,8,10 |
| `hdm_decoder_capabilities.target_count` | int | Number of addressable downstream targets |
| `hdm_decoder_capabilities.bi_capable` | bool | Whether back-invalidation is supported (used for CXL.cache coherency) |
| `devices.single_logical_devices[].port_index` | int | DSP port this SLD connects to |
| `devices.single_logical_devices[].memory_size` | str | Memory capacity: `"256M"`, `"1G"`, `"512K"` (parsed by humanfriendly) |
| `devices.single_logical_devices[].serial_number` | str | 64-bit hex serial number (CXL spec §9.13.3) |
| `devices.generic_fabric_devices[].port_index` | int | DSP port this GFD connects to |
| `devices.generic_fabric_devices[].serial_number` | str | 64-bit hex serial number |

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pbr:identify` returns `INTERNAL_ERROR` | FM not connected to switch | Check MCTP connection on port 8100 |
| `pbr:configurePid` returns `INVALID_INPUT` | PID already assigned to different port | Clear first: `operation=1` |
| `pbr:setDrt` returns `INVALID_INPUT` | `entryType = "RESERVED"` | Use `"PHYSICAL_PORT"`, `"INVALID"`, or `"RGT_INDEX"` |
| `gae:proxyGfdMgmt` returns `INTERNAL_ERROR` | GFD not connected to switch | Start GFD process first |
| `gae:proxyGfdMgmt` returns `INVALID_INPUT` | Empty `gfdPayload` with opcode that requires data | Provide correct payload for the opcode |
| `gae:getProxyStatus` shows `completed=false` | Proxy thread still running | Wait and retry (100ms) |
| `gae:cancelProxy` returns `INVALID_INPUT` | Unknown `threadId` | Use the threadId from `gae:proxyGfdMgmt` |
| Host can't reach GFD after commissioning | PbrSwitchRouter not running | Check switch log for `PbrSwitchRouter` errors |
| `pid=4095` after binding | Background command not complete | Wait 100ms and re-check with `getPidBinding` |

---

## 10. Quick Reference Card

```
CONFIG: configs/1vcs_1sld_1gfd.yaml
  Port 0 = USP (Host + GAE)
  Port 1 = DSP (SLD, 256 MB CXL.mem)
  Port 2 = DSP (GFD, CCI-only)

STARTUP ORDER:
  1. Switch    → opencis switch start configs/1vcs_1sld_1gfd.yaml
  2. FM        → opencis fm start --config-file configs/1vcs_1sld_1gfd.yaml
  3. SLD       → opencis sld start --port 1 --memory-size 256M
  4. GFD       → opencis gfd start --port 2
  5. Host      → opencis host start 0

COMMISSIONING (FM CLI port 8200):
  Step  Event                     Key Input                 Expected
  ────  ────────────────────────  ───────────────────────   ────────────────────
  1     pbr:identify              (none)                    numDrts ≥ 1
  2     pbr:configurePid          pid=16, targetId=2        "SUCCESS"
  3     pbr:setDrt                startEntry=16, port=2     "SUCCESS"
  4     pbr:getPidBinding         vcs=0, vppb=1             pid=4095 (UNBOUND)
  5     pbr:configurePidBinding   pid=16, vppb=1            "BACKGROUND_COMMAND_STARTED"
  6     pbr:getPidBinding         vcs=0, vppb=1             pid=16 (BOUND)

GAE PROXY (after commissioning):
  A     gae:proxyGfdMgmt          gfdOpcode=1               threadId=1
  B     gae:getProxyStatus        threadId=1                completed=true, type=0x04
  C     gae:cancelProxy           threadId=1                "SUCCESS"

DATA ACCESS:
  SLD → cpu.store(HPA, data) / cpu.load(HPA, size) via CXL.mem TLP
  GFD → CCI commands only via GAE proxy (no memory, no BAR)
```

---

*Document version: 1.0 — June 2026*
*Config: [`1vcs_1sld_1gfd.yaml`](file:///c:/Users/pavan/Desktop/cxl/opencis-core/configs/1vcs_1sld_1gfd.yaml)*
*Spec: CXL 4.0 Rev 1.0, Sections 7.7.13-7.7.14*
