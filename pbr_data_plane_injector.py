#!/usr/bin/env python3
import asyncio
import struct
from opencis.cxl.transport.sideband_packets import SidebandConnectionRequestPacket
from opencis.cxl.transport.cxl_io_packets import CxlIoMemWrPacket
from opencis.cxl.component.packet_reader import PacketReader

async def main():
    print("[Injector] Connecting to Switch Data Plane (Port 8000)...")
    reader, writer = await asyncio.open_connection("127.0.0.1", 8000)

    # 1. OpenCIS Sideband Handshake (Bind to Port 0 as Host)
    print("[Injector] Sending Sideband Connection Request for Port 0...")
    req = SidebandConnectionRequestPacket.create(port_index=0)
    writer.write(bytes(req))
    await writer.drain()

    # Wait for Accept
    pkt_reader = PacketReader(reader)
    resp = await pkt_reader.get_packet()
    if resp.is_sideband() and resp.is_connection_accept():
        print("[Injector] Connected to Port 0 successfully!")
    else:
        print("[Injector] Failed to connect.")
        return

    # 2. Create the HBR payload (Write DEADBEEF to offset 0)
    print("[Injector] Creating CXL.io Memory Write Packet (HBR)...")
    hbr_pkt = CxlIoMemWrPacket.create(
        addr=0x00000000, 
        length=16, 
        data=b"\xDE\xAD\xBE\xEF" * 4
    )

    # 3. Inject into the Data Plane
    print("[Injector] Injecting pure HBR Packet into the fabric...")
    print("[Injector] The Switch should intercept this, use HDM Decoder (Addr -> 0x100), encapsulate to PBR, and route to SLD!")
    writer.write(bytes(hbr_pkt))
    await writer.drain()
    
    print("[Injector] Packet sent! The Switch should route it to the SLD.")
    print("[Injector] Use the FM CLI [r] 'Mem-Read' option to verify the data arrived!")

if __name__ == "__main__":
    asyncio.run(main())
