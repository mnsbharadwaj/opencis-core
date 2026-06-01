# GFD Commissioning via FM CLI Commands
### Network Engineer's Reference Guide
### opencis-core · CXL 4.0 Rev 1.0 · June 2026

---

## Overview

The **FM CLI** is exposed as a **Socket.IO server** running on **port 8200** of the Fabric Manager.
Every PBR and GAE command is a Socket.IO *event* you emit with a JSON payload.
The server responds with a JSON object:

```json
{
  "error": "",        // empty string = success; non-empty = error name
  "result": { ... }   // command-specific response fields
}
```

### How to Connect

```python
# Python client (using python-socketio)
import socketio
sio = socketio.Client()
sio.connect("http://127.0.0.1:8200")

# OR from CLI using wscat / socket.io-client
# npx socket.io-client http://127.0.0.1:8200
```

### Command Map (Socket.IO event → CCI opcode)

| Socket.IO Event          | CCI Opcode | Description                    |
|--------------------------|-----------|--------------------------------|
| `pbr:identify`           | `0x5700`  | Identify PBR Switch            |
| `pbr:configurePid`       | `0x5704`  | Configure PID Assignment       |
| `pbr:getPidBinding`      | `0x5705`  | Get PID Binding                |
| `pbr:configurePidBinding`| `0x5706`  | Configure PID Binding (bg)     |
| `pbr:getDrt`             | `0x5708`  | Get DRT                        |
| `pbr:setDrt`             | `0x5709`  | Set DRT                        |
| `gae:identify`           | `0x5800`  | Identify GAE                   |
| `gae:getPidAccessVectors`| `0x5802`  | Get PID Access Vectors         |
| `gae:proxyGfdMgmt`       | `0x5809`  | Proxy GFD Management Command   |
| `gae:getProxyStatus`     | `0x580A`  | Get Proxy Thread Status        |
| `gae:cancelProxy`        | `0x580B`  | Cancel Proxy Thread            |

---

## GFD Commissioning Sequence — All 6 Steps

```
Network Engineer runs these FM CLI commands in order to bring up a GFD device:

  STEP 1  pbr:identify           -- "how many DRTs does the switch have?"
  STEP 2  pbr:configurePid       -- "assign PID 0x010 to DSP port 1"
  STEP 3  pbr:setDrt             -- "tell the router: DPID 0x010 → port 1"
  STEP 4  pbr:getPidBinding      -- "is vPPB 0 free? (expect pid=0xFFF)"
  STEP 5  pbr:configurePidBinding -- "bind vPPB 0 to PID 0x010"
  STEP 6  pbr:getPidBinding      -- "confirm it is bound (expect pid=0x010)"
```

---

## STEP 1 — Identify PBR Switch (`pbr:identify`)

### Purpose
Ask the switch: *"What are your capabilities? How many routing tables do you have?"*
This is always the first command. `numDrts` must be ≥ 1 before you can proceed.

### Socket.IO Event
```
Event: "pbr:identify"
```

### Input (no payload required)
```json
{}
```
or simply emit with no data argument.

### CLI Invocation
```python
response = sio.call("pbr:identify")
print(response)
```

### Expected Output
```json
{
  "error": "",
  "result": {
    "gaeSupportMap": 1,
    "numDrts": 1,
    "numRgts": 0,
    "routingCaps": 0
  }
}
```

### Output Field Descriptions
| Field          | Example Value | Meaning                                         |
|----------------|---------------|-------------------------------------------------|
| `gaeSupportMap`| `1`           | Bitmask: bit N=1 means VCS N has a GAE. `1` = VCS 0 has a GAE |
| `numDrts`      | `1`           | Number of DRT (DPID Routing Tables). Must be ≥ 1 |
| `numRgts`      | `0`           | Number of Routing Group Tables (multicast, unused for simple GFD) |
| `routingCaps`  | `0`           | Routing capability flags (0 = standard)         |

### Validation
```
✓  numDrts >= 1         -- switch can route PBR traffic
✓  gaeSupportMap != 0   -- at least one VCS has a GAE (host can manage GFD)
✗  error != ""          -- switch not ready; check MCTP connection
```

---

## STEP 2 — Configure PID Assignment (`pbr:configurePid`)

### Purpose
*"Assign PID `0x010` to physical port 1 (where the GFD is plugged in)."*
This tells the switch: "Port 1 will be known as DPID 0x010 on the fabric."

> **NOTE:** This does NOT update the DRT. The router still cannot forward traffic yet.
> You MUST run `pbr:setDrt` (Step 3) after this.

### Socket.IO Event
```
Event: "pbr:configurePid"
```

### Input Payload
```json
{
  "operation": 0,
  "entries": [
    {
      "pid": 16,
      "targetId": 1,
      "instanceId": 0
    }
  ]
}
```

### Input Field Descriptions
| Field              | Value | Meaning                                                  |
|--------------------|-------|----------------------------------------------------------|
| `operation`        | `0`   | `0` = ASSIGN a PID, `1` = CLEAR (remove) a PID          |
| `entries[].pid`    | `16`  | PID to assign. `16` decimal = `0x010` hex (12-bit, max 0xFFE) |
| `entries[].targetId`| `1`  | Physical DSP port number the GFD is connected to         |
| `entries[].instanceId`| `0` | Instance ID (use 0 when only one GFD per port)          |

> **Tip:** `pid` is decimal in JSON. `16 decimal = 0x010 hex`.

### CLI Invocation
```python
response = sio.call("pbr:configurePid", {
    "operation": 0,
    "entries": [
        {"pid": 16, "targetId": 1, "instanceId": 0}
    ]
})
print(response)
```

### Expected Output
```json
{
  "error": "",
  "result": "SUCCESS"
}
```

### Error Cases
| Output `error`   | Cause                                               | Fix                              |
|------------------|-----------------------------------------------------|----------------------------------|
| `"INVALID_INPUT"` | PID already assigned to a **different** port        | Clear old PID first (operation=1) |
| `"INVALID_INPUT"` | PID out of range (> 0xFFE) or port not found        | Check pid ≤ 4094 and port exists |

---

## STEP 3 — Set DRT (`pbr:setDrt`)

### Purpose
*"Program the routing table so that DPID `0x010` routes to physical port 1."*
After this command the **data plane is live** — host TLPs with DPID=0x010 will
be forwarded to the GFD.

### Socket.IO Event
```
Event: "pbr:setDrt"
```

### Input Payload
```json
{
  "drtIndex": 0,
  "startEntry": 16,
  "entries": [
    {
      "entryType": "PHYSICAL_PORT",
      "routingTarget": 1
    }
  ]
}
```

### Input Field Descriptions
| Field                  | Value           | Meaning                                               |
|------------------------|-----------------|-------------------------------------------------------|
| `drtIndex`             | `0`             | Which DRT table to write (0 = first, use numDrts-1 max) |
| `startEntry`           | `16`            | Starting DPID index to write (`16 dec = 0x010`). Array index IS the DPID |
| `entries[].entryType`  | `"PHYSICAL_PORT"` | `"PHYSICAL_PORT"` = forward to a port. `"INVALID"` = drop. `"RGT_INDEX"` = multicast |
| `entries[].routingTarget`| `1`           | Physical port number to forward TLPs to (must match targetId from Step 2) |

> **IMPORTANT:** `"RESERVED"` is NOT a valid entryType — the switch will reject it with INVALID_INPUT.

### CLI Invocation
```python
response = sio.call("pbr:setDrt", {
    "drtIndex": 0,
    "startEntry": 16,
    "entries": [
        {"entryType": "PHYSICAL_PORT", "routingTarget": 1}
    ]
})
print(response)
```

### Expected Output
```json
{
  "error": "",
  "result": "SUCCESS"
}
```

### After This Step
```
Host sends TLP with DPID=0x010
  → PbrSwitchRouter: DRT[0][16] = PHYSICAL_PORT → port 1
  → TLP forwarded to GFD on port 1
  → GFD responds (BAR-0 MMIO read/write)
```

---

## STEP 4 — Get PID Binding (`pbr:getPidBinding`) — Verify Unbound

### Purpose
*"Check whether vPPB 0 in VCS 0 is already bound to a PID."*
Expected answer before binding: `pid = 4095 (= 0xFFF = UNBOUND)`.

### Socket.IO Event
```
Event: "pbr:getPidBinding"
```

### Input Payload
```json
{
  "targetVcs": 0,
  "targetVppb": 0
}
```

### Input Field Descriptions
| Field        | Value | Meaning                                          |
|--------------|-------|--------------------------------------------------|
| `targetVcs`  | `0`   | Virtual CXL Switch ID (0 = first VCS)            |
| `targetVppb` | `0`   | Virtual PPB ID within the VCS (0 = first vPPB)   |

### CLI Invocation
```python
response = sio.call("pbr:getPidBinding", {
    "targetVcs": 0,
    "targetVppb": 0
})
print(response)
```

### Expected Output — BEFORE Binding (Step 4)
```json
{
  "error": "",
  "result": {
    "pid": 4095,
    "latencyEntryBaseUnit": 0,
    "latencyEntry": 0,
    "bwEntryBaseUnit": 0,
    "bwEntry": 0
  }
}
```

### Output Field Descriptions
| Field                | Value  | Meaning                                              |
|----------------------|--------|------------------------------------------------------|
| `pid`                | `4095` | `4095 = 0xFFF` = **UNBOUND** (PID not yet assigned to this vPPB) |
| `latencyEntryBaseUnit`| `0`   | HMAT latency base unit (0 = not set)                |
| `latencyEntry`       | `0`    | HMAT latency value (0 = no constraint)              |
| `bwEntryBaseUnit`    | `0`    | HMAT bandwidth base unit (0 = not set)              |
| `bwEntry`            | `0`    | HMAT bandwidth value (0 = no constraint)            |

### Validation
```
✓  pid == 4095 (0xFFF)   -- vPPB is unbound, safe to bind in Step 5
✗  pid == 16  (0x010)    -- vPPB already bound; skip Step 5 or unbind first
```

---

## STEP 5 — Configure PID Binding (`pbr:configurePidBinding`) — BIND

### Purpose
*"Bind vPPB 0 to PID `0x010`. Make vPPB 0 point to the GFD."*
This is a **background command**. The response `"BACKGROUND_COMMAND_STARTED"` is correct — it means
the switch accepted the request and is processing it asynchronously.

### Socket.IO Event
```
Event: "pbr:configurePidBinding"
```

### Input Payload
```json
{
  "operation": 0,
  "targetVcs": 0,
  "targetVppb": 0,
  "pid": 16,
  "latencyEntryBaseUnit": 0,
  "latencyEntry": 0,
  "bwEntryBaseUnit": 0,
  "bwEntry": 0
}
```

### Input Field Descriptions
| Field                | Value | Meaning                                                   |
|----------------------|-------|-----------------------------------------------------------|
| `operation`          | `0`   | `0` = BIND (associate vPPB to PID), `1` = UNBIND         |
| `targetVcs`          | `0`   | Virtual CXL Switch ID                                    |
| `targetVppb`         | `0`   | Virtual PPB ID to bind                                   |
| `pid`                | `16`  | PID to bind to (`16 dec = 0x010 hex`, from Step 2)       |
| `latencyEntryBaseUnit`| `0`  | HMAT latency base unit (0 = no constraint, use default)  |
| `latencyEntry`       | `0`   | HMAT latency value (0 = no constraint)                   |
| `bwEntryBaseUnit`    | `0`   | HMAT bandwidth base unit (0 = no constraint)             |
| `bwEntry`            | `0`   | HMAT bandwidth value (0 = no constraint)                 |

> **When to set HMAT fields:** Only set non-zero HMAT values if you need to advertise
> specific latency/bandwidth characteristics to the host OS for NUMA topology.
> For basic GFD commissioning, leave all HMAT fields at `0`.

### CLI Invocation
```python
response = sio.call("pbr:configurePidBinding", {
    "operation": 0,
    "targetVcs": 0,
    "targetVppb": 0,
    "pid": 16,
    "latencyEntryBaseUnit": 0,
    "latencyEntry": 0,
    "bwEntryBaseUnit": 0,
    "bwEntry": 0
})
print(response)
```

### Expected Output
```json
{
  "error": "",
  "result": "BACKGROUND_COMMAND_STARTED"
}
```

> **`"BACKGROUND_COMMAND_STARTED"` is the CORRECT and EXPECTED response.**
> It means the switch accepted the bind request and is executing it asynchronously.
> Do NOT treat this as an error. Proceed to Step 6 to verify completion.

### To Unbind (Decommission)
```python
# Use operation=1 to remove a binding
response = sio.call("pbr:configurePidBinding", {
    "operation": 1,
    "targetVcs": 0,
    "targetVppb": 0,
    "pid": 16
})
```

---

## STEP 6 — Get PID Binding (`pbr:getPidBinding`) — Verify Bound

### Purpose
*"Confirm that vPPB 0 is now bound to PID `0x010`."*
Same event as Step 4, same input — different expected output.

### CLI Invocation
```python
response = sio.call("pbr:getPidBinding", {
    "targetVcs": 0,
    "targetVppb": 0
})
print(response)
```

### Expected Output — AFTER Binding (Step 6)
```json
{
  "error": "",
  "result": {
    "pid": 16,
    "latencyEntryBaseUnit": 0,
    "latencyEntry": 0,
    "bwEntryBaseUnit": 0,
    "bwEntry": 0
  }
}
```

### Validation
```
✓  pid == 16 (0x010)    -- vPPB successfully bound to GFD PID
✗  pid == 4095 (0xFFF)  -- binding failed; background task may not have completed yet
```

---

## Optional: Verify DRT Entry (`pbr:getDrt`)

After Step 3, use this to confirm the DRT was written correctly.

### Socket.IO Event
```
Event: "pbr:getDrt"
```

### Input Payload
```json
{
  "drtIndex": 0,
  "startEntry": 16,
  "numEntries": 1
}
```

### Input Field Descriptions
| Field        | Value | Meaning                                          |
|--------------|-------|--------------------------------------------------|
| `drtIndex`   | `0`   | DRT table index (0 = first)                      |
| `startEntry` | `16`  | First DPID entry to read (`16 dec = 0x010`)      |
| `numEntries` | `1`   | How many consecutive DRT entries to read         |

### CLI Invocation
```python
response = sio.call("pbr:getDrt", {
    "drtIndex": 0,
    "startEntry": 16,
    "numEntries": 1
})
print(response)
```

### Expected Output
```json
{
  "error": "",
  "result": {
    "drtIndex": 0,
    "startEntry": 16,
    "associatedRgtIndex": 0,
    "entries": [
      {
        "entryType": "PHYSICAL_PORT",
        "routingTarget": 1
      }
    ]
  }
}
```

### Output Field Descriptions
| Field                    | Value           | Meaning                                      |
|--------------------------|-----------------|----------------------------------------------|
| `drtIndex`               | `0`             | DRT table this response is from              |
| `startEntry`             | `16`            | Starting DPID index (`0x010`)                |
| `associatedRgtIndex`     | `0`             | Associated RGT (0 = none)                   |
| `entries[].entryType`    | `"PHYSICAL_PORT"` | Entry type (PHYSICAL_PORT = routable)      |
| `entries[].routingTarget`| `1`             | Confirmed: DPID 0x010 → port 1 (GFD)        |

---

## GAE Commands — Host-Initiated GFD Management

After commissioning, the host can manage the GFD via the GAE proxy.

---

### GAE Step A — Identify GAE (`gae:identify`)

### Purpose
*"Which vPPBs support Global Fabric Attach (G-FAM)?"*

### CLI Invocation
```python
response = sio.call("gae:identify")
print(response)
```

### Expected Output (simple GFD, no G-FAM)
```json
{
  "error": "",
  "result": {
    "numVppbsWithGlobalMemory": 0,
    "vppbEntries": []
  }
}
```

---

### GAE Step B — Proxy GFD Management Command (`gae:proxyGfdMgmt`)

### Purpose
*"Send a CCI command to the GFD via the GAE proxy (because the host can't reach the GFD directly)."*
Returns a `threadId` — use it to poll for the result.

### CLI Invocation (example: Identify GFD via proxy)
```python
response = sio.call("gae:proxyGfdMgmt", {
    "gfdOpcode": 1,
    "gfdPayload": []
})
print(response)
```

### Input Field Descriptions
| Field        | Value | Meaning                                                          |
|--------------|-------|------------------------------------------------------------------|
| `gfdOpcode`  | `1`   | CCI opcode to forward to the GFD. `1 = 0x0001` = Identify GFD  |
| `gfdPayload` | `[]`  | Payload bytes for the GFD command (as a list of integers). Empty for Identify |

### Expected Output
```json
{
  "error": "",
  "result": {
    "threadId": 1
  }
}
```

---

### GAE Step C — Get Proxy Thread Status (`gae:getProxyStatus`)

### Purpose
*"Has the GFD responded to the command I sent in Step B?"*

### CLI Invocation
```python
response = sio.call("gae:getProxyStatus", {"threadId": 1})
print(response)
```

### Expected Output (GFD Identify completed)
```json
{
  "error": "",
  "result": {
    "threadId": 1,
    "completed": true,
    "gfdReturnCode": 0,
    "gfdResponsePayload": [0, 238, 0, 175, 0, 0, 0, 0, 4]
  }
}
```

### Output Field Descriptions
| Field                | Value    | Meaning                                                    |
|----------------------|----------|------------------------------------------------------------|
| `threadId`           | `1`      | The thread ID from Step B                                  |
| `completed`          | `true`   | `true` = GFD has responded; `false` = still in progress   |
| `gfdReturnCode`      | `0`      | GFD's CCI return code (`0 = SUCCESS`)                     |
| `gfdResponsePayload` | `[...]`  | Raw bytes of GFD's response payload. For Identify: `byte[8]=0x04` = GFD component_type |

### Decoding GFD Identify Response
```python
payload = response["result"]["gfdResponsePayload"]
vendor_id       = payload[0] | (payload[1] << 8)  # 0x00EE = EEUM VID
device_id       = payload[2] | (payload[3] << 8)  # 0x00AF = SW_GFD_DID
component_type  = payload[8]                        # 0x04 = GFD
print(f"Vendor ID:      0x{vendor_id:04X}")
print(f"Device ID:      0x{device_id:04X}")
print(f"Component Type: 0x{component_type:02X} ({'GFD' if component_type == 4 else 'unknown'})")
```

---

### GAE Step D — Cancel Proxy Thread (`gae:cancelProxy`) — Optional

### Purpose
*"Cancel a proxy command that is taking too long."*

```python
response = sio.call("gae:cancelProxy", {"threadId": 1})
print(response)
# Expected: {"error": "", "result": "SUCCESS"}
```

---

## Complete FM CLI Commissioning Script

```python
"""
fm_cli_gfd_commission.py
Complete GFD commissioning using FM Socket.IO CLI (port 8200).

Usage:
  python fm_cli_gfd_commission.py

Prerequisites:
  pip install "python-socketio[client]"
  FM must be running with switch connected.
"""

import socketio

FM_HOST   = "127.0.0.1"
FM_PORT   = 8200

GFD_PID   = 16     # 0x010 in decimal -- 12-bit PID
GFD_PORT  = 1      # DSP physical port the GFD is on
VCS_ID    = 0
VPPB_ID   = 0
DRT_INDEX = 0


def check(resp, step_name, expected_result=None):
    """Assert response is OK and optionally validate result."""
    if resp.get("error"):
        raise RuntimeError(f"{step_name} FAILED: error='{resp['error']}'")
    result = resp.get("result", {})
    if expected_result is not None and result != expected_result:
        # For dicts, check subset
        if isinstance(expected_result, dict):
            for k, v in expected_result.items():
                if result.get(k) != v:
                    raise ValueError(
                        f"{step_name}: field '{k}' expected {v!r}, got {result.get(k)!r}"
                    )
        elif result != expected_result:
            raise ValueError(f"{step_name}: expected {expected_result!r}, got {result!r}")
    print(f"  OK  {step_name}")
    return result


def main():
    sio = socketio.Client()
    sio.connect(f"http://{FM_HOST}:{FM_PORT}")
    print(f"Connected to FM at {FM_HOST}:{FM_PORT}\n")

    SEP = "=" * 60

    # ------------------------------------------------------------------
    # STEP 1: Identify PBR Switch
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"STEP 1  pbr:identify  (CCI 0x5700)")
    print(f"  Input: (no payload)")
    print(f"{SEP}")

    resp = sio.call("pbr:identify")
    print(f"  Raw response: {resp}")
    result = check(resp, "pbr:identify")
    print(f"  gaeSupportMap : {result['gaeSupportMap']:#018x}")
    print(f"  numDrts       : {result['numDrts']}")
    print(f"  numRgts       : {result['numRgts']}")
    assert result["numDrts"] >= 1, "numDrts must be >= 1"
    print()

    # ------------------------------------------------------------------
    # STEP 2: Configure PID Assignment
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"STEP 2  pbr:configurePid  (CCI 0x5704)")
    print(f"  Input: operation=0(ASSIGN), pid={GFD_PID}(0x{GFD_PID:03X}), "
          f"targetId={GFD_PORT}, instanceId=0")
    print(f"{SEP}")

    resp = sio.call("pbr:configurePid", {
        "operation": 0,
        "entries": [
            {"pid": GFD_PID, "targetId": GFD_PORT, "instanceId": 0}
        ]
    })
    print(f"  Raw response: {resp}")
    check(resp, "pbr:configurePid", expected_result="SUCCESS")
    print(f"  PID {GFD_PID} (0x{GFD_PID:03X}) assigned to port {GFD_PORT}")
    print(f"  NOTE: DRT not updated yet -- data plane still dark")
    print()

    # ------------------------------------------------------------------
    # STEP 3: Set DRT
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"STEP 3  pbr:setDrt  (CCI 0x5709)")
    print(f"  Input: drtIndex={DRT_INDEX}, startEntry={GFD_PID}(DPID=0x{GFD_PID:03X})")
    print(f"         entries: entryType=PHYSICAL_PORT, routingTarget={GFD_PORT}")
    print(f"{SEP}")

    resp = sio.call("pbr:setDrt", {
        "drtIndex": DRT_INDEX,
        "startEntry": GFD_PID,
        "entries": [
            {"entryType": "PHYSICAL_PORT", "routingTarget": GFD_PORT}
        ]
    })
    print(f"  Raw response: {resp}")
    check(resp, "pbr:setDrt", expected_result="SUCCESS")
    print(f"  DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = PHYSICAL_PORT -> port {GFD_PORT}")
    print(f"  DATA PLANE IS NOW LIVE for DPID=0x{GFD_PID:03X}")
    print()

    # ------------------------------------------------------------------
    # STEP 4: Get PID Binding -- verify UNBOUND
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"STEP 4  pbr:getPidBinding  (CCI 0x5705)  -- expect UNBOUND (pid=4095=0xFFF)")
    print(f"  Input: targetVcs={VCS_ID}, targetVppb={VPPB_ID}")
    print(f"{SEP}")

    resp = sio.call("pbr:getPidBinding", {
        "targetVcs": VCS_ID,
        "targetVppb": VPPB_ID
    })
    print(f"  Raw response: {resp}")
    result = check(resp, "pbr:getPidBinding")
    print(f"  pid = {result['pid']} (0x{result['pid']:03X})")
    assert result["pid"] == 0xFFF, f"Expected 0xFFF (UNBOUND), got {result['pid']:#05x}"
    print(f"  vPPB({VCS_ID},{VPPB_ID}) is UNBOUND -- safe to bind")
    print()

    # ------------------------------------------------------------------
    # STEP 5: Configure PID Binding -- BIND (background command)
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"STEP 5  pbr:configurePidBinding  (CCI 0x5706)  -- BIND  [BACKGROUND COMMAND]")
    print(f"  Input: operation=0(BIND), targetVcs={VCS_ID}, targetVppb={VPPB_ID}, "
          f"pid={GFD_PID}(0x{GFD_PID:03X})")
    print(f"         latencyEntryBaseUnit=0, latencyEntry=0, bwEntryBaseUnit=0, bwEntry=0")
    print(f"{SEP}")

    resp = sio.call("pbr:configurePidBinding", {
        "operation": 0,
        "targetVcs": VCS_ID,
        "targetVppb": VPPB_ID,
        "pid": GFD_PID,
        "latencyEntryBaseUnit": 0,
        "latencyEntry": 0,
        "bwEntryBaseUnit": 0,
        "bwEntry": 0
    })
    print(f"  Raw response: {resp}")
    # "BACKGROUND_COMMAND_STARTED" is correct and expected here
    check(resp, "pbr:configurePidBinding", expected_result="BACKGROUND_COMMAND_STARTED")
    print(f"  'BACKGROUND_COMMAND_STARTED' is CORRECT and EXPECTED")
    print(f"  Switch is binding vPPB({VCS_ID},{VPPB_ID}) -> PID 0x{GFD_PID:03X} in background")
    print()

    # ------------------------------------------------------------------
    # STEP 6: Get PID Binding -- verify BOUND
    # ------------------------------------------------------------------
    import time
    time.sleep(0.1)  # give background task a moment

    print(f"{SEP}")
    print(f"STEP 6  pbr:getPidBinding  (CCI 0x5705)  -- expect BOUND (pid={GFD_PID}=0x{GFD_PID:03X})")
    print(f"  Input: targetVcs={VCS_ID}, targetVppb={VPPB_ID}")
    print(f"{SEP}")

    resp = sio.call("pbr:getPidBinding", {
        "targetVcs": VCS_ID,
        "targetVppb": VPPB_ID
    })
    print(f"  Raw response: {resp}")
    result = check(resp, "pbr:getPidBinding")
    print(f"  pid = {result['pid']} (0x{result['pid']:03X})")
    assert result["pid"] == GFD_PID, f"Expected {GFD_PID} (0x{GFD_PID:03X}), got {result['pid']}"
    print(f"  vPPB({VCS_ID},{VPPB_ID}) is BOUND to PID 0x{GFD_PID:03X}")
    print()

    # ------------------------------------------------------------------
    # OPTIONAL: Verify DRT
    # ------------------------------------------------------------------
    print(f"{SEP}")
    print(f"OPTIONAL  pbr:getDrt  (CCI 0x5708)  -- verify routing table")
    print(f"  Input: drtIndex={DRT_INDEX}, startEntry={GFD_PID}, numEntries=1")
    print(f"{SEP}")

    resp = sio.call("pbr:getDrt", {
        "drtIndex": DRT_INDEX,
        "startEntry": GFD_PID,
        "numEntries": 1
    })
    print(f"  Raw response: {resp}")
    result = check(resp, "pbr:getDrt")
    entry = result["entries"][0]
    print(f"  DRT[{DRT_INDEX}][0x{GFD_PID:03X}]:")
    print(f"    entryType     = {entry['entryType']}")
    print(f"    routingTarget = {entry['routingTarget']}")
    print()

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------
    print("*" * 60)
    print("  GFD COMMISSIONING COMPLETE")
    print(f"  PID 0x{GFD_PID:03X} ({GFD_PID} dec) -> DSP port {GFD_PORT} -> GFD device")
    print(f"  DRT[{DRT_INDEX}][0x{GFD_PID:03X}] = PHYSICAL_PORT -> port {GFD_PORT}")
    print(f"  vPPB({VCS_ID},{VPPB_ID}) bound to PID 0x{GFD_PID:03X}")
    print(f"  TLPs with DPID=0x{GFD_PID:03X} will route to port {GFD_PORT}")
    print("*" * 60)

    sio.disconnect()


if __name__ == "__main__":
    main()
```

---

## Running the Script

```bash
# Start FM (in one terminal)
python -m opencis.apps.fabric_manager --config config.yaml

# Run commissioning (in another terminal)
pip install "python-socketio[client]"
python fm_cli_gfd_commission.py
```

---

## Expected Console Output (all 6 steps)

```
Connected to FM at 127.0.0.1:8200

============================================================
STEP 1  pbr:identify  (CCI 0x5700)
  Input: (no payload)
============================================================
  Raw response: {'error': '', 'result': {'gaeSupportMap': 1, 'numDrts': 1, 'numRgts': 0, 'routingCaps': 0}}
  OK  pbr:identify
  gaeSupportMap : 0x0000000000000001
  numDrts       : 1
  numRgts       : 0

============================================================
STEP 2  pbr:configurePid  (CCI 0x5704)
  Input: operation=0(ASSIGN), pid=16(0x010), targetId=1, instanceId=0
============================================================
  Raw response: {'error': '', 'result': 'SUCCESS'}
  OK  pbr:configurePid
  PID 16 (0x010) assigned to port 1
  NOTE: DRT not updated yet -- data plane still dark

============================================================
STEP 3  pbr:setDrt  (CCI 0x5709)
  Input: drtIndex=0, startEntry=16(DPID=0x010)
         entries: entryType=PHYSICAL_PORT, routingTarget=1
============================================================
  Raw response: {'error': '', 'result': 'SUCCESS'}
  OK  pbr:setDrt
  DRT[0][0x010] = PHYSICAL_PORT -> port 1
  DATA PLANE IS NOW LIVE for DPID=0x010

============================================================
STEP 4  pbr:getPidBinding  (CCI 0x5705)  -- expect UNBOUND (pid=4095=0xFFF)
  Input: targetVcs=0, targetVppb=0
============================================================
  Raw response: {'error': '', 'result': {'pid': 4095, 'latencyEntryBaseUnit': 0, ...}}
  OK  pbr:getPidBinding
  pid = 4095 (0xFFF)
  vPPB(0,0) is UNBOUND -- safe to bind

============================================================
STEP 5  pbr:configurePidBinding  (CCI 0x5706)  -- BIND  [BACKGROUND COMMAND]
  Input: operation=0(BIND), targetVcs=0, targetVppb=0, pid=16(0x010)
         latencyEntryBaseUnit=0, latencyEntry=0, bwEntryBaseUnit=0, bwEntry=0
============================================================
  Raw response: {'error': '', 'result': 'BACKGROUND_COMMAND_STARTED'}
  OK  pbr:configurePidBinding
  'BACKGROUND_COMMAND_STARTED' is CORRECT and EXPECTED
  Switch is binding vPPB(0,0) -> PID 0x010 in background

============================================================
STEP 6  pbr:getPidBinding  (CCI 0x5705)  -- expect BOUND (pid=16=0x010)
  Input: targetVcs=0, targetVppb=0
============================================================
  Raw response: {'error': '', 'result': {'pid': 16, 'latencyEntryBaseUnit': 0, ...}}
  OK  pbr:getPidBinding
  pid = 16 (0x010)
  vPPB(0,0) is BOUND to PID 0x010

============================================================
OPTIONAL  pbr:getDrt  (CCI 0x5708)  -- verify routing table
  Input: drtIndex=0, startEntry=16, numEntries=1
============================================================
  Raw response: {'error': '', 'result': {'drtIndex': 0, 'startEntry': 16, 'entries': [{'entryType': 'PHYSICAL_PORT', 'routingTarget': 1}]}}
  OK  pbr:getDrt
  DRT[0][0x010]:
    entryType     = PHYSICAL_PORT
    routingTarget = 1

************************************************************
  GFD COMMISSIONING COMPLETE
  PID 0x010 (16 dec) -> DSP port 1 -> GFD device
  DRT[0][0x010] = PHYSICAL_PORT -> port 1
  vPPB(0,0) bound to PID 0x010
  TLPs with DPID=0x010 will route to port 1
************************************************************
```

---

## Error Reference

| Step | Error Response                          | Cause                                   | Fix                                          |
|------|-----------------------------------------|-----------------------------------------|----------------------------------------------|
| 1    | `{"error": "INTERNAL_ERROR"}`           | Switch not connected to FM              | Verify MCTP connection on port 8100          |
| 2    | `{"error": "INVALID_INPUT"}`            | PID already assigned to different port  | Clear with `operation=1` first               |
| 2    | `{"error": "INVALID_INPUT"}`            | `pid > 4094` or port doesn't exist      | Use pid ≤ 4094, check port index             |
| 3    | `{"error": "INVALID_INPUT"}`            | `entryType = "RESERVED"`               | Use `"PHYSICAL_PORT"`, `"INVALID"`, or `"RGT_INDEX"` |
| 5    | `{"error": "", "result": "SUCCESS"}`    | Bug: should be BACKGROUND_COMMAND_STARTED | Normal SUCCESS is also acceptable            |
| 5    | `{"error": "INVALID_INPUT"}`            | pid = 0xFFF or vcs/vppb out of range   | Use the PID from Step 2                      |
| 6    | `result.pid == 4095`                    | Background task not done yet            | Wait 100ms and retry Step 6                  |

---

## Quick Reference Card

```
COMMISSIONING SEQUENCE (FM CLI — port 8200):
─────────────────────────────────────────────────────────────
Step  Event                    Key Input                     Expected Result
────  ──────────────────────── ──────────────────────────    ───────────────────────
1     pbr:identify             (none)                        numDrts >= 1
2     pbr:configurePid         pid=16, targetId=1            "SUCCESS"
3     pbr:setDrt               startEntry=16, PHYSICAL_PORT  "SUCCESS"
4     pbr:getPidBinding        targetVcs=0, targetVppb=0     pid=4095 (0xFFF=UNBOUND)
5     pbr:configurePidBinding  pid=16, operation=0(BIND)     "BACKGROUND_COMMAND_STARTED"
6     pbr:getPidBinding        targetVcs=0, targetVppb=0     pid=16 (0x010=BOUND)
─────────────────────────────────────────────────────────────
NOTE: pid=16 decimal = 0x010 hex throughout
NOTE: "BACKGROUND_COMMAND_STARTED" at Step 5 is CORRECT
```

---

*Document version: 1.0 — June 2026*
*FM CLI Socket.IO server: [socketio_server.py](file:///c:/Users/pavan/Desktop/cxl/opencis-core/opencis/cxl/component/fabric_manager/socketio_server.py)*
*FM port: 8200 (Socket.IO / HTTP)*
*Spec: CXL 4.0 Rev 1.0, Section 7.7.13-7.7.14*
