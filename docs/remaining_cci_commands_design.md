# Design Document: Remaining CXL FM API CCI Commands

This document details the design, design decisions, and implementation strategies for the remaining 12 CXL Fabric Manager (FM) API CCI commands (Table 7-17) on both the **OpenCIS Simple Device (Simulator)** and **QEMU Device Emulation**.

---

## 1. MLD Info & Allocations (Group 54h)

### 1.1 Get LD Info (0x5400)
* **Design Overview**: Retrieves configuration info of a Multi-Logical Device (MLD) including total logical devices (LDs), active mappings, and supported ranges.
* **Design Decision**: Query the switch's `VirtualSwitchManager` and connected port bindings to return the exact counts, rather than leaving it unimplemented.
* **Simple Device (OpenCIS) Implementation**:
  - The switch command parser reads `request.payload` to extract `port_id`.
  - Queries `VirtualSwitchManager.get_virtual_switch(vcs_id)` to count bound upstream/downstream ports.
  - Returns a struct containing `num_lds` and standard division of DPA bounds.
* **QEMU Device Emulation (Future)**:
  - In QEMU, the MLD device exposes this register block via its PCIe capability structure. 
  - QEMU will parse the back-end host memory directories and query the guest kernel's resource allocation trees to return actual logical partitions.

### 1.2 Get LD Allocations (0x5401)
* **Design Overview**: Returns the memory ranges (DPA ranges) allocated to specific LDs.
* **Design Decision**: Maintain an in-memory LD allocation table within the switch component.
* **Simple Device (OpenCIS) Implementation**:
  - Command executes by reading the `allocated_ld` map stored in the `CxlVirtualSwitch` class.
  - Returns starting DPA and length of volatile/persistent memory allocated to the requested LD.
* **QEMU Device Emulation (Future)**:
  - Mapped directly to the guest’s physical address mappings. QEMU will read the backend shared file offsets representing logical capacity chunks.

---

## 2. QoS Control, Status, & Bandwidth (Group 54h)

### 2.1 Get QoS Control (0x5403) & Set QoS Control (0x5404)
* **Design Overview**: Reads and writes throttling policies and back-pressure controls.
* **Design Decision**: Emulate standard status registers and log Set operations without active scheduling.
* **Simple Device (OpenCIS) Implementation**:
  - `Get QoS Control` returns `QoS_Throttling_Supported = 0` (standard fallback).
  - `Set QoS Control` accepts parameters and returns `SUCCESS`.
* **QEMU Device Emulation (Future)**:
  - QEMU will intercept Set commands and configure cgroups or block I/O throttle limits on the host backing device.

### 2.2 Get QoS Status (0x5405)
* **Design Overview**: Returns traffic telemetry (throttling triggers, back-pressure latency).
* **Design Decision**: Return default all-zero metrics indicating nominal operating conditions.
* **Simple Device (OpenCIS) Implementation**:
  - Instantiates a telemetry payload with standard active counts set to 0.
* **QEMU Device Emulation (Future)**:
  - QEMU will read live host stats (`io.stat` or custom hypervisor counters) and report them via virtualized MMIO.

### 2.3 Get QoS Allocated Bandwidth (0x5406) & Set QoS Allocated Bandwidth (0x5407)
* **Design Overview**: Manages bandwidth allocation caps per logical head/LD.
* **Design Decision**: Store bandwidth allocations in a local memory table and respond with success.
* **Simple Device (OpenCIS) Implementation**:
  - The switch command maintains a `dict` mapping `ld_id -> bandwidth_limit`. 
  - Get reads from the dict; Set updates the dict and returns `SUCCESS`.
* **QEMU Device Emulation (Future)**:
  - QEMU will map these allocations to virtio-net or block I/O rate limits to throttle host bus speeds.

### 2.4 Get QoS Bandwidth Limit (0x5408) & Set QoS Bandwidth Limit (0x5409)
* **Design Overview**: Reads and writes maximum limit thresholds.
* **Design Decision**: Store limits in switch state and respond with success.
* **Simple Device (OpenCIS) Implementation**:
  - Maintains `max_bandwidth_limit` in the `PhysicalPortManager` registers.
* **QEMU Device Emulation (Future)**:
  - QEMU uses host system network/bus controllers to actively throttle memory bus frequencies.

---

## 3. Multi-Headed Device (MHD) Management (Group 55h)

### 3.1 Get Multi-Headed Info (0x5500)
* **Design Overview**: Returns head configuration and ownership/arbitration state.
* **Design Decision**: Return a single-head topology by default.
* **Simple Device (OpenCIS) Implementation**:
  - Returns `num_heads = 1` and marks head 0 as active/owned.
* **QEMU Device Emulation (Future)**:
  - In a multi-VM setup, QEMU will coordinate ownership via a shared daemon (e.g. using socket communications or a central cluster manager) to dynamically arbitrate head access.

---

## 4. LD-Specific Commands (Group 53h)

### 4.1 Send LD CXL.io Configuration Request (0x5301)
* **Design Overview**: Relays configuration reads/writes to a specific LD within an MLD.
* **Design Decision**: Forward payload via the downstream port connection's `cci_fifo` using LD tags.
* **Simple Device (OpenCIS) Implementation**:
  - The switch resolves the physical port, wraps the payload in a `CciMessagePacket` with the target `ld_id`, and sends it downstream over `cci_fifo`.
* **QEMU Device Emulation (Future)**:
  - QEMU intercepts the write and routes it to the specific virtio/vhost endpoint context representing the requested LD.

### 4.2 Send LD CXL.io Memory Request (0x5302)
* **Design Overview**: Forwards memory accesses directly to the target LD.
* **Design Decision**: Emulate by reading/writing to the simulated memdev file mapping offset by `ld_id`.
* **Simple Device (OpenCIS) Implementation**:
  - Intercepts the request and issues reads/writes to the `memory_file` at the offset corresponding to `ld_id * ld_size`.
* **QEMU Device Emulation (Future)**:
  - QEMU executes memory translations targeting the logical partition's host file descriptor.
