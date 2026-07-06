# CXL Fabric Manager API: Implementation Details & Limitations

This document provides a detailed breakdown of the 36 CXL Fabric Manager (FM) API CCI commands implemented or supported in the OpenCIS repository. It categorizes each command based on whether its execution is fully functional (straightforward integration) or emulated using simulated/mocked values, along with its specific limitations.

---

## 1. Summary of Command Classifications

* **Fully Functional Commands**: **15 commands** (Directly integrated with the OpenCIS virtual switch state machines, resource managers, or dynamic capacity device bounds).
* **Simulated/Mocked Commands**: **21 commands** (Parse and validate specifications correctly, but execute mock behaviors, return static values, or trace simulator logging because the underlying hardware actions are not modeled in CPU emulation).

---

## 2. Detailed Command Classifications

### Group A: Fully Functional Commands (15 Commands)

These commands interact directly with active manager objects in OpenCIS (`PhysicalPortManager`, `VirtualSwitchManager`, `MldManager`) to query or modify live emulated states:

| Opcode | Command Name | Functional Logic in OpenCIS | Realized Effects |
| :--- | :--- | :--- | :--- |
| **0x5100** | **Identify Switch Device** | Queries `PhysicalPortManager` and `VirtualSwitchManager`. | Returns exact counts of active ports, VCS instances, and vPPB binding slots. |
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

### Group B: Simulated / Mocked Commands (21 Commands)

These commands validate, parse, and respond correctly according to spec payloads, but use static or mocked variables instead of real hardware implementations:

| Opcode | Command Name | Simulated Values / Mock Logic | Limitations |
| :--- | :--- | :--- | :--- |
| **0x5102** | **Physical Port Control** | Parses port ID and opcode; traces debug events. | Does not assert physical sideband/reset pins (e.g., PERST#) on virtual boards. |
| **0x5103** | **Send PPB CXL.io Config** | Simulates read/write, returns static `0` on reads. | Does not issue real PCIe configuration cycles to target physical downstream ports. |
| **0x5104** | **Get Domain Validation SV State**| Tracks whether validation SV is set (0x01) or not (0x00). | Stored in memory, but no hardware validation block enforces access control. |
| **0x5105** | **Set Domain Validation SV** | Stores a 16-byte UUID in a local variable. | Standard spec constraint (no double-setting) is enforced, but no security lock occurs. |
| **0x5106** | **Get VCS Domain Validation State**| Checks if the domain key is set for the VCS ID. | Static tracking; does not perform cryptographic checks. |
| **0x5107** | **Get Domain Validation SV** | Returns the stored 16-byte UUID. | Only retrieves the value stored by the mock database. |
| **0x5203** | **Generate AER Event** | Unpacks VCS ID, status, and 32-byte header. | Logs simulated AER event to the switch trace; does not issue host interrupts. |
| **0x5301** | **Send LD CXL.io Config** | Logs headers, returns static `0` on configuration reads. | Does not query real guest configuration tables. |
| **0x5302** | **Send LD CXL.io Memory** | Returns zeroed bytes matching requested length on reads. | Does not write to/read from real physical RAM bus lines. |
| **0x5403** | **Get QoS Control** | Returns static simulated values (e.g., moderate limit: 10%, severe: 25%). | Values can be read, but no actual traffic throttling is performed. |
| **0x5404** | **Set QoS Control** | Updates moderate/severe values in Python database. | Programmed thresholds are stored but not enforced by the virtual memory bus. |
| **0x5405** | **Get QoS Status** | Returns hardcoded average backpressure: **`5%`**. | Static feedback; does not reflect real-time switch port congestion. |
| **0x5406** | **Get QoS Allocated BW** | Reads bandwidth fractions per LD from dictionary. | Retrieves mock fractions only. |
| **0x5407** | **Set QoS Allocated BW** | Stores bandwidth fractions per LD in dictionary. | Values do not constrain the throughput of memory traffic in the simulator. |
| **0x5408** | **Get QoS BW Limit** | Reads bandwidth limits per LD from dictionary. | Retrieves mock limits only. |
| **0x5409** | **Set QoS BW Limit** | Stores bandwidth limits per LD in dictionary. | Limits are not enforced on virtual host controllers. |
| **0x5500** | **Get Multi-Headed Info** | Returns hardcoded mappings: 16 LDs mapped to 4 heads. | Map is static and cannot be modified. |
| **0x5501** | **Get Head Info** | Returns hardcoded Gen 4 speed, x16 width, and L0 LTSSM. | Does not query real link controllers. |
| **0x5606** | **DC Add Reference** | Tracks reference counts for memory tags. | Stored in Python dictionaries; does not lock real hardware pages. |
| **0x5607** | **DC Remove Reference** | Decrements reference counts in dictionary. | Does not trigger physical page zeroing or sanitizer routines. |
| **0x5608** | **Dynamic Capacity List Tags** | Lists tag structures and reference bitmaps. | Retrieves database state but does not restrict real memory allocations. |
