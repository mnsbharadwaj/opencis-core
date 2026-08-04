import socket
import struct
import sys
from typing import NamedTuple, List

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

# Define 34 command payloads matching the exact struct formats in OpenCIS CCI request classes
TEST_CASES: List[CommandTestCase] = [
    # ── 1. Generic Commands ───────────────────────────────────────────────────
    CommandTestCase("1. Identify (Generic)", 0x0001, b""),
    CommandTestCase("2. Background Operation Status", 0x0002, b""),
    CommandTestCase("3. Keep Alive", 0x0003, b""),

    # ── 2. Physical Switch Commands ───────────────────────────────────────────
    CommandTestCase("4. Identify Switch Device", 0x5100, b""),
    CommandTestCase("5. Get Physical Port State", 0x5101, struct.pack("<B", 1) + bytes([0])), # 1 port, Port ID 0
    CommandTestCase("6. Physical Port Control", 0x5102, struct.pack("<BBB", 1, 1, 0)), # ppb_id=1, opcode=1 (Reset), rsvd=0
    CommandTestCase("7. Send PPB Config Request (Read)", 0x5103, struct.pack("<BHBBBBI", 1, 0x10, 0, 0xF, 0, 0, 0)), # Read register 0x10
    CommandTestCase("8. Get Domain Validation SV State", 0x5104, b""),
    CommandTestCase("9. Set Domain Validation SV", 0x5105, bytes([0]*16)), # 16-byte UUID
    CommandTestCase("10. Get VCS Domain Validation SV State", 0x5106, struct.pack("<H", 0)), # VCS ID 0
    CommandTestCase("11. Get Domain Validation SV", 0x5107, struct.pack("<H", 0)), # VCS ID 0

    # ── 3. Virtual Switch Commands ────────────────────────────────────────────
    CommandTestCase("12. Get Virtual CXL Switch Info", 0x5200, struct.pack("<BBH", 0, 0xFF, 0)), # Start VCS=0, Limit=255
    CommandTestCase("13. Bind vPPB", 0x5201, struct.pack("<BBBB", 0, 4, 2, 0)), # VCS=0, vPPB=4, Port=2, LD=0
    CommandTestCase("14. Unbind vPPB", 0x5202, struct.pack("<BBH", 0, 4, 0)), # VCS=0, vPPB=4
    CommandTestCase("15. Generate AER Event", 0x5203, struct.pack("<BBI", 0, 1, 0x80000005) + bytes([0]*32)),
    CommandTestCase("16. Freeze vPPB", 0x5215, struct.pack("<BBH", 0, 1, 0)), # VCS=0, vPPB=1
    CommandTestCase("17. Unfreeze vPPB", 0x5216, struct.pack("<BBH", 0, 1, 0)), # VCS=0, vPPB=1

    # ── 4. MLD Port Commands ──────────────────────────────────────────────────
    CommandTestCase("18. Send LD CXL.io Config (Read)", 0x5301, struct.pack("<BHBBBB", 1, 0x08, 0, 0xF, 0, 2)), # Read LD 2 register 0x08
    CommandTestCase("19. Send LD CXL.io Memory (Read)", 0x5302, struct.pack("<BBBBHQ", 1, 0xF, 0, 0, 8, 0x10000)), # Read 8 bytes from addr 0x10000 on LD 3 (port 1)

    # ── 5. MLD Component Commands ─────────────────────────────────────────────
    CommandTestCase("20. Get LD Info (Port 1)", 0x5400, b""), # Empty request payload
    CommandTestCase("21. Get LD Allocations", 0x5401, struct.pack("<BBBB", 1, 0, 4, 0)), # Port 1, start LD 0, limit 4
    CommandTestCase("22. Set LD Allocation", 0x5402, struct.pack("<BBBBH", 1, 4, 0, 0, 0x1000)), # Port 1, 4 LDs, start 0

    # ── 6. Multi-Headed Device Commands ───────────────────────────────────────
    CommandTestCase("23. Get Multi-Headed Info", 0x5500, struct.pack("<BH", 0, 16)), # Start LD 0, limit 16
    CommandTestCase("24. Get Head Info", 0x5501, struct.pack("<BH", 0, 2)), # Start head 0, count 2

    # ── 7. Dynamic Capacity Device (DCD) Commands ─────────────────────────────
    CommandTestCase("25. DCD Add Reference", 0x5606, bytes([0xAA]*16)), # 16-byte reference tag
    CommandTestCase("26. DCD Remove Reference", 0x5607, bytes([0xAA]*16)), # 16-byte reference tag
    CommandTestCase("27. DCD List Tags", 0x5608, struct.pack("<HH", 0, 10)), # Start index 0, limit 10

    # ── 8. Port-Based Routing (PBR) Commands ──────────────────────────────────
    CommandTestCase("28. Identify PBR Switch", 0x5700, b""),
    CommandTestCase("29. Configure PID Assignment", 0x5704, struct.pack("<BHBB", 0x010, 1, 0, 0)), # PID 0x010 target port 1
    CommandTestCase("30. Get PID Binding", 0x5705, struct.pack("<BB", 0, 1)), # VCS 0, vPPB 1
    CommandTestCase("31. Configure PID Binding", 0x5706, struct.pack("<BBBBH", 0, 0, 1, 0, 0x010)), # Bind PID 0x010 to VCS 0, vPPB 1
    CommandTestCase("32. Get DRT", 0x5708, struct.pack("<BBBB", 0, 0, 16, 0)), # DRT index 0, start 0, limit 16
    CommandTestCase("33. Set DRT", 0x5709, struct.pack("<BBBBH", 0, 0, 0, 0, 0x010) + bytes([1])), # DRT index 0, map PID 0x010 to port 1
    CommandTestCase("34. Fabric Crawl Out (Tunnel GFD)", 0x5701, struct.pack("<H", 1) + struct.pack("<H", 0x0001) + struct.pack("<H", 0)), # Tunnel Identify GFD (opcode 0001h)
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
    print("\n" + "="*70)
    print(f"{'Command Name':<50} | {'Execution Status':<15}")
    print("="*70)
    for name, status in results:
        color = "\033[92m" if "SUCCESS" in status else "\033[91m"
        print(f"{name:<50} | {color}{status:<15}\033[0m")
    print("="*70)

if __name__ == "__main__":
    run_tests()
