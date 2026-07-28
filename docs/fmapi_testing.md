# CXL FMAPI Testing Guide: `1vcs_2MLDs` Configuration

This guide provides testing commands and payload structures for validating all Fabric Manager (FM) CCI commands supported in the OpenCIS repository. The examples assume a standard **1 VCS (Virtual CXL Switch) and 2 MLDs (Multi-Logical Devices)** topology.

---

## 1. Emulated Topology Configuration (`1vcs_2MLDs`)
* **VCS ID**: `0`
* **VCS Downstream Ports (vPPB)**: `1` and `2`
* **MLD Port IDs**: Port `1` (connected to MLD 0) and Port `2` (connected to MLD 1)
* **Logical Devices (LDs)**: LD `0` to `3` on MLD 0, LD `0` to `1` on MLD 1
* **Test UUID Tag**: `00112233445566778899aabbccddeeff`

---

## 2. Standard CXL Fabric Manager API Commands

### 2.1 Virtual Switch Binding & State Control (Opcodes 5201h - 5202h, 5204h - 5205h)

#### 1. Bind vPPB (5201h)
Bind virtual switch `0` Downstream Port `1` to physical port `1`, Logical Device `0`:
```bash
python opencis/bin/fabric_manager.py bind 0 1 1 0
```
* **Payload arguments:**
  * `vcs`: `0`
  * `vppb`: `1`
  * `port`: `1`
  * `ld_id`: `0` (Logical Device ID)

#### 2. Unbind vPPB (5202h)
Unbind virtual switch `0` Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py unbind 0 1
```
* **Payload arguments:**
  * `vcs`: `0`
  * `vppb`: `1`

#### 3. Freeze vPPB (5204h)
Freeze virtual switch `0` Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py freeze 0 1
```
* **Payload arguments:**
  * `vcs`: `0`
  * `vppb`: `1`

#### 4. Unfreeze vPPB (5205h)
Unfreeze virtual switch `0` Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py unfreeze 0 1
```
* **Payload arguments:**
  * `vcs`: `0`
  * `vppb`: `1`

---

### 2.2 MLD Component Allocation (Opcodes 5400h - 0x5402)

#### 1. Get LD Info (5400h)
Get Logical Device metadata/info for physical Port `1`:
```bash
python opencis/bin/fabric_manager.py get-ld-info 1
```
* **Payload arguments:**
  * `port_index`: `1`

#### 2. Get LD Allocations (5401h)
Query LD memory ranges allocated for Port `1`, starting from LD ID `0`, up to `4` entries:
```bash
python opencis/bin/fabric_manager.py get-ld-allocations 1 0 4
```
* **Payload arguments:**
  * `port`: `1`
  * `start_ld_id`: `0`
  * `limit`: `4`

#### 3. Set LD Allocation (5402h)
Modify allocations on physical Port `1` for `4` Logical Devices starting from LD ID `0`:
```bash
python opencis/bin/fabric_manager.py set-ld-allocation 1 4 0 0x1000
```
* **Payload arguments:**
  * `port`: `1`
  * `n_lds`: `4`
  * `start_ld`: `0`
  * `list`: `0x1000` (Allocation range values list)

---

### 2.3 Physical Switch & Port Control (Opcodes 5102h - 5107h)

#### 1. Physical Port Control (5102h)
Perform control action (e.g., Reset PPB / Opcode `1`) on Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py port-control 1 1
```
* **Payload arguments:**
  * `ppb_id`: `1` (Downstream Port 1)
  * `port_opcode`: `1` (Reset PPB)

#### 2. Send PPB Config (5103h)
Issue a config read to Downstream Port `1` at register offset `0x10`:
```bash
python opencis/bin/fabric_manager.py send-ppb-config 1 0x10 0 0xF 0
```
* **Payload arguments:**
  * `ppb_id`: `1`
  * `register_num`: `0x10`
  * `ext_register_num`: `0`
  * `first_dword_byte_enable`: `0xF`
  * `transaction_type`: `0` (Read)

#### 3. Set Domain Validation SV (5105h)
Set domain validation secret value (16-byte UUID in hex):
```bash
python opencis/bin/fabric_manager.py set-domain-val 00112233445566778899aabbccddeeff
```
* **Payload arguments:**
  * `secret_value_hex`: `00112233445566778899aabbccddeeff`

#### 4. Get Domain Validation SV State (5104h)
Retrieve current domain validation status:
```bash
python opencis/bin/fabric_manager.py get-domain-val-state
```

#### 5. Get VCS Domain Validation SV State (5106h)
Query validation state for VCS ID `0`:
```bash
python opencis/bin/fabric_manager.py get-vcs-domain-val-state 0
```

#### 6. Get Domain Validation SV (5107h)
Retrieve the set validation secret value for VCS ID `0`:
```bash
python opencis/bin/fabric_manager.py get-domain-val 0
```

---

### 2.4 Virtual Switch AER Event injection (Opcode 5203h)

#### Generate AER Event (5203h)
Inject AER correctable/uncorrectable event on VCS `0` at Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py generate-aer 0 1 0x80000005 00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
```
* **Payload arguments:**
  * `vcs_id`: `0`
  * `vppb_instance`: `1`
  * `aer_error`: `0x80000005` (Severity bit 31 is 1: Uncorrectable; Status bit: 5)
  * `aer_header_hex`: `00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff` (32-byte header)

---

### 2.5 MLD Port Commands (Opcodes 5301h - 5302h)

#### 1. Send LD Config (5301h)
Issue config read to LD `2` on physical Port `1` at offset `0x08`:
```bash
python opencis/bin/fabric_manager.py send-ld-config 1 0x08 0 0xF 0 2
```
* **Payload arguments:**
  * `ppb_id`: `1`
  * `register_num`: `0x08`
  * `ext_register_num`: `0`
  * `first_dword_byte_enable`: `0xF`
  * `transaction_type`: `0`
  * `ld_id`: `2`

#### 2. Send LD Memory Request (5302h)
Perform direct memory read (8 bytes) on Port `1` to logical device LD `3` at offset `0x10000`:
```bash
python opencis/bin/fabric_manager.py send-ld-memory 1 0xF 0 0 3 8 0x10000
```
* **Payload arguments:**
  * `port_id`: `1`
  * `first_dword_byte_enable`: `0xF`
  * `last_dword_byte_enable`: `0`
  * `transaction_type`: `0`
  * `ld_id`: `3`
  * `transaction_length`: `8`
  * `transaction_address`: `0x10000`

---

### 2.6 Multi-Headed Device Commands (Opcodes 5501h - 5502h)

#### 1. Get Multi-Headed Info (5501h)
Retrieve Multi-Headed layout information starting from LD ID `0`:
```bash
python opencis/bin/fabric_manager.py get-mhd-info 0 16
```

#### 2. Get Head Info (5502h)
Retrieve physical head status starting from Head `0` for `2` heads:
```bash
python opencis/bin/fabric_manager.py get-head-info 0 2
```

---

### 2.7 DCD Management Reference Commands (Opcodes 5606h - 5608h)

#### 1. DCD Add Reference (5606h)
Add reference to a dynamic capacity memory region matching Tag:
```bash
python opencis/bin/fabric_manager.py dcd-add-ref 00112233445566778899aabbccddeeff
```

#### 2. DCD List Tags (5608h)
List active dynamic capacity reference tags:
```bash
python opencis/bin/fabric_manager.py dcd-list-tags 0 10
```

#### 3. DCD Remove Reference (5607h)
Remove reference to a dynamic capacity memory region matching Tag:
```bash
python opencis/bin/fabric_manager.py dcd-remove-ref 00112233445566778899aabbccddeeff
```

---

## 3. PBR / GFD / GAE Switch Commands (Opcodes 5700h - 580Bh)

These commands are used to configure and route TLPs inside switches configured for **Port-Based Routing (PBR)** mode:

### 3.1 PBR Switch & DRT Configuration (Opcodes 5700h - 5709h)

#### 1. Identify PBR Switch (5700h)
Get DRT table limits and PBR features:
```bash
python opencis/bin/fabric_manager.py pbr-identify
```

#### 2. Configure PID Assignment (5704h)
Assign a Routing PID (e.g. `0x010`) to physical Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-configure-pid 0x010 1
```

#### 3. Clear PID Assignment (5704h)
Remove PID assignment `0x010` from Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-clear-pid 0x010 1
```

#### 4. Get PID Binding (5705h)
Query the PID currently bound to VCS `0` Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-get-pid-binding 0 1
```

#### 5. Bind PID (5706h)
Bind PID `0x010` to VCS `0` Downstream Port `1` (triggers link activation):
```bash
python opencis/bin/fabric_manager.py pbr-bind-pid 0 1 0x010
```

#### 6. Unbind PID (5706h)
Unbind PID `0x010` from VCS `0` Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-unbind-pid 0 1 0x010
```

#### 7. Get DRT (5708h)
Read DPID routing table starting from offset `0` for `16` entries:
```bash
python opencis/bin/fabric_manager.py pbr-get-drt --start-entry 0 --num-entries 16
```

#### 8. Set DRT (5709h)
Configure routing target for PID `0x010` to physical Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-set-drt 0 0x010 1
```

#### 9. Tunnel Command (Fabric Crawl Out - 5701h)
Forward CCI command (e.g., Identify GFD / opcode `0x0001`) to GFD connected at physical Port `1`:
```bash
python opencis/bin/fabric_manager.py pbr-fabric-crawl-out 1 0x0001
```

---

### 3.2 GAE (Generic Access Endpoint) Commands (Opcodes 5800h - 580Bh)

#### 1. Identify GAE (5800h)
Show which vPPBs support Global Memory access:
```bash
python opencis/bin/fabric_manager.py gae-identify
```

#### 2. Get GAE Access Vectors (5802h)
Get access vectors for target PID `0x010`:
```bash
python opencis/bin/fabric_manager.py gae-get-pid-vectors 0x010
```

#### 3. Proxy GFD Command (5809h)
Proxy GFD command (e.g., opcode `0x0001`) through GAE (returns a `thread_id`):
```bash
python opencis/bin/fabric_manager.py gae-proxy-gfd 0x0001
```

#### 4. Get Proxy Status (580Ah)
Poll completion state for proxy thread `1`:
```bash
python opencis/bin/fabric_manager.py gae-proxy-status 1
```

#### 5. Cancel Proxy Operation (580Bh)
Cancel executing proxy operation on thread `1`:
```bash
python opencis/bin/fabric_manager.py gae-cancel-proxy 1
```
