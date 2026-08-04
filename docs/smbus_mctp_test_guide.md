# CXL SMBus MCTP Sideband Testing Guide

This document explains how to construct and send raw SMBus packets that encapsulate MCTP frames and CCI (Component Command Interface) payloads to test the Fabric Manager's SMBus server on port `8300`.

---

## 1. Frame Serialization Structure

Every CXL CCI transaction sent over SMBus follows the DMTF DSP0237 specification layout:

```
[ SMBus Header (4B) ] [ MCTP Header (4B) ] [ Msg Type (1B) ] [ CCI Message Header (8B / 12B) ] [ Payload (Variable) ] [ PEC (1B) ]
```

### Packet Fields:
1. **dest_slave_addr (1 Byte):** Host-to-FM target address = `0x0F` (Target `0x07` + Write bit `0`).
2. **command_code (1 Byte):** SMBus Command Code = `0x0F` (MCTP block transaction).
3. **byte_count (1 Byte):** Length of the remaining packet bytes excluding this header and PEC.
4. **src_slave_addr (1 Byte):** Source address byte = `0x01`.
5. **MCTP Base Header (4 Bytes):**
   - Byte 0: `hdr_ver` = `0x01`
   - Byte 1: `dest_eid` = `0x08` (Fabric Manager EID)
   - Byte 2: `src_eid` = `0xC3` (Host EID)
   - Byte 3: `flags` = `0xC8` (SOM=1, EOM=1, Packet Sequence=0, TO=1, Tag=0)
6. **MCTP Message Type (1 Byte):** `0x01` (CXL CCI).
7. **CCI Request Header (8 Bytes):**
   - Byte 0: `cci_category` = `0x00` (Request)
   - Byte 1: `cci_tag` = Message Tag (matches response)
   - Byte 2: `reserved` = `0x00`
   - Bytes 3-4: `cci_opcode` = 2-byte Little-Endian command opcode (e.g. `0x01 0x00` for Identify `0001h`)
   - Bytes 5-7: `cci_plen` = 3-byte Little-Endian payload length
8. **CCI Payload (Variable Length):** The command parameters.
9. **PEC (1 Byte):** CRC-8 checksum computed across all bytes from `dest_slave_addr` through the end of the CCI payload.

---

## 2. Core CCI Command Byte Configurations

The following are the raw byte payloads for executing core commands over the SMBus port:

### 2.1 Generic Commands

#### 1. Identify (0001h)
* **Request Payload Length:** 0 bytes
* **Raw Request Packet (22 bytes total):**
  `0F 0F 13 01 01 08 C3 C8 01 00 00 00 01 00 00 00 00 [PEC]`
* **Expected Response (32 bytes total):**
  - Status Code: `0x0000` (Success)
  - Payload Size: 18 bytes (`0x12`) containing the Vendor ID, Device ID, subsystem parameters, and capabilities.

#### 2. Background Operation Status (0002h)
* **Request Payload Length:** 0 bytes
* **Raw Request Packet (22 bytes total):**
  `0F 0F 13 01 01 08 C3 C8 01 00 00 00 02 00 00 00 00 [PEC]`

---

### 2.2 Physical Switch Commands (Opcodes 5100h - 5107h)

#### 1. Identify Switch Device (5100h)
* **Request Payload Length:** 0 bytes
* **Raw Request Packet (22 bytes total):**
  `0F 0F 13 01 01 08 C3 C8 01 00 00 00 00 51 00 00 00 [PEC]`

#### 2. Get Domain Validation SV State (5104h)
* **Request Payload Length:** 0 bytes
* **Raw Request Packet (22 bytes total):**
  `0F 0F 13 01 01 08 C3 C8 01 00 00 00 04 51 00 00 00 [PEC]`

#### 3. Get VCS Domain Validation SV State (5106h)
* **Request Payload (2 bytes):** VCS ID (2 bytes LE) = `0x00 0x00`
* **Raw Request Packet (24 bytes total):**
  `0F 0F 15 01 01 08 C3 C8 01 00 00 00 06 51 02 00 00 00 00 [PEC]`

---

### 2.3 Virtual Switch Commands (Opcodes 5200h - 5205h)

#### 1. Get Virtual CXL Switch Info (5200h)
* **Request Payload (4 bytes):** Start VCS ID (`0x00`), Limit (`0xFF`), Reserved (`0x00 0x00`)
* **Raw Request Packet (26 bytes total):**
  `0F 0F 17 01 01 08 C3 C8 01 00 00 00 00 52 04 00 00 00 FF 00 00 [PEC]`

#### 2. Bind vPPB (5201h)
* **Request Payload (4 bytes):** VCS ID (`0`), vPPB ID (`4`), Physical Port (`2`), LD ID (`0`)
* **Raw Request Packet (26 bytes total):**
  `0F 0F 17 01 01 08 C3 C8 01 00 00 00 01 52 04 00 00 00 04 02 00 [PEC]`

---

### 2.4 MLD Component Commands (Opcodes 5400h - 5402h)

#### 1. Get LD Info (5400h)
* **Request Payload Length:** 0 bytes
* **Execution Behavior:** The FM SMBus server automatically defaults this request to map to target Port Index `1` since the CXL specification specifies an empty request payload.
* **Raw Request Packet (22 bytes total):**
  `0F 0F 13 01 01 08 C3 C8 01 00 00 00 00 54 00 00 00 [PEC]`
* **Expected Response:** 11-byte spec-compliant GetLdInfo payload (containing memory capacity and supported logical device count for Port 1).

#### 2. Get LD Allocations (5401h)
* **Request Payload (4 bytes):** Port Index (`0x01`), Start LD ID (`0x00`), List Limit (`0x04`), Reserved (`0x00`)
* **Raw Request Packet (26 bytes total):**
  `0F 0F 17 01 01 08 C3 C8 01 00 00 00 01 54 04 00 00 01 00 04 00 [PEC]`

---

## 3. Python Execution Client

You can run this test script to connect to port `8300` and send the raw byte structures directly:

```python
import socket
import struct

def crc8_smbus(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

# Assemble packet
def make_smbus_packet(opcode: int, payload: bytes = b"") -> bytes:
    # 8-byte CCI Header
    cci_hdr = struct.pack("<BBBH3s", 0x00, 0x00, 0x00, opcode, struct.pack("<I", len(payload))[:3])
    cci_msg = cci_hdr + payload
    
    # 5-byte MCTP Base Header + Msg Type
    mctp_hdr = struct.pack("<BBBBB", 0x01, 0x08, 0xC3, 0xC8, 0x01)
    
    # 4-byte SMBus Header
    byte_count = len(mctp_hdr) + len(cci_msg) + 1
    smbus_hdr = struct.pack("<BBBB", 0x0F, 0x0F, byte_count, 0x01)
    
    raw_packet = smbus_hdr + mctp_hdr + cci_msg
    pec = crc8_smbus(raw_packet)
    return raw_packet + bytes([pec])

def run_test():
    # Identify Switch command payload (Opcode 5100h)
    req_packet = make_smbus_packet(0x5100)
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("127.0.0.1", 8300))
        # Encapsulate with 4-byte OpenCIS bridge system header
        s.sendall(struct.pack("<HH", 0x01, len(req_packet)) + req_packet)
        
        # Receive Response
        sys_hdr = s.recv(4)
        _, resp_len = struct.unpack("<HH", sys_hdr)
        resp_data = s.recv(resp_len)
        print("SMBus RX Raw Hex:", resp_data.hex().upper())

if __name__ == "__main__":
    run_test()
```
