# CXL Fabric Manager API: Implementation Details & Limitations

This document provides a detailed breakdown of the 36 CXL Fabric Manager (FM) API CCI commands implemented or supported in the OpenCIS repository. It categorizes each command based on whether its execution is fully functional (straightforward integration) or emulated using simulated/mocked values, along with its specific limitations.

---

## 1. Summary of Command Classifications

* **Fully Functional Commands**: **15 commands** (Directly integrated with the OpenCIS virtual switch state machines, resource managers, or dynamic capacity device bounds).
* **Simulated/Mocked Commands**: **21 commands** (Parse and validate specifications correctly, but execute mock behaviors, return static values, or trace simulator logging because the underlying hardware actions are not modeled in CPU emulation).

---

## 2. Group A: Fully Functional Commands (15 Commands)

These commands interact directly with active manager objects in OpenCIS (`PhysicalPortManager`, `VirtualSwitchManager`, `MldManager`) to query or modify live emulated states:

| Opcode | Command Name | Functional Logic in OpenCIS | Realized Effects |
| :--- | :--- | :--- | :--- |
| **0x5100** | **Identify Switch Device** | Queries `PhysicalPortManager` and `VirtualSwitchManager`. | Returns exact counts of active ports, VCS instances, and vPPB slots. |
| **0x5101** | **Get Physical Port State** | Iterates over port configs and active link states. | Returns actual emulated link status, speed, width, and connected device mode. |
| **0x5200** | **Get Virtual CXL Switch Info** | Queries active VCS objects. | Returns live bindings (physical port ID, LD ID) for each vPPB. |
| **0x5201** | **Bind vPPB** | Triggers binding handler in the Switch connection client. | Actually binds a physical port/LD to a virtual downstream port. |
| **0x5202** | **Unbind vPPB** | Clears routing tables and disconnects endpoints. | Unbinds port/LD and clears active routing maps. |
| **0x5300** | **Tunnel Management** | Unpacks and encapsulates CCI packets. | Actually tunnels Fabric Manager packets to the target LD executor. |
| **0x5400** | **Get LD Info** | Queries `MldManager` limits. | Returns the actual total capacity and maximum logical device count configured. |
| **0x5401** | **Get LD Allocations** | Reads memory profiles. | Returns base addresses and lengths allocated to active LDs. |
| **0x5402** | **Set LD Allocations** | Updates live allocations in `MldManager`. | Dynamically re-dimensions logical device memory ranges. |
| **0x5600** | **Get DCD Info** | Reads emulated device specs. | Returns host counts, region block limits, and policy supports. |
| **0x5601** | **Get Host DC Region Config** | Reads active region config entries. | Returns exact bases, block sizes, and granulates for memory regions. |
| **0x5602** | **Set DC Region Config** | Modifies active region config entries. | Updates block sizing parameters and block counts in the emulated device. |
| **0x5603** | **Get DC Region Extent Lists** | Traverses dynamic capacity databases. | Returns list of extents (`start_dpa`, length, sequence, tag) currently active. |
| **0x5604** | **Initiate Dynamic Capacity Add** | Triggers DCD dynamic capacity add logic. | Runs the state machine to assign memory extents to the emulated host. |
| **0x5605** | **Initiate Dynamic Capacity Release** | Triggers DCD release logic. | Reclaims dynamic capacity blocks from the emulated host. |

---

## 3. Group B: Simulated / Mocked Commands (21 Commands)

These commands validate, parse, and respond correctly according to spec payloads, but use static or mocked variables instead of real hardware implementations:

### 3.1 Physical Switch Set (Opcodes 0x5102 - 0x5103)

#### Physical Port Control (0x5102)
* **Command Purpose:** Allows the Fabric Manager (FM) to perform hardware sideband control or line resets (such as asserting/deasserting PERST# or resetting the PPB connection state) on a switch port.
* **Simulated Values & Derivation:** 
  * The command processes the request (`ppb_id`, `port_opcode`) and writes warning/info tracing events into the simulator output.
  * It returns `CCI_RETURN_CODE.SUCCESS` (0x0000) for valid ports and `CCI_RETURN_CODE.INVALID_INPUT` (0x0002) for invalid ports/opcodes.
* **Architectural Rationale:** Since the emulator runs in user-space CPU memory, there is no physical PCIe reset pin or motherboard controller to assert. Mocking the success state allows external management scripts to run without failing.
* **Limitations:** The command does not reboot, power-cycle, or electrically reset any simulated connections; it only records the intent in the log.

#### Send PPB CXL.io Configuration Request (0x5103)
* **Command Purpose:** Permits the FM to issue PCI configuration read/write cycles to the PCI-to-PCI Bridge (PPB) representing a downstream switch port.
* **Simulated Values & Derivation:** 
  * **Reads:** Return `0x00000000` (in `SendPpbCxlIoConfigurationResponsePayload`). This default value was chosen because configuration register offsets that are unmapped or unprogrammed in standard PCI/PCIe specifications return zero values, preventing crash loops.
  * **Writes:** Mocked as a success trace.
* **Architectural Rationale:** Emulated downstream switch ports (PPBs) inside OpenCIS are modeled statically using mock headers. Direct config cycle modifications on these downstream configurations do not map to physical bridge silicon.
* **Limitations:** Writing configuration registers does not modify real hardware routing, lane reversal, or link parameters in the system kernel.

---

### 3.2 Domain Security & Validation (Opcodes 0x5104 - 0x5107)

* **Commands Covered:**
  * `Get Domain Validation SV State` (0x5104)
  * `Set Domain Validation SV` (0x5105)
  * `Get VCS Domain Validation SV State` (0x5106)
  * `Get Domain Validation SV` (0x5107)
* **Command Purpose:** Standardizes multi-tenant security verification. The Fabric Manager sets a Secret Value (SV) UUID to bind a host domain to a virtual CXL switch (VCS) to prevent host-domain bypasses.
* **Simulated Values & Derivation:**
  * **Validation State:** Initially reports `0x00` (Not Set) and changes to `0x01` (Set) once the SV is successfully written.
  * **SV Storage:** Stores and returns a 16-byte UUID set by the caller.
* **Architectural Rationale:** The state transitions and verification locks are fully emulated to ensure that standard management software compliance loops (which query whether a validation SV is set before assigning host routes) function correctly.
* **Limitations:** The 16-byte UUID is stored in volatile emulated state variables. No cryptographic silicon engine (such as a TPM or HSM) is utilized, and no real hardware bus-level partitioning enforces access control based on this key.

---

### 3.3 Virtual Switch Events (Opcode 0x5203)

#### Generate AER Event (0x5203)
* **Command Purpose:** Directs the virtual switch to generate an Advanced Error Reporting (AER) event interrupt and log standard TLP error headers on a specific vPPB.
* **Simulated Values & Derivation:**
  * Parses severity bits (`0` for correctable, `1` for uncorrectable), status register bit locations, and a 32-byte AER header.
  * Logs the error details inside the simulation log.
* **Architectural Rationale:** In a physical system, generating an AER event triggers standard PCIe interrupts (MSI-X/INTx) to the host root complex, which prompts the OS driver to dump headers. In an emulated user-space environment, we log the trace to let developers verify that their FM software is correctly mapping and reporting error injections.
* **Limitations:** No physical interrupts or signals are injected into the kernel of the host machine running OpenCIS.

---

### 3.4 MLD Port Direct Access (Opcodes 0x5301 - 0x5302)

#### Send LD CXL.io Configuration Request (0x5301)
* **Command Purpose:** Allows direct access to the configuration space of unbound Logical Devices (LDs) inside the Multi-Logical Device endpoint.
* **Simulated Values & Derivation:**
  * **Reads:** Return `0` (zeroed 4-byte payload).
  * **Writes:** Mocked as a success trace.
* **Architectural Rationale:** In the absence of an active host binding, configuration spaces are unmapped, returning default zero values.
* **Limitations:** Writing configuration registers does not modify PCIe capabilities, base address registers (BARs), or device capabilities.

#### Send LD CXL.io Memory Request (0x5302)
* **Command Purpose:** Performs direct memory-mapped read/write requests (MMIO) to unbound LD address ranges.
* **Simulated Values & Derivation:**
  * **Reads:** Return a block of zeroed bytes matching the length requested.
  * **Writes:** Logged trace.
* **Architectural Rationale:** Unbound logical devices do not have allocated physical address regions in host memory. Returning zeroed arrays prevents buffer index overflows in caller scripts.
* **Limitations:** Reading and writing do not perform physical CPU bus transactions and do not read/write to the guest host's RAM memory pages.

---

### 3.5 MLD Component QoS & Bandwidth Control (Opcodes 0x5403 - 0x5409)

* **Commands Covered:**
  * `Get/Set QoS Control` (0x5403, 0x5404)
  * `Get QoS Status` (0x5405)
  * `Get/Set QoS Allocated BW` (0x5406, 0x5407)
  * `Get/Set QoS BW Limit` (0x5408, 0x5409)
* **Command Purpose:** Controls and monitors bandwidth allocations, limits, and congestion metrics per Logical Device to enforce Quality of Service.
* **Simulated Values & Derivation:**
  * **QoS Control Defaults:** Moderate backpressure threshold: `10%`, Severe: `25%`, Sample Interval: `8`.
  * **QoS Status:** Returns a hardcoded link backpressure value of **`5%`**. This value was chosen as a healthy default baseline (sub-threshold) to prevent the virtual switch from thinking the link is congested and issuing throttling requests.
  * **Fractions and Limits:** Fractions are stored in a dictionary and returned on demand.
* **Architectural Rationale:** Modeling dynamic packet flow rates and queuing congestion in software requires heavy real-time timing measurements that are too CPU-intensive. Mocking these variables allows QoS discovery and control panels to test their user interfaces.
* **Limitations:** The bandwidth limits are stored but **do not rate-limit or throttle memory access speeds** inside OpenCIS.

---

### 3.6 Multi-Headed Device Links (Opcodes 0x5500 - 0x5501)

#### Get Multi-Headed Info (0x5500)
* **Command Purpose:** Returns mapping of physical interface ports (heads) to target logical devices on a shared multi-host endpoint.
* **Simulated Values & Derivation:**
  * Default number of LDs: `16`
  * Default physical heads: `4`
  * Default Mapping: Maps physical heads `0-3` sequentially to LDs `0-7` (2 LDs per head) to model a standard dual-port configuration per host context. Unassigned slots return `0xFF`.
* **Architectural Rationale:** Provides a standard multi-host layout profile for verification by cluster managers.
* **Limitations:** The mapping is a static model and cannot be modified dynamically at runtime.

#### Get Head Info (0x5501)
* **Command Purpose:** Retrieves speed, negotiated widths, and LTSSM states of physical interface links on the Multi-Headed device.
* **Simulated Values & Derivation:**
  * Negotiated Link Width: `16` (x16 lanes)
  * Supported Speeds Vector: `0x0F` (Gen 1, Gen 2, Gen 3, and Gen 4 support)
  * Max Link Speed / Current Speed: `4` (Gen 4 / 16GT/s)
  * LTSSM State: `4` (L0 - Link Active state)
  * Link Flags: `0` (Normal lane order, no resets)
* **Architectural Rationale:** These parameters match high-speed enterprise CXL Gen 4 controller hardware specifications, providing realistic link metrics.
* **Limitations:** They are static mock values and do not change if virtual host ports disconnect.

---

### 3.7 DCD Tag & Shared Memory References (Opcodes 0x5606 - 0x5608)

* **Commands Covered:**
  * `Dynamic Capacity Add Reference` (0x5606)
  * `Dynamic Capacity Remove Reference` (0x5607)
  * `Dynamic Capacity List Tags` (0x5608)
* **Command Purpose:** Manages shared memory allocation references. When multiple hosts share dynamic capacity blocks (extents), the device keeps reference tags to prevent sanitizing/deallocating shared memory until all hosts release their references.
* **Simulated Values & Derivation:**
  * Keeps track of tag reference counts and active/pending host bitmaps in a Python dictionary.
* **Architectural Rationale:** Emulates tag reference counting to verify coordination logic in multi-host dynamic capacity allocations.
* **Limitations:** Updating reference counts does not trigger actual physical page zeroing, page fault hooks, or security sanitization on memory boards.
