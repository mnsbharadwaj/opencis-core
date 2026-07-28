# CXL FMAPI Testing Guide: `1vcs_2MLDs` Configuration

This guide provides testing commands and payload structures for validating the Fabric Manager (FM) CCI commands on OpenCIS. The examples assume a standard **1 VCS (Virtual CXL Switch) and 2 MLDs (Multi-Logical Devices)** topology.

---

## 1. Emulated Topology Configuration (`1vcs_2MLDs`)
* **VCS ID**: `0`
* **VCS Downstream Ports (vPPB)**: `1` and `2`
* **MLD Port IDs**: Port `1` (connected to MLD 0) and Port `2` (connected to MLD 1)
* **Logical Devices (LDs)**: LD `0` to `7` on MLD 0, LD `0` to `7` on MLD 1
* **Test UUID Tag**: `00112233445566778899aabbccddeeff`

---

## 2. Testing Commands & Example Payloads

### 2.1 Physical Switch Commands (Opcodes 5102h - 5107h)

#### 1. Physical Port Control (5102h)
Perform control action (e.g., Reset PPB / Opcode `1`) on Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py port-control 1 1
```
* **Payload structure/arguments:**
  * `ppb_id`: `1` (Downstream Port 1)
  * `port_opcode`: `1` (Reset PPB)

#### 2. Send PPB CXL.io Config (5103h)
Issue a configuration read transaction to PPB `1` at register offset `0x10`:
```bash
python opencis/bin/fabric_manager.py send-ppb-config 1 0x10 0 0xF 0
```
* **Payload structure/arguments:**
  * `ppb_id`: `1` (PPB index 1)
  * `register_num`: `0x10` (PCI configuration register offset)
  * `ext_register_num`: `0` (Extended register offset)
  * `first_dword_byte_enable`: `0xF` (Enabled bytes mask)
  * `transaction_type`: `0` (0 for read, 1 for write)
  * `--transaction-data`: `0` (optional write data, defaults to 0)

#### 3. Set Domain Validation SV (5105h)
Set the validation secret value (16-byte UUID) for the switch domain:
```bash
python opencis/bin/fabric_manager.py set-domain-val 00112233445566778899aabbccddeeff
```
* **Payload structure/arguments:**
  * `secret_value_hex`: `00112233445566778899aabbccddeeff` (16-byte hex secret)

#### 4. Get Domain Validation SV State (5104h)
Query the validation state of the switch domain:
```bash
python opencis/bin/fabric_manager.py get-domain-val-state
```
* **Expected Output Payload:** Returns whether the validation SV is set (State `0x01` if set, otherwise `0x00`).

#### 5. Get VCS Domain Validation SV State (5106h)
Query validation state for VCS ID `0`:
```bash
python opencis/bin/fabric_manager.py get-vcs-domain-val-state 0
```
* **Payload structure/arguments:**
  * `vcs_id`: `0`

#### 6. Get Domain Validation SV (5107h)
Retrieve the set validation secret value for VCS ID `0`:
```bash
python opencis/bin/fabric_manager.py get-domain-val 0
```
* **Payload structure/arguments:**
  * `vcs_id`: `0`
* **Expected Output Payload:** The 16-byte UUID `00112233445566778899aabbccddeeff` (if validation SV was set).

---

### 2.2 Virtual Switch Commands (Opcode 5203h)

#### Generate AER Event (5203h)
Inject an Advanced Error Reporting (AER) event on VCS `0` at Downstream Port/vPPB `1` with Uncorrectable severity (Status Bit 5):
```bash
python opencis/bin/fabric_manager.py generate-aer 0 1 0x80000005 00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
```
* **Payload structure/arguments:**
  * `vcs_id`: `0`
  * `vppb_instance`: `1` (Target Downstream Port)
  * `aer_error`: `0x80000005` (Severity bit 31 set to `1` indicating Uncorrectable, Error Status Bit 5)
  * `aer_header_hex`: `00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff` (32-byte header log in hex)

---

### 2.3 MLD Port Commands (Opcodes 5301h - 5302h)

#### 1. Send LD Config (5301h)
Issue a configuration read transaction to LD `2` connected to physical Downstream Port `1`:
```bash
python opencis/bin/fabric_manager.py send-ld-config 1 0x08 0 0xF 0 2
```
* **Payload structure/arguments:**
  * `ppb_id`: `1` (Downstream Port 1)
  * `register_num`: `0x08` (PCI configuration register offset)
  * `ext_register_num`: `0` (Extended register offset)
  * `first_dword_byte_enable`: `0xF` (Enabled bytes mask)
  * `transaction_type`: `0` (0 for read, 1 for write)
  * `ld_id`: `2` (Target Logical Device ID)

#### 2. Send LD Memory Request (5302h)
Perform direct memory read request (8 bytes) on physical Downstream Port `1` to logical device LD `3` at memory address `0x10000`:
```bash
python opencis/bin/fabric_manager.py send-ld-memory 1 0xF 0 0 3 8 0x10000
```
* **Payload structure/arguments:**
  * `port_id`: `1` (Downstream Port 1)
  * `first_dword_byte_enable`: `0xF` (Enabled bytes mask)
  * `last_dword_byte_enable`: `0`
  * `transaction_type`: `0` (0 for read, 1 for write)
  * `ld_id`: `3` (Target Logical Device ID)
  * `transaction_length`: `8` (Bytes length of transfer)
  * `transaction_address`: `0x10000` (Memory address)
  * `--transaction-data-hex`: Optional payload write data in hex

---

### 2.4 Multi-Headed Device Commands (Opcodes 5501h - 5502h)

#### 1. Get Multi-Headed Info (5501h)
Retrieve Multi-Headed layout information starting from Logical Device ID `0`:
```bash
python opencis/bin/fabric_manager.py get-mhd-info 0 16
```
* **Payload structure/arguments:**
  * `start_ld_id`: `0`
  * `ld_map_list_limit`: `16` (Max entries returned)

#### 2. Get Head Info (5502h)
Retrieve interface link info starting from Physical Head `0` for `2` heads:
```bash
python opencis/bin/fabric_manager.py get-head-info 0 2
```
* **Payload structure/arguments:**
  * `start_head`: `0`
  * `num_heads`: `2`

---

### 2.5 DCD Management Commands (Opcodes 5606h - 5608h)

#### 1. DCD Add Reference (5606h)
Add reference to a dynamic capacity memory region matching Tag `00112233445566778899aabbccddeeff`:
```bash
python opencis/bin/fabric_manager.py dcd-add-ref 00112233445566778899aabbccddeeff
```
* **Payload structure/arguments:**
  * `tag_hex`: `00112233445566778899aabbccddeeff` (16-byte UUID in hex)

#### 2. DCD List Tags (5608h)
List active dynamic capacity references:
```bash
python opencis/bin/fabric_manager.py dcd-list-tags 0 10
```
* **Payload structure/arguments:**
  * `starting_index`: `0`
  * `max_tags`: `10`

#### 3. DCD Remove Reference (5607h)
Remove reference to a dynamic capacity memory region matching Tag `00112233445566778899aabbccddeeff`:
```bash
python opencis/bin/fabric_manager.py dcd-remove-ref 00112233445566778899aabbccddeeff
```
* **Payload structure/arguments:**
  * `tag_hex`: `00112233445566778899aabbccddeeff` (16-byte UUID in hex)
