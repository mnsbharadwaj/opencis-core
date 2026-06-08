# GFD, GAE, & PBR Switch Design Validation Report

**Scope:** GFD Device, GAE Manager, PBR Switch, and MCTP/SMBus FM Interfaces.  
**Validation Summary:** 199 targeted unit tests passing successfully (352 total project tests).  
**Date:** June 2026  

---

## 1. GFD & GAE Commissioning Commands in Detail

The following section describes the inputs, outputs, and control-plane behaviors for the CXL 4.0 commissioning commands used by the Fabric Manager (FM) to set up and proxy Generic Fabric Devices (GFD) and Generic Access Endpoints (GAE).

### 1.1 Identify PBR Switch (0x5700)
* **Description**: FM queries the switch to read metadata capabilities, VCS mappings, and GAE support vectors.
* **Inputs (Payload In)**: None (0 bytes).
* **Outputs (Payload Out)**:
  - `num_drts` (1 byte): Number of Dynamic Routing Tables.
  - `num_rgts` (1 byte): Number of Routing Group Tables.
  - `routing_modes` (2 bytes): Supported routing modes bitmask.
  - `gae_support_map` (8 bytes): Bitmask indicating GAE support for each VCS ID.
  - `routing_caps` (1 byte): Routing capabilities flags.

### 1.2 Configure PID Assignment (0x5704)
* **Description**: Assigns or clears a 12-bit Port Identifier (PID) to/from a switch target port.
* **Inputs (Payload In)**:
  - `operation` (1 byte): `0 = ASSIGN`, `1 = CLEAR`.
  - `num_entries` (1 byte): Number of assignment entries in payload.
  - `entries` (Variable length): List of entries containing:
    - `pid` (2 bytes): 12-bit PID value.
    - `target_id` (1 byte): Target Port ID.
    - `instance_id` (1 byte): Instance ID.
* **Outputs (Payload Out)**: None (standard return code in CCI header).

### 1.3 Set DRT (0x5709)
* **Description**: Programs/writes routing instructions to the Dynamic Routing Table (DRT) to map a PID to an egress port.
* **Inputs (Payload In)**:
  - `drt_index` (1 byte): Index of target DRT table.
  - `start_entry` (2 bytes): Starting index entry in DRT.
  - `num_entries` (2 bytes): Number of entries to set.
  - `entries` (Variable length): List of `DrtEntry` containing:
    - `entry_type` (1 byte): `0 = INVALID`, `1 = PHYSICAL_PORT`, `2 = RGT_INDEX`, `3 = RESERVED`.
    - `routing_target` (1 byte): Egress port or RGT index.
* **Outputs (Payload Out)**: None (standard return code in CCI header).

### 1.4 Get DRT (0x5708)
* **Description**: Reads entry mappings from the Dynamic Routing Table (DRT) for verification.
* **Inputs (Payload In)**:
  - `drt_index` (1 byte): Index of target DRT table.
  - `start_entry` (2 bytes): Starting index entry in DRT.
  - `num_entries` (2 bytes): Number of entries to retrieve.
* **Outputs (Payload Out)**:
  - `num_entries` (2 bytes): Number of entries returned.
  - `entries` (Variable length): List of `DrtEntry` (type, target).

### 1.5 Get PID Binding (0x5705)
* **Description**: FM queries the current PID bound to a specific Virtual CXL Switch (vCS) and Virtual Port-Based Bridge (vPPB).
* **Inputs (Payload In)**:
  - `target_vcs` (1 byte): Virtual CXL Switch ID.
  - `target_vppb` (1 byte): Virtual Port-Based Bridge ID.
* **Outputs (Payload Out)**:
  - `pid` (2 bytes): Bound 12-bit PID (returns `0xFFF` if unbound).

### 1.6 Configure PID Binding (0x5706) [Background Command]
* **Description**: Binds or unbinds a vCS/vPPB slot to a physical PID.
* **Inputs (Payload In)**:
  - `operation` (1 byte): `0 = BIND`, `1 = UNBIND`.
  - `target_vcs` (1 byte): Virtual CXL Switch ID.
  - `target_vppb` (1 byte): Virtual Port-Based Bridge ID.
  - `pid` (2 bytes): 12-bit PID value to bind.
* **Outputs (Payload Out)**: None (reports background status updates).

### 1.7 Identify GAE (0x5800)
* **Description**: Query GAE capabilities at the host-edge USP.
* **Inputs (Payload In)**: None.
* **Outputs (Payload Out)**:
  - `num_vppbs` (1 byte): Number of vPPBs associated with the GAE.
  - `num_pid_access_vectors` (2 bytes): Number of access vector entries.
  - `gfam_die_support` (1 byte): GFAM die-sharing capability flag.

### 1.8 Get PID Access Vectors (0x5802)
* **Description**: Read access permission vectors associated with a PID.
* **Inputs (Payload In)**:
  - `pid` (2 bytes): Target 12-bit PID.
* **Outputs (Payload Out)**:
  - `pid` (2 bytes): Target PID.
  - `gmv` (8 bytes): Global Memory Vector.
  - `vtv` (8 bytes): Virtualization Vector.

### 1.9 Proxy GFD Mgmt Command (0x5809) [Background Command]
* **Description**: Proxies a CCI command from GAE to a downstream GFD device.
* **Inputs (Payload In)**:
  - `gfd_opcode` (2 bytes): CCI opcode intended for GFD.
  - `gfd_payload` (Variable): Raw payload bytes for the GFD command.
* **Outputs (Payload Out)**:
  - `thread_id` (2 bytes): Background task/thread handle identifier.

### 1.10 Get Proxy Thread Status (0x580A)
* **Description**: Query the status/results of a previously started GAE-to-GFD proxy command thread.
* **Inputs (Payload In)**:
  - `thread_id` (2 bytes): Target thread identifier.
* **Outputs (Payload Out)**:
  - `completed` (1 byte): `True` (completed) or `False` (pending).
  - `gfd_return_code` (2 bytes): Return code from the GFD device execution.
  - `gfd_response_payload` (Variable): Raw output payload bytes returned by GFD.

### 1.11 Cancel Proxy Thread (0x580B)
* **Description**: Terminates a running background GAE-to-GFD proxy task.
* **Inputs (Payload In)**:
  - `thread_id` (2 bytes): Target thread identifier.
* **Outputs (Payload Out)**: None.

### 1.12 Fabric Crawl Out (0x5701)
* **Description**: Tunnel a CCI command from the FM down to a GFD connected via a physical port using DSP CCI FIFOs.
* **Inputs (Payload In)**:
  - `port_id` (1 byte): Downstream Physical Port ID.
  - `embedded_opcode` (2 bytes): CCI opcode.
  - `embedded_payload` (Variable): Embedded command payload.
* **Outputs (Payload Out)**:
  - `embedded_return_code` (2 bytes): Return code from device.
  - `embedded_response_payload` (Variable): Payload returned.

---

## 2. Focused Unit Test Count by Component

The following table lists the unit test files directly validating the Port-Based Routing (PBR) Switch, Generic Fabric Device (GFD), and Generic Access Endpoint (GAE) designs.

| Test File | Number of Test Cases | Focus Area |
| :--- | :---: | :--- |
| `test_pbr_switch_command_set.py` | 45 | PBR Switch Manager, DRT, and CCI Commands |
| `test_gae_commands.py` | 20 | GAE CCI Commands (Opcodes 0x5800 - 0x580B) |
| `test_gae_control_plane.py` | 25 | GAE Manager & Proxy Threads Lifecycle |
| `test_gae_proxy_management.py` | 20 | GAE Proxy Tunnel & Mock Executor |
| `test_fabric_crawl_out.py` | 17 | Fabric Crawl Out Command (Opcode 0x5701) |
| `test_mctp_fm_port_integration.py` | 15 | MCTP Port 8300 CCI Command Forwarding |
| `test_mctp_fm_port.py` | 11 | MCTP Fabric Manager CCI Port (Port 8300) Adapter |
| `test_gfd_live_switch.py` | 10 | GFD & PBR Switch Live Integration |
| `test_smbus_dual_port_pbr_cmds.py` | 7 | PBR CCI Commands over SMBus Dual Port |
| `test_smbus_mctp_server_pbr_cmds.py` | 7 | PBR CCI Commands via SMBus MCTP Server |
| `test_gfd_device.py` | 6 | Generic Fabric Device (GFD) Device Mailbox Logic |
| `test_host_gae_proxy.py` | 6 | Host-side GAE Proxy mailbox commands routing |
| `test_gae_tcp_integration.py` | 4 | GAE Proxy Routing over TCP Sockets |
| `test_pbr_qemu_e2e.py` | 3 | PBR QEMU Data Plane E2E Ingress & Egress |
| `test_pbr_data_plane.py` | 2 | PBR Switch Data Plane Router & Address Resolution |
| `test_pbr_packet_serialization.py` | 1 | HBR-to-PBR Flit Packaging & Serialization |
| **TOTAL TARGETED TESTS** | **199** | |

---

## 3. Complete Code Coverage Report

The following is the complete code coverage report for all files across the `opencis-core` codebase:

| Source File | Statements | Misses | Branches | Branch Misses | Coverage % |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `opencis/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/apps/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/apps/accelerator.py` | 391 | 391 | 50 | 0 | **0%** |
| `opencis/apps/cxl_simple_host.py` | 89 | 36 | 16 | 4 | **54%** |
| `opencis/apps/cxl_switch.py` | 130 | 24 | 42 | 13 | **72%** |
| `opencis/apps/fabric_manager.py` | 84 | 84 | 6 | 0 | **0%** |
| `opencis/apps/generic_fabric_device.py` | 34 | 1 | 6 | 1 | **95%** |
| `opencis/apps/memory_pooling.py` | 189 | 189 | 56 | 0 | **0%** |
| `opencis/apps/multi_logical_device.py` | 202 | 202 | 74 | 0 | **0%** |
| `opencis/apps/multiheaded_single_logical_device.py` | 31 | 31 | 10 | 0 | **0%** |
| `opencis/apps/packet_trace_runner.py` | 41 | 41 | 10 | 0 | **0%** |
| `opencis/apps/pci_device.py` | 21 | 21 | 0 | 0 | **0%** |
| `opencis/apps/single_logical_device.py` | 33 | 33 | 6 | 0 | **0%** |
| `opencis/bin/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/bin/accelerator.py` | 33 | 33 | 6 | 0 | **0%** |
| `opencis/bin/cli.py` | 196 | 196 | 62 | 0 | **0%** |
| `opencis/bin/common.py` | 14 | 14 | 4 | 0 | **0%** |
| `opencis/bin/cxl_host.py` | 46 | 46 | 10 | 0 | **0%** |
| `opencis/bin/cxl_switch.py` | 27 | 27 | 0 | 0 | **0%** |
| `opencis/bin/cxl_type1_test.py` | 72 | 72 | 6 | 0 | **0%** |
| `opencis/bin/cxl_type2_test.py` | 86 | 86 | 10 | 0 | **0%** |
| `opencis/bin/cxl_type3_test.py` | 86 | 86 | 10 | 0 | **0%** |
| `opencis/bin/fabric_manager.py` | 162 | 162 | 4 | 0 | **0%** |
| `opencis/bin/generic_fabric_device.py` | 31 | 31 | 2 | 0 | **0%** |
| `opencis/bin/get_info.py` | 14 | 14 | 0 | 0 | **0%** |
| `opencis/bin/mem.py` | 41 | 41 | 2 | 0 | **0%** |
| `opencis/bin/multi_logical_device.py` | 64 | 64 | 8 | 0 | **0%** |
| `opencis/bin/packet_runner.py` | 15 | 15 | 0 | 0 | **0%** |
| `opencis/bin/single_logical_device.py` | 37 | 37 | 4 | 0 | **0%** |
| `opencis/bin/socketio_client.py` | 157 | 157 | 2 | 0 | **0%** |
| `opencis/bin/test_external_switch_cxl.py` | 59 | 59 | 12 | 0 | **0%** |
| `opencis/bin/test_external_switch_pcie.py` | 44 | 44 | 8 | 0 | **0%** |
| `opencis/cpu.py` | 73 | 73 | 16 | 0 | **0%** |
| `opencis/cxl/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/common.py` | 181 | 29 | 12 | 0 | **83%** |
| `opencis/cxl/cci/fabric_manager/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/dcd_management/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/dcd_management/dc_region.py` | 208 | 208 | 20 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/dcd_management/get_dcd_info.py` | 80 | 80 | 2 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/dcd_management/initiate_dynamic_capacity.py` | 128 | 128 | 12 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/gae/__init__.py` | 7 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/gae/cancel_proxy_thread.py` | 36 | 4 | 2 | 1 | **87%** |
| `opencis/cxl/cci/fabric_manager/gae/fabric_crawl_out.py` | 139 | 77 | 20 | 0 | **39%** |
| `opencis/cxl/cci/fabric_manager/gae/get_pid_access_vectors.py` | 64 | 4 | 4 | 2 | **91%** |
| `opencis/cxl/cci/fabric_manager/gae/get_proxy_thread_status.py` | 72 | 5 | 8 | 2 | **91%** |
| `opencis/cxl/cci/fabric_manager/gae/identify_gae.py` | 69 | 3 | 8 | 3 | **92%** |
| `opencis/cxl/cci/fabric_manager/gae/proxy_gfd_mgmt.py` | 68 | 6 | 10 | 3 | **88%** |
| `opencis/cxl/cci/fabric_manager/mld_components/__init__.py` | 3 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/mld_components/get_ld_allocations.py` | 87 | 37 | 6 | 0 | **54%** |
| `opencis/cxl/cci/fabric_manager/mld_components/get_ld_info.py` | 54 | 19 | 2 | 0 | **62%** |
| `opencis/cxl/cci/fabric_manager/mld_components/set_ld_allocations.py` | 86 | 53 | 22 | 0 | **31%** |
| `opencis/cxl/cci/fabric_manager/mld_port/__init__.py` | 1 | 1 | 0 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/mld_port/tunnel_management.py` | 35 | 35 | 4 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/multi_headed_devices/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/__init__.py` | 6 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_assignment.py` | 82 | 7 | 14 | 3 | **90%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/configure_pid_binding.py` | 71 | 7 | 4 | 2 | **88%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/get_drt.py` | 92 | 6 | 12 | 3 | **91%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/get_pid_binding.py` | 73 | 7 | 6 | 2 | **89%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/identify_pbr_switch.py` | 45 | 1 | 2 | 1 | **96%** |
| `opencis/cxl/cci/fabric_manager/pbr_switch/set_drt.py` | 58 | 6 | 8 | 2 | **88%** |
| `opencis/cxl/cci/fabric_manager/physical_switch/__init__.py` | 2 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/physical_switch/get_physical_port_state.py` | 185 | 86 | 18 | 0 | **49%** |
| `opencis/cxl/cci/fabric_manager/physical_switch/identify_switch_device.py` | 67 | 35 | 2 | 0 | **46%** |
| `opencis/cxl/cci/fabric_manager/physical_switch/physical_port_control.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/physical_switch/send_ppb_cxl_io_configuration_request.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/__init__.py` | 5 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/bind_vppb.py` | 72 | 43 | 10 | 0 | **35%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/freeze_vppb.py` | 56 | 30 | 8 | 0 | **41%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/generate_aer_event.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/get_virtual_cxl_switch_info.py` | 159 | 76 | 14 | 0 | **48%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/tunnel_management.py` | 32 | 32 | 0 | 0 | **0%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/unbind_vppb.py` | 59 | 34 | 8 | 0 | **37%** |
| `opencis/cxl/cci/fabric_manager/virtual_switch/unfreeze_vppb.py` | 56 | 30 | 8 | 0 | **41%** |
| `opencis/cxl/cci/generic/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/events.py` | 147 | 69 | 20 | 0 | **47%** |
| `opencis/cxl/cci/generic/features.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/firmware_update.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/information_and_status/__init__.py` | 2 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/information_and_status/background_command_status.py` | 58 | 25 | 2 | 0 | **55%** |
| `opencis/cxl/cci/generic/information_and_status/identify.py` | 58 | 4 | 2 | 1 | **92%** |
| `opencis/cxl/cci/generic/information_and_status_command_set.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/logs.py` | 62 | 42 | 12 | 0 | **27%** |
| `opencis/cxl/cci/generic/maintenance.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/generic/timestamp.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/memory_device/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/memory_device/dynamic_capacity.py` | 182 | 98 | 18 | 0 | **42%** |
| `opencis/cxl/cci/memory_device/identify_memory_device.py` | 14 | 8 | 2 | 0 | **38%** |
| `opencis/cxl/cci/vendor_specfic/__init__.py` | 4 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/vendor_specfic/get_connected_devices.py` | 97 | 44 | 12 | 0 | **49%** |
| `opencis/cxl/cci/vendor_specfic/notify_device_update.py` | 10 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/cci/vendor_specfic/notify_port_update.py` | 29 | 7 | 2 | 0 | **71%** |
| `opencis/cxl/cci/vendor_specfic/notify_switch_update.py` | 33 | 14 | 2 | 0 | **54%** |
| `opencis/cxl/component/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/bi_decoder.py` | 157 | 38 | 14 | 2 | **71%** |
| `opencis/cxl/component/bind_processor.py` | 45 | 12 | 6 | 0 | **69%** |
| `opencis/cxl/component/cache_controller.py` | 354 | 354 | 106 | 0 | **0%** |
| `opencis/cxl/component/cache_id_decoder_capability.py` | 70 | 33 | 18 | 0 | **42%** |
| `opencis/cxl/component/cache_route_table.py` | 106 | 38 | 20 | 0 | **54%** |
| `opencis/cxl/component/cache_route_table_manager.py` | 36 | 18 | 4 | 0 | **45%** |
| `opencis/cxl/component/cci_executor.py` | 139 | 17 | 12 | 2 | **87%** |
| `opencis/cxl/component/common.py` | 15 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/cxl_bridge_component.py` | 59 | 16 | 2 | 0 | **70%** |
| `opencis/cxl/component/cxl_cache_dcoh.py` | 196 | 196 | 86 | 0 | **0%** |
| `opencis/cxl/component/cxl_cache_manager.py` | 9 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/cxl_component.py` | 50 | 7 | 0 | 0 | **86%** |
| `opencis/cxl/component/cxl_connection.py` | 8 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/cxl_host.py` | 66 | 66 | 8 | 0 | **0%** |
| `opencis/cxl/component/cxl_io_callback_data.py` | 8 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/cxl_io_manager.py` | 24 | 3 | 0 | 0 | **88%** |
| `opencis/cxl/component/cxl_mem_dcoh.py` | 238 | 238 | 120 | 0 | **0%** |
| `opencis/cxl/component/cxl_mem_manager.py` | 84 | 61 | 28 | 0 | **21%** |
| `opencis/cxl/component/cxl_memory_device_component.py` | 195 | 104 | 16 | 0 | **43%** |
| `opencis/cxl/component/cxl_memory_hub.py` | 126 | 126 | 20 | 0 | **0%** |
| `opencis/cxl/component/cxl_packet_processor.py` | 397 | 204 | 178 | 37 | **44%** |
| `opencis/cxl/component/device_llc_iogen.py` | 65 | 65 | 14 | 0 | **0%** |
| `opencis/cxl/component/dsp_cci_tunnel.py` | 75 | 13 | 12 | 6 | **78%** |
| `opencis/cxl/component/fabric_manager/socketio_server.py` | 694 | 694 | 230 | 0 | **0%** |
| `opencis/cxl/component/fmld.py` | 252 | 235 | 104 | 0 | **5%** |
| `opencis/cxl/component/gae_cci_mailbox.py` | 62 | 19 | 8 | 1 | **66%** |
| `opencis/cxl/component/gae_manager.py` | 106 | 6 | 16 | 2 | **92%** |
| `opencis/cxl/component/hdm_decoder.py` | 261 | 82 | 36 | 5 | **65%** |
| `opencis/cxl/component/host_llc_iogen.py` | 65 | 65 | 14 | 0 | **0%** |
| `opencis/cxl/component/host_manager.py` | 200 | 148 | 24 | 1 | **24%** |
| `opencis/cxl/component/irq_manager.py` | 12 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/mctp/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/mctp/fm_mctp_cci_server.py` | 77 | 9 | 4 | 0 | **89%** |
| `opencis/cxl/component/mctp/fm_smbus_dual_port_server.py` | 291 | 59 | 32 | 9 | **78%** |
| `opencis/cxl/component/mctp/fm_smbus_mctp_server.py` | 495 | 64 | 70 | 21 | **84%** |
| `opencis/cxl/component/mctp/mctp_cci_api_client.py` | 290 | 119 | 76 | 12 | **53%** |
| `opencis/cxl/component/mctp/mctp_cci_executor.py` | 171 | 85 | 56 | 4 | **43%** |
| `opencis/cxl/component/mctp/mctp_connection.py` | 7 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/mctp/mctp_connection_client.py` | 44 | 13 | 12 | 1 | **61%** |
| `opencis/cxl/component/mctp/mctp_connection_manager.py` | 54 | 10 | 4 | 1 | **78%** |
| `opencis/cxl/component/mctp/mctp_packet_processor.py` | 51 | 0 | 4 | 0 | **100%** |
| `opencis/cxl/component/mctp/mctp_packet_reader.py` | 63 | 11 | 12 | 6 | **77%** |
| `opencis/cxl/component/mctp/smbus_mctp_framing.py` | 125 | 5 | 18 | 5 | **93%** |
| `opencis/cxl/component/mld_client.py` | 201 | 201 | 44 | 0 | **0%** |
| `opencis/cxl/component/mld_manager.py` | 385 | 385 | 120 | 0 | **0%** |
| `opencis/cxl/component/packet_reader.py` | 163 | 77 | 78 | 15 | **44%** |
| `opencis/cxl/component/pbr_hdm_decoder.py` | 45 | 45 | 6 | 0 | **0%** |
| `opencis/cxl/component/pbr_switch_manager.py` | 177 | 15 | 52 | 11 | **89%** |
| `opencis/cxl/component/pbr_switch_router.py` | 109 | 20 | 38 | 11 | **78%** |
| `opencis/cxl/component/physical_port_manager.py` | 147 | 73 | 52 | 0 | **44%** |
| `opencis/cxl/component/root_complex/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/root_complex/cache_coherency_bridge.py` | 293 | 293 | 142 | 0 | **0%** |
| `opencis/cxl/component/root_complex/home_agent.py` | 281 | 281 | 100 | 0 | **0%** |
| `opencis/cxl/component/root_complex/io_bridge.py` | 112 | 112 | 20 | 0 | **0%** |
| `opencis/cxl/component/root_complex/memory_controller.py` | 41 | 41 | 6 | 0 | **0%** |
| `opencis/cxl/component/root_complex/root_complex.py` | 83 | 83 | 2 | 0 | **0%** |
| `opencis/cxl/component/root_complex/root_port_client_manager.py` | 41 | 41 | 4 | 0 | **0%** |
| `opencis/cxl/component/root_complex/root_port_switch.py` | 72 | 72 | 4 | 0 | **0%** |
| `opencis/cxl/component/short_msg_conn.py` | 148 | 91 | 34 | 3 | **33%** |
| `opencis/cxl/component/smbus/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/smbus/smbus_mctp_bridge.py` | 126 | 126 | 10 | 0 | **0%** |
| `opencis/cxl/component/switch_connection_client.py` | 105 | 30 | 24 | 8 | **64%** |
| `opencis/cxl/component/switch_connection_manager.py` | 136 | 31 | 22 | 8 | **72%** |
| `opencis/cxl/component/virtual_switch/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/virtual_switch/downstream_vppb.py` | 33 | 13 | 0 | 0 | **61%** |
| `opencis/cxl/component/virtual_switch/port_binder.py` | 91 | 44 | 28 | 0 | **41%** |
| `opencis/cxl/component/virtual_switch/routers.py` | 353 | 211 | 118 | 0 | **32%** |
| `opencis/cxl/component/virtual_switch/routing_table.py` | 25 | 10 | 6 | 0 | **48%** |
| `opencis/cxl/component/virtual_switch/upstream_vppb.py` | 30 | 4 | 0 | 0 | **87%** |
| `opencis/cxl/component/virtual_switch/virtual_switch.py` | 210 | 100 | 48 | 10 | **48%** |
| `opencis/cxl/component/virtual_switch/vppb.py` | 66 | 23 | 0 | 0 | **65%** |
| `opencis/cxl/component/virtual_switch/vppb_routing_info.py` | 6 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/component/virtual_switch_manager.py` | 64 | 23 | 18 | 0 | **60%** |
| `opencis/cxl/config_space/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/config_space/cfg.py` | 50 | 2 | 8 | 3 | **88%** |
| `opencis/cxl/config_space/device.py` | 25 | 8 | 2 | 0 | **63%** |
| `opencis/cxl/config_space/doe/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/config_space/doe/cdat.py` | 55 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/config_space/doe/doe.py` | 17 | 0 | 2 | 1 | **95%** |
| `opencis/cxl/config_space/doe/doe_table_access.py` | 79 | 39 | 12 | 1 | **45%** |
| `opencis/cxl/config_space/dvsec/__init__.py` | 113 | 22 | 22 | 9 | **74%** |
| `opencis/cxl/config_space/dvsec/common.py` | 12 | 0 | 2 | 1 | **93%** |
| `opencis/cxl/config_space/dvsec/cxl_devices.py` | 82 | 28 | 8 | 0 | **60%** |
| `opencis/cxl/config_space/dvsec/cxl_extension_dvsec_for_ports.py` | 23 | 1 | 2 | 1 | **92%** |
| `opencis/cxl/config_space/dvsec/flex_bus_port.py` | 31 | 0 | 2 | 1 | **97%** |
| `opencis/cxl/config_space/dvsec/mld_dvsec.py` | 17 | 6 | 2 | 0 | **58%** |
| `opencis/cxl/config_space/dvsec/register_locator.py` | 61 | 4 | 14 | 6 | **87%** |
| `opencis/cxl/config_space/port.py` | 30 | 1 | 2 | 1 | **94%** |
| `opencis/cxl/config_space/serial_number/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/config_space/serial_number/common.py` | 42 | 2 | 6 | 3 | **90%** |
| `opencis/cxl/device/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/device/config/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/device/config/dynamic_capacity_device.py` | 45 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/device/config/logical_device.py` | 31 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/device/cxl_gfd_device.py` | 70 | 9 | 6 | 1 | **84%** |
| `opencis/cxl/device/cxl_type1_device.py` | 110 | 110 | 10 | 0 | **0%** |
| `opencis/cxl/device/cxl_type2_device.py` | 102 | 102 | 8 | 0 | **0%** |
| `opencis/cxl/device/cxl_type3_device.py` | 75 | 40 | 0 | 0 | **47%** |
| `opencis/cxl/device/downstream_port_device.py` | 149 | 65 | 8 | 1 | **54%** |
| `opencis/cxl/device/pci_to_pci_bridge_device.py` | 183 | 97 | 34 | 0 | **40%** |
| `opencis/cxl/device/port_device.py` | 71 | 7 | 0 | 0 | **90%** |
| `opencis/cxl/device/root_port_device.py` | 694 | 524 | 168 | 4 | **20%** |
| `opencis/cxl/device/upstream_port_device.py` | 54 | 7 | 0 | 0 | **87%** |
| `opencis/cxl/environment/__init__.py` | 1 | 1 | 0 | 0 | **0%** |
| `opencis/cxl/environment/environment.py` | 145 | 145 | 42 | 0 | **0%** |
| `opencis/cxl/features/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/features/event_manager.py` | 24 | 6 | 2 | 0 | **69%** |
| `opencis/cxl/features/log_manager.py` | 64 | 30 | 10 | 0 | **46%** |
| `opencis/cxl/features/mailbox.py` | 158 | 63 | 28 | 0 | **51%** |
| `opencis/cxl/mmio/__init__.py` | 47 | 15 | 8 | 2 | **62%** |
| `opencis/cxl/mmio/component_register/__init__.py` | 27 | 2 | 8 | 4 | **83%** |
| `opencis/cxl/mmio/component_register/memcache_register/__init__.py` | 63 | 5 | 16 | 5 | **87%** |
| `opencis/cxl/mmio/component_register/memcache_register/capability.py` | 88 | 3 | 12 | 5 | **92%** |
| `opencis/cxl/mmio/component_register/memcache_register/hdm_decoder_capability.py` | 188 | 43 | 28 | 7 | **71%** |
| `opencis/cxl/mmio/component_register/memcache_register/link_capability.py` | 23 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/mmio/component_register/memcache_register/ras_capability.py` | 17 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/mmio/device_register/__init__.py` | 104 | 81 | 26 | 0 | **18%** |
| `opencis/cxl/mmio/device_register/device_capabilities.py` | 85 | 46 | 16 | 0 | **39%** |
| `opencis/cxl/mmio/device_register/device_status_register.py` | 32 | 12 | 4 | 0 | **56%** |
| `opencis/cxl/mmio/device_register/mailbox_register.py` | 141 | 78 | 38 | 0 | **35%** |
| `opencis/cxl/mmio/device_register/memory_device_capabilities.py` | 31 | 12 | 4 | 0 | **54%** |
| `opencis/cxl/mmio/gfd_mmio_registers.py` | 26 | 26 | 6 | 0 | **0%** |
| `opencis/cxl/transport/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/transport/cache_fifo.py` | 38 | 38 | 0 | 0 | **0%** |
| `opencis/cxl/transport/cci_packets.py` | 390 | 269 | 70 | 3 | **27%** |
| `opencis/cxl/transport/common.py` | 15 | 1 | 2 | 1 | **88%** |
| `opencis/cxl/transport/cxl_cache_packets.py` | 71 | 27 | 4 | 0 | **59%** |
| `opencis/cxl/transport/cxl_io_packets.py` | 124 | 71 | 24 | 0 | **36%** |
| `opencis/cxl/transport/cxl_mem_packets.py` | 79 | 27 | 0 | 0 | **66%** |
| `opencis/cxl/transport/fields.py` | 24 | 24 | 0 | 0 | **0%** |
| `opencis/cxl/transport/generate_packet_structs.py` | 258 | 258 | 64 | 0 | **0%** |
| `opencis/cxl/transport/generate_py_fallback.py` | 212 | 212 | 36 | 0 | **0%** |
| `opencis/cxl/transport/mem_benchmark.py` | 31 | 31 | 6 | 0 | **0%** |
| `opencis/cxl/transport/mem_benchmark_legacy.py` | 19 | 19 | 4 | 0 | **0%** |
| `opencis/cxl/transport/memory_fifo.py` | 25 | 25 | 0 | 0 | **0%** |
| `opencis/cxl/transport/mixin.py` | 176 | 91 | 24 | 1 | **43%** |
| `opencis/cxl/transport/packet_constants.py` | 180 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/transport/packet_structs.py` | 4021 | 2097 | 452 | 28 | **44%** |
| `opencis/cxl/transport/packets.py` | 1 | 1 | 0 | 0 | **0%** |
| `opencis/cxl/transport/pbr_packets.py` | 14 | 0 | 0 | 0 | **100%** |
| `opencis/cxl/transport/setup.py` | 7 | 7 | 0 | 0 | **0%** |
| `opencis/cxl/transport/sideband_packets.py` | 20 | 0 | 0 | 0 | **100%** |
| `opencis/drivers/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/drivers/cxl_bus_driver.py` | 420 | 420 | 116 | 0 | **0%** |
| `opencis/drivers/cxl_mem_driver.py` | 62 | 62 | 18 | 0 | **0%** |
| `opencis/drivers/pci_bus_driver.py` | 361 | 361 | 96 | 0 | **0%** |
| `opencis/msim/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/msim/emulator.py` | 80 | 80 | 14 | 0 | **0%** |
| `opencis/pci/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/pci/component/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/pci/component/config_space_manager.py` | 139 | 90 | 36 | 2 | **29%** |
| `opencis/pci/component/doe_mailbox.py` | 161 | 89 | 24 | 1 | **41%** |
| `opencis/pci/component/fifo_pair.py` | 6 | 0 | 0 | 0 | **100%** |
| `opencis/pci/component/mmio_manager.py` | 160 | 98 | 52 | 1 | **32%** |
| `opencis/pci/component/packet_processor.py` | 42 | 17 | 10 | 2 | **52%** |
| `opencis/pci/component/pci.py` | 138 | 56 | 30 | 0 | **49%** |
| `opencis/pci/component/pci_connection.py` | 6 | 0 | 0 | 0 | **100%** |
| `opencis/pci/component/routing_table.py` | 94 | 48 | 28 | 0 | **41%** |
| `opencis/pci/config_space/__init__.py` | 61 | 8 | 4 | 2 | **85%** |
| `opencis/pci/config_space/pci.py` | 298 | 92 | 46 | 2 | **62%** |
| `opencis/pci/config_space/pcie/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/pci/config_space/pcie/doe.py` | 84 | 27 | 26 | 2 | **54%** |
| `opencis/pci/config_space/pcie/msi.py` | 41 | 3 | 4 | 2 | **89%** |
| `opencis/pci/config_space/pcie/pcie_capability.py` | 45 | 8 | 6 | 3 | **78%** |
| `opencis/pci/device/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/pci/device/pci_device.py` | 34 | 34 | 4 | 0 | **0%** |
| `opencis/util/__init__.py` | 0 | 0 | 0 | 0 | **100%** |
| `opencis/util/accessor.py` | 15 | 11 | 0 | 0 | **27%** |
| `opencis/util/async_gatherer.py` | 18 | 0 | 4 | 0 | **100%** |
| `opencis/util/bound_event.py` | 15 | 15 | 0 | 0 | **0%** |
| `opencis/util/component.py` | 119 | 22 | 26 | 7 | **80%** |
| `opencis/util/logger.py` | 73 | 28 | 18 | 5 | **55%** |
| `opencis/util/memory.py` | 20 | 20 | 4 | 0 | **0%** |
| `opencis/util/number.py` | 74 | 41 | 22 | 1 | **35%** |
| `opencis/util/number_const.py` | 4 | 0 | 0 | 0 | **100%** |
| `opencis/util/pci.py` | 20 | 14 | 4 | 0 | **25%** |
| `opencis/util/server.py` | 73 | 5 | 16 | 3 | **91%** |
| `opencis/util/unaligned_bit_structure.py` | 568 | 264 | 208 | 29 | **49%** |
| **TOTAL PROJECT** | **27442** | **16141** | **5188** | **375** | **37%** |

---

## 4. Requirements Traceability Matrix

The traceability matrix below connects the CXL 4.0 Specification requirements for PBR Switch, GFD Devices, GAE, and MCTP/SMBus transport to the corresponding implementation codebase and validating test cases.

### 4.1 PBR Switch and CCI Commissioning Requirements

| Req ID | Spec § | Requirement Description | Code Method | Test Case(s) |
| :--- | :--- | :--- | :--- | :--- |
| **REQ-PBR-01** | §7.7.13.1 | `IdentifyPbrSwitch` command must report capabilities, including `num_drts >= 1`. | `PbrSwitchManager.get_identify_info` | `test_gfd_live_fm_identify_pbr_switch`<br>`test_pbr_identify` |
| **REQ-PBR-02** | §7.7.13.1 | The `num_drts` in the Identify response must match the actual number of DRT tables. | `IdentifyPbrSwitchCommand._execute` | `test_gfd_live_fm_identify_pbr_switch` |
| **REQ-PBR-03** | §7.7.13.5 | `ConfigurePidAssignment` must assign, read, and clear Physical Port Identifiers (PIDs). | `PbrSwitchManager.assign_pid`<br>`PbrSwitchManager.clear_pid` | `test_gfd_live_fm_configure_pid_assignment`<br>`test_assign_pid_success`<br>`test_clear_pid_success`<br>`test_pbr_configure_pid_assignment` |
| **REQ-PBR-04** | §7.7.13.5 | Assigning a duplicate PID to a different target port must return `INVALID_INPUT`. | `PbrSwitchManager.assign_pid` | `test_assign_pid_duplicate_different_target_rejected` |
| **REQ-PBR-05** | §7.7.13.5 | Re-assigning an already assigned PID to the same target must exit idempotently with `SUCCESS`. | `PbrSwitchManager.assign_pid` | `test_assign_same_pid_same_target_idempotent` |
| **REQ-PBR-06** | §7.7.13.6 | `GetPidBinding` must support looking up current assignments for a VCS/vPPB slot. | `PbrSwitchManager.get_pid_binding` | `test_gfd_live_fm_get_pid_binding`<br>`test_gfd_live_full_fm_workflow` |
| **REQ-PBR-07** | §7.7.13.6 | `GetPidBinding` must return `0xFFF` (`PID_UNASSIGNED`) if the slot is unbound. | `PbrSwitchManager.get_pid_binding` | `test_pbr_get_pid_binding_unbound`<br>`test_get_unbound_returns_none` |
| **REQ-PBR-08** | §7.7.13.7 | `ConfigurePidBinding` is a background command that must support binding or unbinding slots. | `ConfigurePidBindingCommand` | `test_gfd_live_fm_configure_pid_binding`<br>`test_pbr_configure_pid_binding_background` |
| **REQ-PBR-09** | §7.7.13.9 | `SetDrt` must reject programming entries with entry type `RESERVED (0b11)`. | `PbrSwitchManager.set_drt` | `test_set_drt_reserved_entry_type_rejected` |
| **REQ-PBR-10** | §7.7.13.9 | `SetDrt` and `GetDrt` must enforce table size bounds and validate indices. | `PbrSwitchManager.set_drt`<br>`PbrSwitchManager.get_drt` | `test_set_drt_exceeds_table_size`<br>`test_get_drt_invalid_index` |
| **REQ-PBR-11** | §7.7.7 | The PBR Router must encapsulate standard HBR CXL TLPs into PBR flits on ingress ports. | `PbrSwitchRouter._route_packet` | `test_hbr_to_pbr_encapsulation_and_decapsulation` |
| **REQ-PBR-12** | §7.7.7 | The PBR Router must decapsulate PBR flits back into standard HBR TLPs on egress ports. | `PbrSwitchRouter._route_packet` | `test_pbr_data_plane_routing` |
| **REQ-PBR-13** | §7.7.7 | The HDM Decoder must map generic host memory range addresses to target DPIDs. | `PbrHdmDecoderManager.get_dpid` | `test_pbr_end_to_end_address_routing`<br>`test_pbr_qemu_e2e_usp_ingress` |

### 4.2 Generic Fabric Device (GFD) Requirements

| Req ID | Spec § | Requirement Description | Code Method | Test Case(s) |
| :--- | :--- | :--- | :--- | :--- |
| **REQ-GFD-01** | §7.7.13 | GFD must NOT expose any PCIe BAR configuration space (has NO host-visible registers). | `CxlGfdDevice` | `test_gfd_no_bar` |
| **REQ-GFD-02** | §7.7.13 | GFD must communicate exclusively via the `cci_fifo` mailbox interface. | `CxlGfdDevice` | `test_gfd_cci_mailbox_dispatch` |
| **REQ-GFD-03** | §7.7.13 | GFD must respond to CCI Identify command returning GFD component type `0x04`. | `IdentifyCommand` | `test_gfd_cci_identify` |
| **REQ-GFD-04** | §7.7.13 | GFD must gracefully handle unknown opcodes and return `UNSUPPORTED`. | `CxlGfdDevice` | `test_gfd_cci_unknown_opcode` |
| **REQ-GFD-05** | §7.7.13 | GFD must start and stop cleanly (lifecycle validation). | `GenericFabricDevice.run` | `test_gfd_starts_and_stops` |

### 4.3 Generic Access Endpoint (GAE) Requirements

| Req ID | Spec § | Requirement Description | Code Method | Test Case(s) |
| :--- | :--- | :--- | :--- | :--- |
| **REQ-GAE-01** | §7.7.14 | `IdentifyGae` (0x5800) must return the total number of vPPBs and capability bits. | `IdentifyGaeCommand._execute` | `test_identify_gae_command_execute_with_vppbs`<br>`test_identify_gae_opcode_is_0x5800`<br>`test_gfd_live_fm_identify_gae` |
| **REQ-GAE-02** | §7.7.14 | `GetPidAccessVectors` (0x5802) must return global memory and virtualization vectors. | `GetPidAccessVectorsCommand` | `test_get_pid_access_vectors_command_execute`<br>`test_gfd_live_fm_get_pid_access_vectors` |
| **REQ-GAE-03** | §7.7.14 | `ProxyGfdMgmt` (0x5809) must run asynchronously in the background and return a `thread_id`. | `GaeManager.start_proxy` | `test_gae_start_proxy_executor_returns_thread_id`<br>`test_proxy_cmd_returns_thread_id`<br>`test_gfd_live_fm_proxy_gfd_mgmt_flow` |
| **REQ-GAE-04** | §7.7.14 | `GetProxyThreadStatus` (0x580A) must retrieve proxy command execution results. | `GaeManager.get_proxy_status` | `test_get_proxy_thread_status_completed`<br>`test_get_proxy_thread_status_unknown_thread` |
| **REQ-GAE-05** | §7.7.14 | `CancelProxyThread` (0x580B) must cancel a running GAE proxy thread task. | `GaeManager.cancel_proxy` | `test_cancel_proxy_thread_success`<br>`test_cancel_proxy_thread_unknown_id`<br>`test_gfd_live_fm_cancel_proxy_thread` |
| **REQ-GAE-06** | §7.7.14 | The GAE must support tunneling commands downstream to physical ports via the DSP CCI tunnel. | `GaeManager.set_gfd_tunnel` | `test_proxy_via_dsp_cci_tunnel_end_to_end`<br>`test_fabric_crawl_out_command_full_path` |

### 4.4 MCTP & SMBus Transport Requirements

| Req ID | Description | Code Method / Interface | Test Case(s) |
| :--- | :--- | :--- | :--- |
| **REQ-MCTP-01** | MCTP packets must be correctly depacketized, forwarded, and repacketized over TCP Port 8300. | `FmMctpCciServer`<br>`MctpCciApiClient` | `test_mctp_fm_port.py`<br>`test_mctp_fm_port_integration.py` |
| **REQ-SMBUS-01** | MCTP/CCI commands must be transported and validated over SMBus dual port interfaces. | `FmSmbusDualPortServer` | `test_smbus_dual_port.py`<br>`test_smbus_dual_port_pbr_cmds.py` |
| **REQ-SMBUS-02** | MCTP command requests must bridge successfully from SMBus to MCTP target connections. | `SmbusMctpBridge` | `test_smbus_mctp_bridge.py`<br>`test_smbus_mctp_server_pbr_cmds.py` |
