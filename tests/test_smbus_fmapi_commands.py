import socket
import struct
import sys
from typing import NamedTuple

# CRC-8 computation for SMBus Packet Error Code (PEC)
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

# Helper to pack standard SMBus MCTP frames
def make_smbus_packet(opcode: int, payload: bytes = b"", tag: int = 0) -> bytes:
    # 8-byte CCI Header
    cci_hdr = struct.pack("<BBBH3s", 0x00, tag, 0x00, opcode, struct.pack("<I", len(payload))[:3])
    cci_msg = cci_hdr + payload
    
    # 5-byte MCTP Base Header + Msg Type
    mctp_hdr = struct.pack("<BBBBB", 0x01, 0x08, 0xC3, 0xC0 | tag, 0x01)
    
    # 4-byte SMBus Header
    byte_count = len(mctp_hdr) + len(cci_msg) + 1
    smbus_hdr = struct.pack("<BBBB", 0x0F, 0x0F, byte_count, 0x01)
    
    raw_packet = smbus_hdr + mctp_hdr + cci_msg
    pec = crc8_smbus(raw_packet)
    return raw_packet + bytes([pec])

class CommandTestCase(NamedTuple):
    name: str
    opcode: int
    payload: bytes

# Commands to test (covering generic, physical port/switch, virtual switch, MLD, MHD, and DCD)
TEST_CASES = [
    CommandTestCase("Identify (Generic)", 0x0001, b""),
    CommandTestCase("Background Operation Status", 0x0002, b""),
    CommandTestCase("Identify Switch Device", 0x5100, b""),
    CommandTestCase("Get Domain Validation SV State", 0x5104, b""),
    CommandTestCase("Get VCS Domain Validation SV State (VCS 0)", 0x5106, struct.pack("<H", 0)),
    CommandTestCase("Get Virtual CXL Switch Info", 0x5200, struct.pack("<BBH", 0, 0xFF, 0)),
    CommandTestCase("Get LD Info (MLD Port 1)", 0x5400, b""),
    CommandTestCase("Get LD Allocations (Port 1)", 0x5401, struct.pack("<BBBB", 1, 0, 4, 0)),
    CommandTestCase("Get Multi-Headed Info", 0x5500, struct.pack("<BH", 0, 16)),
    CommandTestCase("Get Head Info", 0x5501, struct.pack("<BH", 0, 2)),
    CommandTestCase("Dynamic Capacity List Tags", 0x5608, struct.pack("<HH", 0, 10)),
]

def run_tests():
    host, port = "127.0.0.1", 8300
    print(f"Connecting to SMBus server at {host}:{port}...")
    
    results = []
    
    for i, tc in enumerate(TEST_CASES):
        print(f"\n[{i+1}/{len(TEST_CASES)}] Testing: {tc.name} (Opcode 0x{tc.opcode:04X})")
        req_frame = make_smbus_packet(tc.opcode, tc.payload, tag=i % 8)
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect((host, port))
                
                # Encapsulate with 4-byte OpenCIS bridge system header
                s.sendall(struct.pack("<HH", 0x01, len(req_frame)) + req_frame)
                
                # Receive Response
                sys_hdr = s.recv(4)
                if not sys_hdr or len(sys_hdr) < 4:
                    print("  -> Fail: Connection closed or header incomplete")
                    results.append((tc.name, "NO_RESPONSE"))
                    continue
                
                _, resp_len = struct.unpack("<HH", sys_hdr)
                resp_data = s.recv(resp_len)
                
                # Parse response
                # SMBus response header is 7 bytes, CCI header is 12 bytes
                if len(resp_data) < 20:
                    print(f"  -> Fail: Response packet too short ({len(resp_data)} bytes)")
                    results.append((tc.name, "BAD_RESPONSE_FORMAT"))
                    continue
                
                # Verify PEC checksum
                expected_pec = crc8_smbus(resp_data[:-1])
                pec_valid = (expected_pec == resp_data[-1])
                
                # Extract return code from CCI response header (offset 15-16 inside frame)
                # offset 7 (SMBus header + MCTP header) + 8 (offset to return code inside CCI)
                return_code = struct.unpack("<H", resp_data[15:17])[0]
                
                status_str = "SUCCESS" if return_code == 0x0000 else f"ERROR(0x{return_code:04X})"
                if not pec_valid:
                    status_str += " (BAD_PEC)"
                
                print(f"  -> Status: {status_str}, Resp Payload size: {len(resp_data)-20} bytes")
                results.append((tc.name, status_str))
                
        except Exception as e:
            print(f"  -> Fail: {e}")
            results.append((tc.name, f"EXCEPTION: {type(e).__name__}"))

    # Print Results Table
    print("\n" + "="*65)
    print(f"{'Command Name':<45} | {'Execution Status':<15}")
    print("="*65)
    for name, status in results:
        color = "\033[92m" if "SUCCESS" in status else "\033[91m"
        print(f"{name:<45} | {color}{status:<15}\033[0m")
    print("="*65)

if __name__ == "__main__":
    run_tests()
