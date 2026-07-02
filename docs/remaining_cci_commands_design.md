# Design Document: CXL Switch, MLD, & DCD CCI Commands

This document provides a comprehensive design reference for the CXL Switch, MLD, and DCD Fabric Manager (FM) API CCI commands. It highlights the distinction between the **OpenCIS Simulation-Only path** and the **QEMU Hardware Emulation path**, complete with detailed code flow diagrams for each command category.

---

## 1. Scope & System Architecture

The scope of this implementation is to support standard CXL Switch, MLD, and DCD commands (opcode range `5100h` to `5605h` + Generic status commands). PBR Switch commands (`57xxh`) and GAE/GFA commands (`58xxh`) are out of scope.

### Code Flow Overview
```
       [Fabric Manager CLI / MCTP Client]
                       │
                       ▼ (CCI Request Packet)
              [FmMctpCciServer]
                       │
                       ▼ (Raw Payload Packet)
             [MctpCciApiClient]
                       │
                       ▼ (TCP / Port 8300)
              [MctpCciExecutor]
                       │
                       ▼ (Resolve Opcode)
         [Switch CCI Command Executor] 
           /           │           \
          ▼            ▼            ▼
   [Physical Switch] [Virtual Switch] [DCD / MLD State]
```

---

## 2. Command Reference & Code Flows

---

### 2.1 Physical Switch Commands (Group 51h)

#### 2.1.1 Identify Switch Device (0x5100) & Get Physical Port State (0x5101)
* **Simulation Flow (OpenCIS)**: Directly queries active port configurations inside the switch's `PhysicalPortManager` and returns connection metrics.
* **QEMU Emulation Flow**: Reads registers exposed by QEMU's PCI device representation (`PCIDevice` state).
* **Code Flow**:
```
FM ──► GetPhysicalPortState(port_id) ──► PhysicalPortManager ──► Retrieve Port State ──► FM (SUCCESS)
```

#### 2.1.2 Physical Port Control (0x5102)
* **Simulation Flow (OpenCIS)**: Performs a logical reset or assertion/deassertion of PERST in memory (e.g. setting `port.enabled = False`).
* **QEMU Emulation Flow**: Triggers an actual virtual system reset signal (e.g. `pci_device_reset()`) to reinitialize the backing virtual device.
* **Code Flow**:
```
FM ──► PhysicalPortControl(ASSERT_PERST) ──► PhysicalPortManager ──► Set port.enabled = False ──► FM (SUCCESS)
```

#### 2.1.3 Send PPB CXL.io Configuration Request (0x5103)
* **Simulation Flow (OpenCIS)**: Reads/writes directly to the in-memory config register arrays of the `PpbDevice` mapped to the target port.
* **QEMU Emulation Flow**: Passes PCIe config-space reads/writes directly to QEMU's PCI bus structures (`pci_default_read_config` / `pci_default_write_config`).
* **Code Flow**:
```
FM ──► SendPpbCxlIoConfig(Read Reg 0) ──► PhysicalPortManager ──► PpbDevice.read_bytes() ──► FM (SUCCESS + Data)
```

---

### 2.2 Virtual Switch Commands (Group 52h)

#### 2.2.1 Get Virtual CXL Switch Info (0x5200), Bind vPPB (0x5201), & Unbind vPPB (0x5202)
* **Simulation Flow (OpenCIS)**: Updates the binding maps inside `VirtualSwitchManager` and notifies the routing loop to dynamically link/unlink packet queues.
* **QEMU Emulation Flow**: Modifies PCI hot-plug bindings dynamically. Binding triggers QEMU virtual bus hot-plug events (`qdev_device_add`).
* **Code Flow**:
```
FM ──► BindVppb(vPPB_1, Port_2) ──► VirtualSwitchManager ──► Update Bind Maps ──► Update Queue Links ──► FM
```

#### 2.2.2 Generate AER Event (0x5203)
* **Simulation Flow (OpenCIS)**: Log-only/simulated event. Emulates event generation by asserting the IRQ line associated with the target vPPB.
* **QEMU Emulation Flow**: Writes to QEMU's virtual device AER registers (`pcie_aer_write_config`) and raises an actual MSI-X interrupt to the guest OS.
* **Code Flow**:
```
FM ──► GenerateAerEvent(VCS0, vPPB1, FATAL) ──► VirtualSwitchManager ──► Trigger IRQ line ──► FM (SUCCESS)
```

#### 2.2.3 Freeze vPPB (0x5215) & Unfreeze vPPB (0x5216)
* **Simulation Flow (OpenCIS)**: Toggles a logical traffic gate inside `VirtualSwitch` to stop forwarding incoming CXL packets.
* **QEMU Emulation Flow**: Pauses or queues backing memory mapping ring-buffer requests.
* **Code Flow**:
```
FM ──► FreezeVppb(vPPB_1) ──► VirtualSwitch ──► Set traffic_gate = FROZEN ──► FM (SUCCESS)
```

---

### 2.3 Tunneling & MLD Info (Group 53h & 54h)

#### 2.3.1 Tunnel Management Command (0x5300)
* **Simulation Flow (OpenCIS)**: Fetches the connected device connection queue and puts the packet payload directly on `cci_fifo.host_to_target`.
* **QEMU Emulation Flow**: Routes the MCTP packet to the target virtual PCIe endpoint's physical mailbox interface.
* **Code Flow**:
```
FM ──► TunnelMgmtCmd(Port_1, Payload) ──► Switch ──► port_connection.cci_fifo ──► Endpoint (Process)
                                                                                     │
FM ◄── Return Tunnel Payload ◄── Switch ◄── cci_fifo.target_to_host ◄────────────────┘
```

#### 2.3.2 Send LD CXL.io Config (0x5301) & Send LD CXL.io Memory (0x5302)
* **Simulation Flow (OpenCIS)**: Forwards the config/memory packet to the downstream connection's queue after appending the target `ld_id` tag.
* **QEMU Emulation Flow**: Uses IOMMU context mapping or specific PCIe requester IDs (RIDs) to isolate the target LD within virtualized host spaces.
* **Code Flow**:
```
FM ──► SendLdConfig(ld_id=2, data) ──► Switch ──► Wrap with LD ID ──► target_connection.cfg_fifo ──► Device
```

#### 2.3.3 Get LD Info (0x5400) & Get LD Allocations (0x5401)
* **Simulation Flow (OpenCIS)**: Reads logical division limits (e.g. partition offsets) and active mappings directly from `VirtualSwitchManager`.
* **QEMU Emulation Flow**: Queries host system namespaces representing disk or virtual memory block mounts.
* **Code Flow**:
```
FM ──► GetLdAllocations(LD_0) ──► Switch ──► Query VirtualSwitchManager ──► Return DPA Base/Length ──► FM
```

---

### 2.4 QoS Control & Bandwidth (Group 54h)

#### 2.4.1 Get/Set QoS Control (0x5403, 0x5404), Get QoS Status (0x5405), Get/Set Bandwidth Limits (0x5406–0x5409)
* **Simulation Flow (OpenCIS)**: **Simulation-Only**. The switch maintains an in-memory database of bandwidth caps and throttling status fields. Commands read/write from this map and return `SUCCESS`. No active scheduling or hardware throttling is performed.
* **QEMU Emulation Flow**: Intercepts QoS settings and configures system cgroups (`io.weight`/`io.max`) or block I/O throttle limits on the host backing devices to restrict actual IOPS/bandwidth.
* **Code Flow**:
```
FM ──► SetQosAllocatedBandwidth(LD_1, 500MB/s) ──► Switch ──► Update qos_limits[LD_1] = 500 ──► FM (SUCCESS)
```

---

### 2.5 DCD Management (Group 56h)

#### 2.5.1 Get DCD Info (0x5600) & Get Host DC Region Configuration (0x5601)
* **Simulation Flow (OpenCIS)**: Maps regions by querying the memory capacity bounds declared in the switch's `device_configs` for the active port.
* **QEMU Emulation Flow**: Reads dynamic capacity configuration structures declared in the backing device backend parameters (`-device cxl-type3,dc-regions=...`).
* **Code Flow**:
```
FM ──► GetDcdInfo(port_id=1) ──► Switch ──► Lookup device_configs[port_id] ──► Return Region Configs ──► FM
```

#### 2.5.2 Set DC Region Configuration (0x5602) & Get DC Region Extent Lists (0x5603)
* **Simulation Flow (OpenCIS)**: Returns default, spec-compliant empty/initialized lists from memory.
* **QEMU Emulation Flow**: Modifies active memory mappings dynamically via host virtual memory subsystems.
* **Code Flow**:
```
FM ──► GetExtentLists(Region_0) ──► Switch ──► Read local extent list ──► FM (SUCCESS + Extents)
```

#### 2.5.3 Initiate Dynamic Capacity Add (0x5604) & Initiate Dynamic Capacity Release (0x5605)
* **Simulation Flow (OpenCIS)**: **Simulation-Only**. Accepts the target allocation parameters (DPA range, Tag, Shared Sequence), verifies syntax, logs the action, and returns `SUCCESS` immediately to emulate complete control plane handshakes.
* **QEMU Emulation Flow**: Communicates with the VM kernel's memory-balloon driver or dev-dax driver to dynamically allocate/release backing host physical memory pages to the guest's kernel space.
* **Code Flow**:
```
FM ──► InitiateDcAdd(DPA=0x0, Len=256MB) ──► Switch ──► Log Event ──► Return SUCCESS ──► FM (Handshake Ok)
```
