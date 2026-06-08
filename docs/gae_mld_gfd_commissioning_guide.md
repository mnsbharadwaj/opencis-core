# Static Commissioning Flow & GAE Command Reference
### CXL 4.0 Rev 1.0 Control Plane Commissioning Guide (1-MLD & 1-GFD Configuration)

This document outlines the step-by-step Fabric Manager (FM) commissioning flow and provides a detailed reference for all GAE (Generic Access Endpoint) commands under the following configuration:
- **Port 1:** Multi-Logical Device (MLD) supporting up to 16 Logical Devices (LDs).
- **Port 2:** Generic Fabric Device (GFD) with 4 KB BAR-0 MMIO.
- **VCS 0 / vPPB 0:** Upstream Virtual Port-Based Bridge to bind to the GFD.

---

## Part 1: Topology Diagram

```
                 Fabric Manager (FM)
             [TCP Port 8300 (Adapter)]
                        │
                        ▼
                [ PBR Switch ]
                ├── USP (Host Edge, VCS 0, vPPB 0) ──► GAE (Proxy Handler)
                ├── Port 1 (MLD)
                └── Port 2 (GFD)
```

---

## Part 2: The 13 Commissioning Commands

### 1. Identify PBR Switch (Opcode: `5700h`)
* **Purpose:** Discover PBR Switch capabilities and routing table capacities.
* **Request Payload:** None.
* **Key Response Fields:**
  * `num_drts`: `1` (Switch supports 1 DPID Routing Table).
  * `num_rgts`: `0` (Multicast Routing Group Tables).
  * `gae_support_map`: `0x1` (VCS 0 has a GAE).
  * `routing_caps`: Bitmask indicating supported port types and PBR capability.

### 2. Identify GAE (Opcode: `5800h`)
* **Purpose:** Learn about the Generic Access Endpoint (GAE) capabilities on the host upstream port.
* **Request Payload:** None.
* **Key Response Fields:**
  * `num_vppbs_with_gm_support`: `1` (VCS 0 exposes 1 virtual Port-Based Bridge supporting G-FAM).
  * `vppb_entries`: `[VppbGlobalMemorySupportInfo(vppb_id=0, global_memory_support=True)]`

### 3. Get LD Info (Opcode: `0101h`) — Targeted to Port 1
* **Purpose:** Query the Logical Device capabilities of the MLD connected to Port 1.
* **Request Payload:** None (routed to Port 1 via `port_index=1` TCP header).
* **Key Response Fields:**
  * `num_lds_supported`: `16` (MLD supports up to 16 Logical Devices).
  * `ld_allocations_remaining`: `16` (Number of LDs currently unassigned).

### 4. Get LD Allocations (Opcode: `0102h`) — Targeted to Port 1
* **Purpose:** Read current LD allocation map from the MLD on Port 1.
* **Request Payload:** None.
* **Key Response Fields:**
  * `number_of_lds`: `0` (Currently no LDs are statically allocated).
  * `ld_allocation_list`: Empty.

### 5. Set LD Allocations (Opcode: `0103h`) — Targeted to Port 1
* **Purpose:** Statically allocate 4 Logical Devices (LDs 0 to 3) on Port 1 for host binding.
* **Request Payload:**
  * `start_ld_id`: `0`
  * `number_of_lds`: `4`
  * `ld_allocation_list`: `[(1, 0), (1, 0), (1, 0), (1, 0)]` (Allocates 1 range unit to each LD).
* **Response:**
  * `return_code`: `SUCCESS` (LDs 0, 1, 2, 3 are now active).

### 6. Configure PID Assignment — Assign (Opcode: `5704h`)
* **Purpose:** Assign a 12-bit Port Identifier (PID) of `0x010` to Port 2 (the GFD device).
* **Request Payload:**
  * `operation`: `0` (`ASSIGN`)
  * `entries`: `[PidAssignmentEntry(pid=0x010, target_id=2, instance_id=0)]` (Note: target_id `2` = Port 2).
* **Response:**
  * `return_code`: `SUCCESS`

### 7. Set DRT (Opcode: `5709h`)
* **Purpose:** Program the DPID Routing Table (DRT) to route packets for destination PID `0x010` to physical Port 2.
* **Request Payload:**
  * `drt_index`: `0`
  * `start_entry`: `0x010` (PID assigned in step 6)
  * `entries`: `[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=2)]`
* **Response:**
  * `return_code`: `SUCCESS`
  * **Result:** The Switch's data plane router is now live for Port 2.

### 8. Get DRT (Opcode: `5708h`)
* **Purpose:** Read back and verify the DRT routing path.
* **Request Payload:**
  * `drt_index`: `0`
  * `start_entry`: `0x010`
  * `num_entries`: `1`
* **Response:**
  * `entries`: `[DrtEntry(entry_type=DrtEntryType.PHYSICAL_PORT, routing_target=2)]` (Verified).

### 9. Get PID Binding (Opcode: `5705h`) — Before Binding
* **Purpose:** Inspect the current binding status of VCS 0 / vPPB 0.
* **Request Payload:**
  * `target_vcs`: `0`
  * `target_vppb`: `0`
* **Response:**
  * `pid`: `0xFFF` (Returns `PID_UNASSIGNED` sentinel, confirming it is currently unbound).

### 10. Configure PID Binding — BIND (Opcode: `5706h`)
* **Purpose:** Stitch VCS 0 / vPPB 0 to the GFD's PID `0x010`.
* **Request Payload:**
  * `operation`: `0` (`BIND`)
  * `target_vcs`: `0`
  * `target_vppb`: `0`
  * `pid`: `0x010`
* **Response:**
  * `return_code`: `BACKGROUND_COMMAND_STARTED` (starts async binding task on the switch).
  * **Result:** Once complete, VCS 0 / vPPB 0 bindings point to PID `0x010`.

### 11. Get PID Access Vectors (Opcode: `5802h`)
* **Purpose:** Retrieve the access mask for the bound PID to ensure host endpoints have access rights.
* **Request Payload:**
  * `pid`: `0x010`
* **Key Response Fields:**
  * `pid`: `0x010`
  * `gmv`: `1` (Global Memory Vector - indicating memory plane access).
  * `vtv`: `1` (Virtualization Vector - indicating virtualization access).

### 12. Proxy GFD Management Command (Opcode: `5809h`)
* **Purpose:** Send an Identify GFD command (`0x0001`) from the host to the GFD via the GAE proxy.
* **Request Payload:**
  * `gfd_opcode`: `0x0001` (Identify GFD)
  * `gfd_payload`: None.
* **Response:**
  * `return_code`: `BACKGROUND_COMMAND_STARTED`
  * `thread_id`: `1` (ID of the proxy task running on the switch).

### 13. Get Proxy Thread Status (Opcode: `580Ah`)
* **Purpose:** Retrieve the GFD Identify response payload from GAE proxy thread `1`.
* **Request Payload:**
  * `thread_id`: `1`
* **Key Response Fields:**
  * `completed`: `True`
  * `gfd_return_code`: `SUCCESS` (0)
  * `gfd_response_payload`: Bytes matching GFD Identify response (`component_type=IdentifyComponentType.GFD (0x04)`, Vendor ID, etc.).

---

## Part 3: GAE Command Reference Deep-Dive

Here is the exact wire structure, payload layout, and programmatic usage for each GAE command in the CXL 4.0 specification:

### 1. Identify GAE (`5800h`)
* **Purpose:** Queries the GAE’s capabilities and virtual topology on the switch USP.
* **Request structure:** Empty payload (0 bytes).
* **Response structure:**
  * `Byte 0x00..0x01` (`uint16`): `num_vppbs_with_gm_support` (Number of virtual Port-Based Bridges supporting Global Fabric Attached Memory).
  * `Byte 0x02..0x03` (`uint16`): Reserved.
  * `Byte 0x04..varies`: Array of `GaeVppbInfo` structures (4 bytes each).
    * `Byte 0`: `vppb_id` (ID of the vPPB).
    * `Byte 1`: Bit 0 = `global_memory_support` flag; Bits 7:1 = Reserved.
    * `Byte 2..3`: Reserved.

### 2. Get PID Access Vectors (`5802h`)
* **Purpose:** Checks the authorization bitmasks (Global Memory Vector and Virtualization Vector) for a target GFD PID.
* **Request structure:**
  * `Byte 0x00..0x01` (`uint16`): `pid` (the 12-bit PID of the target GFD).
* **Response structure:**
  * `Byte 0x00..0x01` (`uint16`): `pid` (mirrored).
  * `Byte 0x02` (`uint8`): `gmv` (Global Memory Vector bitmask: 1 = allowed, 0 = blocked).
  * `Byte 0x03` (`uint8`): `vtv` (Virtualization Vector bitmask: 1 = allowed, 0 = blocked).

### 3. Proxy GFD Management Command (`5809h`)
* **Purpose:** Initiates a proxied CCI command to a GFD on a downstream port.
* **Request structure:**
  * `Byte 0x00..0x01` (`uint16`): `gfd_opcode` (Opcode of the command to execute on the GFD, e.g., `0x0001` for GFD Identify).
  * `Byte 0x02..0x03` (`uint16`): `gfd_payload_len` (Length of payload bytes).
  * `Byte 0x04..varies`: `gfd_payload` (Verbatim payload bytes to pass to GFD).
* **Response structure:**
  * `Byte 0x00..0x01` (`uint16`): `thread_id` (allocated proxy task identifier).
  * **Note:** This is a background command. It always returns `BACKGROUND_COMMAND_STARTED` immediately.

### 4. Get Proxy Thread Status (`580Ah`)
* **Purpose:** Polls the status and collects response data from an active proxy task.
* **Request structure:**
  * `Byte 0x00..0x01` (`uint16`): `thread_id` (task identifier returned in `5809h`).
* **Response structure:**
  * `Byte 0x00` (`uint8`): `completed` flag (`1` = complete, `0` = running).
  * `Byte 0x01` (`uint8`): Reserved.
  * `Byte 0x02..0x03` (`uint16`): `gfd_return_code` (CCI return code from the GFD device, e.g., `SUCCESS` (0) or error code).
  * `Byte 0x04..varies`: `gfd_response_payload` (VERBATIM response bytes returned by the GFD).

### 5. Cancel Proxy Thread (`580Bh`)
* **Purpose:** Requests the immediate termination of an active proxy task.
* **Request structure:**
  * `Byte 0x00..0x01` (`uint16`): `thread_id` (the target proxy task to cancel).
* **Response structure:**
  * `Byte 0x00..0x01` (`uint16`): `thread_id` (mirrored).
  * `return_code`: `SUCCESS` if cancelled or task not running, error otherwise.

### 6. Fabric Crawl Out (`5701h`)
* **Purpose:** Tunnels a CCI command directly to any downstream port device through the switch DSP mailbox.
* **Request structure:**
  * `Byte 0x00` (`uint8`): `port_index` (physical DSP port to crawl out to).
  * `Byte 0x01` (`uint8`): Reserved.
  * `Byte 0x02..0x03` (`uint16`): `gfd_opcode` (target command opcode).
  * `Byte 0x04..varies`: `gfd_payload` (CCI request bytes).
* **Response structure:**
  * `Byte 0x00..0x01` (`uint16`): `gfd_return_code` (mirrored).
  * `Byte 0x02..varies`: `gfd_response_payload` (response bytes).
