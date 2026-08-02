# CXL Fabric Manager API (FMAPI) Testing: `1vcs_2MLDs` Switch Configuration

This document defines the comprehensive test suite, commands, input parameters, and expected outputs for all CXL Fabric Manager API (FMAPI) commands under the `1vcs_2MLDs` switch configuration in OpenCIS.

---

## 1. Switch Configuration Reference (`1vcs_2mlds.yaml`)

This testing suite targets the standard **1 VCS (Virtual CXL Switch) and 2 MLDs (Multi-Logical Devices)** configuration defined in [1vcs_2mlds.yaml](file:///C:/Users/User/.gemini/antigravity/scratch/opencis-core/configs/1vcs_2mlds.yaml):

* **Switch Ports:**
  * **Port 0:** Upstream Port (USP)
  * **Port 1:** Downstream Port (DSP) connected to Multi-Logical Device 1
  * **Port 2:** Downstream Port (DSP) connected to Multi-Logical Device 2
* **Virtual CXL Switch (VCS ID 0):**
  * Contains **6 virtual PPBs (vPPBs)** (indices `0` to `5`)
  * **vPPB Initial Bindings:**
    * `vPPB 0` -> Port 1, LD 0
    * `vPPB 1` -> Port 1, LD 1
    * `vPPB 2` -> Port 1, LD 2
    * `vPPB 3` -> Port 1, LD 3
    * `vPPB 4` -> Port 2, LD 0
    * `vPPB 5` -> Port 2, LD 1
* **Device Layout:**
  * **MLD on Port 1 (MLD 1):** Supports 4 Logical Devices (LDs `0` to `3`), total capacity `2G` (`256M` memory size per LD).
  * **MLD on Port 2 (MLD 2):** Supports 4 Logical Devices (LDs `0` to `1` allocated, `2` and `3` unallocated), total capacity `2G` (`512M` memory size per LD).

---

## 2. FMAPI CCI Test Cases

### 2.1 Virtual CXL Switch (VCS) State & Binding Control (Opcodes 5201h - 5205h)

#### 1. Bind vPPB (Opcode 5201h)
* **Description:** Bind virtual switch downstream port (vPPB) to a physical port and a logical device ID.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py bind 0 4 2 0
  ```
* **Inputs:**
  * `vcs`: `0`
  * `vppb`: `4` (vPPB 4)
  * `physical_port`: `2` (Port 2)
  * `ld_id`: `0` (LD 0)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * vPPB 4 binding updated successfully.

#### 2. Unbind vPPB (Opcode 5202h)
* **Description:** Unbind a virtual switch downstream port (vPPB), cutting the host-to-device mapping.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py unbind 0 4
  ```
* **Inputs:**
  * `vcs`: `0`
  * `vppb`: `4`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * vPPB 4 state transitions to unbound.

#### 3. Freeze vPPB (Opcode 5204h)
* **Description:** Freeze a virtual switch downstream port, blocking memory transaction processing.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py freeze 0 1
  ```
* **Inputs:**
  * `vcs`: `0`
  * `vppb`: `1`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * vPPB 1 frozen.

#### 4. Unfreeze vPPB (Opcode 5205h)
* **Description:** Unfreeze a previously frozen vPPB to resume memory operations.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py unfreeze 0 1
  ```
* **Inputs:**
  * `vcs`: `0`
  * `vppb`: `1`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * vPPB 1 unfrozen and active.

#### 5. Inject AER Event (Opcode 5203h)
* **Description:** Inject an Advanced Error Reporting (AER) event into the Virtual Switch hierarchy.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py generate-aer 0 1 0x80000005 00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
  ```
* **Inputs:**
  * `vcs_id`: `0`
  * `vppb_instance`: `1`
  * `aer_error`: `0x80000005` (Uncorrectable severity status bit 5)
  * `aer_header_hex`: `00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff` (32-byte AER Header)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Host notified of the simulated virtual device/port failure.

---

### 2.2 MLD Component Allocation (Opcodes 5400h - 5402h)

#### 1. Get LD Info (Opcode 5400h)
* **Description:** Retrieve the total capacity, total LD count, and QoS capabilities of the MLD component on a physical port.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-ld-info 1
  ```
* **Inputs:**
  * `port_index`: `1`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * 11-byte spec-compliant response payload containing:
    * `memory_size`: `0x80000000` (2GB in bytes)
    * `ld_count`: `4`
    * `qos_telemetry_capability`: QoS telemetry capability flags.

#### 2. Get LD Allocations (Opcode 5401h)
* **Description:** Retrieve the specific allocation range and details for Logical Devices.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-ld-allocations 1 0 4
  ```
* **Inputs:**
  * `port_index`: `1`
  * `start_ld_id`: `0`
  * `ld_allocation_list_limit`: `4`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Response structure:
    * `number_of_lds`: Number of Logical Devices currently allocated.
    * `memory_granularity`: Memory allocation step size.
    * `start_ld_id`: Starting logical device ID.
    * `ld_allocation_list_length`: Entries returned.
    * `ld_allocation_list`: Memory size values for each LD.

#### 3. Set LD Allocation (Opcode 5402h)
* **Description:** Configure the memory size allocations for Logical Devices on the physical port.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py set-ld-allocation 1 4 0 0x1000
  ```
* **Inputs:**
  * `port_index`: `1`
  * `number_of_lds`: `4`
  * `start_ld_id`: `0`
  * `ld_allocation_list`: `0x1000` (hex representation of the memory ranges)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Memory sizes for Logical Devices `0` to `3` updated on MLD 1.

---

### 2.3 Physical Switch & Port Control (Opcodes 5102h - 5107h)

#### 1. Physical Port Control (Opcode 5102h)
* **Description:** Reset or control power and state for a physical Downstream Port (PPB).
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py port-control 1 1
  ```
* **Inputs:**
  * `ppb_id`: `1` (Downstream Port 1)
  * `port_opcode`: `1` (Port Reset)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Physical port 1 performs a link reset.

#### 2. Send PPB CXL.io Configuration Request (Opcode 5103h)
* **Description:** Tunnels a PCIe/CXL config transaction directly to the selected Downstream Port (PPB) registry space.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py send-ppb-config 1 0x10 0 0xF 0
  ```
* **Inputs:**
  * `ppb_id`: `1`
  * `register_num`: `0x10`
  * `ext_register_num`: `0`
  * `first_dword_byte_enable`: `0xF`
  * `transaction_type`: `0` (Read config)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns the 4-byte read register value.

#### 3. Set Domain Validation Secret Value (Opcode 5105h)
* **Description:** Configure the domain validation secret key for verification.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py set-domain-val 00112233445566778899aabbccddeeff
  ```
* **Inputs:**
  * `secret_value_hex`: `00112233445566778899aabbccddeeff` (16-byte UUID)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Domain validation SV updated.

#### 4. Get Domain Validation SV State (Opcode 5104h)
* **Description:** Retrieve the current domain validation verification status.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-domain-val-state
  ```
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Status code indicating whether the domain validation is verified.

#### 5. Get VCS Domain Validation SV State (Opcode 5106h)
* **Description:** Query validation state for a specific Virtual Switch ID.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-vcs-domain-val-state 0
  ```
* **Inputs:**
  * `vcs_id`: `0`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Validation state for VCS 0.

#### 6. Get Domain Validation SV (Opcode 5107h)
* **Description:** Retrieve the set validation secret value for a specific VCS.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-domain-val 0
  ```
* **Inputs:**
  * `vcs_id`: `0`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns the matching 16-byte validation SV UUID.

---

### 2.4 MLD Port Tunneling Commands (Opcodes 5301h - 5302h)

#### 1. Send LD Config Request (Opcode 5301h)
* **Description:** Issue a PCIe CXL.io Configuration read/write transaction to a Logical Device (LD) behind a physical port.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py send-ld-config 1 0x08 0 0xF 0 2
  ```
* **Inputs:**
  * `ppb_id`: `1`
  * `register_num`: `0x08`
  * `ext_register_num`: `0`
  * `first_dword_byte_enable`: `0xF`
  * `transaction_type`: `0` (Read config)
  * `ld_id`: `2` (LD 2 on MLD 1)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns target register configuration bytes for LD 2.

#### 2. Send LD Memory Request (Opcode 5302h)
* **Description:** Perform direct memory read/write request transactions to logical device memory space.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py send-ld-memory 1 0xF 0 0 3 8 0x10000
  ```
* **Inputs:**
  * `port_id`: `1`
  * `first_dword_byte_enable`: `0xF`
  * `last_dword_byte_enable`: `0`
  * `transaction_type`: `0` (Read memory)
  * `ld_id`: `3` (LD 3 on MLD 1)
  * `transaction_length`: `8` (8 bytes)
  * `transaction_address`: `0x10000`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns 8 bytes of data read from memory location `0x10000` on LD 3.

---

### 2.5 Multi-Headed Device (MHD) Commands (Opcodes 5501h - 5502h)

#### 1. Get MHD Info (Opcode 5501h)
* **Description:** Retrieve multi-headed device topology layout information.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-mhd-info 0 16
  ```
* **Inputs:**
  * `start_ld_id`: `0`
  * `ld_map_list_limit`: `16`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns the MHD layout structures mapping logical heads to devices.

#### 2. Get Head Info (Opcode 5502h)
* **Description:** Retrieve head status and limits for multi-headed device.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py get-head-info 0 2
  ```
* **Inputs:**
  * `start_head`: `0`
  * `num_heads`: `2`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns configuration status blocks for Head 0 and Head 1.

---

### 2.6 Dynamic Capacity Device (DCD) Reference Commands (Opcodes 5606h - 5608h)

#### 1. DCD Add Reference (Opcode 5606h)
* **Description:** Add reference tag to dynamic capacity extent list region.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py dcd-add-ref 00112233445566778899aabbccddeeff
  ```
* **Inputs:**
  * `tag_hex`: `00112233445566778899aabbccddeeff` (16-byte Tag)
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Reference tag successfully added.

#### 2. DCD List Tags (Opcode 5608h)
* **Description:** Retrieve the list of active dynamic capacity tags.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py dcd-list-tags 0 10
  ```
* **Inputs:**
  * `starting_index`: `0`
  * `max_tags`: `10`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Returns list of matching tags starting from index 0.

#### 3. DCD Remove Reference (Opcode 5607h)
* **Description:** Delete dynamic capacity reference matching Tag.
* **Testing Command:**
  ```bash
  python opencis/bin/fabric_manager.py dcd-remove-ref 00112233445566778899aabbccddeeff
  ```
* **Inputs:**
  * `tag_hex`: `00112233445566778899aabbccddeeff`
* **Expected Output:**
  * Success status code `SUCCESS (0x0000)`.
  * Reference tag successfully removed.
