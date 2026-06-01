"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.

FmSmbusMctpServer -- SMBus+MCTP CCI server for CxlFabricManager
==============================================================

Replaces the previous FmMctpCciServer (CciPayloadPacket format) with
a standard MCTP-over-SMBus (DMTF DSP0237) framed server.

Accepts connections from QEMU SMBus Slave devices.
Sends responses to QEMU SMBus Master devices.

Protocol per connection:
  QEMU SMBus Slave  ->  [SMBus hdr | MCTP hdr | msg_type | CCI msg | PEC]  ->  FM port
  FM extracts CCI opcode + payload
  FM calls MctpCciApiClient.send_raw_cci(opcode, payload)  ->  Switch
  Switch returns (return_code, response_bytes, is_background)
  FM wraps: [byte_count | FM addr | MCTP hdr | msg_type | CCI resp | PEC]
  FM sends wrapped response  ->  QEMU SMBus Master

Packet format reference: smbus_mctp_framing.py
CCI field layout reference: opencis/cxl/transport/fields.py
"""

import asyncio
import struct
import sys
from asyncio import create_task, gather
from typing import Optional, TYPE_CHECKING

from opencis.util.component import RunnableComponent
from opencis.util.logger import logger
from opencis.util.server import ServerComponent
from opencis.cxl.cci.common import CCI_RETURN_CODE, get_opcode_string
from opencis.cxl.component.mctp.smbus_mctp_framing import (
    SmbusMctpRequest,
    build_smbus_mctp_response,
    crc8_smbus,
    read_smbus_frame,
    SMBUS_MCTP_COMMAND_CODE,
)

if TYPE_CHECKING:
    from opencis.cxl.component.mctp.mctp_cci_api_client import MctpCciApiClient


def _p(*args, **kw):
    """Print to sys.__stdout__ to bypass pytest stdout capture."""
    out = getattr(sys, "__stdout__", None) or sys.stdout
    kw.setdefault("flush", True)
    print(*args, file=out, **kw)


class FmSmbusMctpServer(RunnableComponent):
    """
    Fabric Manager SMBus+MCTP CCI server.

    Listens on a TCP port (default 8300).
    Accepts connections from QEMU SMBus Slave.
    Sends CCI responses in SMBus+MCTP format to QEMU SMBus Master
    (on the same connection -- full-duplex).

    Frame format: DMTF DSP0237 (MCTP over SMBus/I2C).

    Zero switch-programming logic here -- all CCI processing is done
    by the switch via MctpCciApiClient.send_raw_cci().
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8301,
        mctp_client: Optional["MctpCciApiClient"] = None,
        fm_i2c_addr: int = 0x10,
        verify_pec: bool = False,
        label: Optional[str] = None,
    ):
        """
        Args:
            host:         bind address
            port:         TCP port (default 8300)
            mctp_client:  FM CLI path (MctpCciApiClient). When None, returns
                          UNSUPPORTED for all commands (offline/test mode).
            fm_i2c_addr:  7-bit I2C address of the FM (used in response frames)
            verify_pec:   if True, drop packets with bad CRC-8 PEC
        """
        super().__init__(label or "FmSmbusMctpServer")
        self._host         = host
        self._port         = port
        self._mctp_client  = mctp_client
        self._fm_i2c_addr  = fm_i2c_addr
        self._verify_pec   = verify_pec

        self._server = ServerComponent(
            handle_client=self._handle_client,
            host=self._host,
            port=self._port,
            stop_callback=self._on_server_stop,
            label="FmSmbusMctpServer",
        )

    # -- Public helpers -----------------------------------------------------

    def get_port(self) -> int:
        return self._server.get_port()

    def bind_mctp_client(self, client: "MctpCciApiClient") -> None:
        """
        Late-bind the FM CLI api_client after the switch connects on port 8100.
        Call this once MctpCciApiClient is running.
        """
        self._mctp_client = client
        logger.info(self._create_message(
            "MctpCciApiClient bound -- port 8300 (SMBus+MCTP) bridges to FM CLI"
        ))

    # -- Per-connection handler ---------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.info(self._create_message(f"SMBus client connected: {peer}"))

        try:
            await self._process_client(reader, writer)
        except asyncio.IncompleteReadError:
            logger.info(self._create_message(f"SMBus client disconnected: {peer}"))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(self._create_message(
                f"SMBus client {peer} error: {exc}"
            ))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(self._create_message(f"SMBus session closed: {peer}"))

    # -- Packet printer -----------------------------------------------------

    @staticmethod
    def _hex_dump(data: bytes, indent: str = "    ") -> str:
        """Format bytes as a hex dump with ASCII side-panel."""
        if not data:
            return f"{indent}(empty)"
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{indent}{i:04X}  {hex_part:<47}  |{asc_part}|")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # PBR CCI payload decoders                                             #
    # Called by _print_rx_packet (request) and _print_tx_packet (response) #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _decode_request_payload(opcode: int, payload: bytes) -> None:
        """
        Print a human-readable field-by-field breakdown of a CCI request
        payload for every known PBR opcode.  Called after the hex dump.
        """
        if not payload:
            return

        P = "\033[96m"   # cyan accent for field labels
        R = "\033[0m"
        D = "\033[2m"

        # --- CONFIGURE_PID_ASSIGNMENT (0x5704) ---
        if opcode == 0x5704:
            if len(payload) < 4:
                return
            ops     = payload[0] & 0x07
            op_name = {0: "ASSIGN", 1: "CLEAR_ALL", 2: "CLEAR_SPECIFIC"}.get(ops, f"UNKNOWN({ops})")
            num_tgt = struct.unpack_from("<H", payload, 2)[0]
            _p(f"{P}  [CCI-REQ] CONFIGURE_PID_ASSIGNMENT payload decoded:{R}")
            _p(f"    operation   : {ops}  ({op_name})")
            _p(f"    num_targets : {num_tgt}")
            for i in range(num_tgt):
                off = 4 + i * 5
                if off + 5 > len(payload):
                    break
                pid      = struct.unpack_from("<H", payload, off)[0] & 0x0FFF
                tgt_id   = struct.unpack_from("<H", payload, off + 2)[0]
                inst_id  = payload[off + 4]
                _p(f"    entry[{i}]    : pid=0x{pid:03X}  target_port={tgt_id}  instance={inst_id}")

        # --- GET_PID_BINDING (0x5705) ---
        elif opcode == 0x5705:
            if len(payload) < 2:
                return
            _p(f"{P}  [CCI-REQ] GET_PID_BINDING payload decoded:{R}")
            _p(f"    target_vcs  : {payload[0]}  (VCS to query)")
            _p(f"    target_vppb : {payload[1]}  (vPPB slot to query)")

        # --- CONFIGURE_PID_BINDING (0x5706) ---
        elif opcode == 0x5706:
            if len(payload) < 6:
                return
            ops     = payload[0] & 0x07
            op_name = {0: "BIND", 1: "UNBIND"}.get(ops, f"UNKNOWN({ops})")
            vcs     = payload[1]
            vppb    = payload[2]
            pid     = struct.unpack_from("<H", payload, 4)[0] & 0x0FFF
            _p(f"{P}  [CCI-REQ] CONFIGURE_PID_BINDING payload decoded:{R}")
            _p(f"    operation   : {ops}  ({op_name})")
            _p(f"    target_vcs  : {vcs}")
            _p(f"    target_vppb : {vppb}")
            _p(f"    pid         : 0x{pid:03X}  ({'UNASSIGNED' if pid == 0xFFF else f'PID 0x{pid:03X}'})")
            if len(payload) > 6:
                _p(f"{D}    hmat_data   : {len(payload)-6} bytes (HMAT/bandwidth info){R}")

        # --- GET_DRT (0x5708) ---
        elif opcode == 0x5708:
            if len(payload) < 6:
                return
            drt_idx    = payload[0]
            num_ent    = struct.unpack_from("<H", payload, 2)[0]
            start_ent  = struct.unpack_from("<H", payload, 4)[0]
            _p(f"{P}  [CCI-REQ] GET_DRT payload decoded:{R}")
            _p(f"    drt_index   : {drt_idx}  (which DRT table to read)")
            _p(f"    num_entries : {num_ent}  (how many DRT entries to read)")
            _p(f"    start_entry : 0x{start_ent:03X}  (starting DPID index)")

        # --- SET_DRT (0x5709) ---
        elif opcode == 0x5709:
            if len(payload) < 6:
                return
            drt_idx   = payload[0]
            num_ent   = struct.unpack_from("<H", payload, 2)[0]
            start_ent = struct.unpack_from("<H", payload, 4)[0]
            etype_map = {0: "INVALID", 1: "PHYSICAL_PORT", 2: "RGT_INDEX", 3: "RESERVED"}
            _p(f"{P}  [CCI-REQ] SET_DRT payload decoded:{R}")
            _p(f"    drt_index   : {drt_idx}")
            _p(f"    num_entries : {num_ent}")
            _p(f"    start_entry : 0x{start_ent:03X}  (starting DPID)")
            for i in range(num_ent):
                off = 6 + i * 2
                if off + 2 > len(payload):
                    break
                etype  = payload[off] & 0x03
                target = payload[off + 1]
                _p(f"    entry[{i}]    : type={etype_map.get(etype,'?')}({etype})  routing_target={target}")

    @staticmethod
    def _decode_response_payload(opcode: int, payload: bytes) -> None:
        """
        Print a human-readable field-by-field breakdown of a CCI response
        payload for every known PBR opcode.  Called after the hex dump.
        """
        if not payload:
            return

        P = "\033[92m"   # green accent for field labels (matches TX header)
        R = "\033[0m"
        D = "\033[2m"

        # --- IDENTIFY_PBR_SWITCH (0x5700) ---
        if opcode == 0x5700:
            if len(payload) < 10:
                return
            gae_map  = int.from_bytes(payload[0:8], "little")
            num_drts = payload[8]
            num_rgts = payload[9]
            _p(f"{P}  [CCI-RESP] IDENTIFY_PBR_SWITCH response decoded:{R}")
            _p(f"    gae_support_map : 0x{gae_map:016X}")
            _p(f"{D}      64-bit bitmask -- bit N=1 means VCS N has a GAE (Generic Access Endpoint){R}")
            _p(f"    num_drts        : {num_drts}  (number of DRT tables the switch supports)")
            _p(f"    num_rgts        : {num_rgts}  (number of RGT tables)")

        # --- GET_PID_BINDING (0x5705) ---
        elif opcode == 0x5705:
            if len(payload) < 2:
                return
            pid   = struct.unpack_from("<H", payload, 0)[0] & 0x0FFF
            if pid == 0xFFF:
                bound_str = "UNBOUND  (0xFFF = no PID assigned to this vPPB)"
            else:
                bound_str = f"BOUND -> PID 0x{pid:03X}"
            _p(f"{P}  [CCI-RESP] GET_PID_BINDING response decoded:{R}")
            _p(f"    bound_pid       : 0x{pid:03X}  ({bound_str})")

        # --- GET_DRT (0x5708) ---
        elif opcode == 0x5708:
            if len(payload) < 8:
                return
            num_ent   = struct.unpack_from("<H", payload, 2)[0]
            start_ent = struct.unpack_from("<H", payload, 4)[0]
            etype_map = {0: "INVALID", 1: "PHYSICAL_PORT", 2: "RGT_INDEX", 3: "RESERVED"}
            _p(f"{P}  [CCI-RESP] GET_DRT response decoded:{R}")
            _p(f"    num_entries     : {num_ent}")
            _p(f"    start_entry     : 0x{start_ent:03X}  (DPID base index)")
            for i in range(num_ent):
                off = 8 + i * 2
                if off + 2 > len(payload):
                    break
                etype  = payload[off] & 0x03
                target = payload[off + 1]
                _p(f"    entry[{i}]        : DPID=0x{start_ent + i:03X}  type={etype_map.get(etype,'?')}  routing_target={target}")

    def _print_rx_packet(self, raw_frame: bytes, req: "SmbusMctpRequest | None",
                         parse_error: str = "") -> None:
        """
        Print full per-byte annotated breakdown of every RX (request) frame.
        Cyan header.  Each byte shown as [Bxx] hex binary + field meaning + spec ref.
        """
        sep = "=" * 60
        _p(f"\n\033[1m\033[96m{sep}")
        _p(f"  SMBus+MCTP RX  [{len(raw_frame)} bytes]  port {self._port}")
        _p(f"{sep}\033[0m")

        _p("\033[2m  Raw bytes:\033[0m")
        _p(self._hex_dump(raw_frame))

        if parse_error:
            _p(f"\033[91m  Parse ERROR: {parse_error}\033[0m")
            _p(f"\033[1m{'-' * 60}\033[0m\n")
            return

        H = "\033[93m"   # yellow -- section headings
        D = "\033[2m"    # dim    -- detail / annotation lines
        R = "\033[0m"    # reset

        # ================================================================
        # SMBus Header  (DMTF DSP0237 ss.4.1)
        # Frame layout:  B00=dest_addr  B01=cmd_code  B02=byte_count
        #                B03=src_addr   B04..=MCTP
        # ================================================================
        _p(f"{H}  -- SMBus Header (DSP0237 ss.4.1) --------------------{R}")

        b = raw_frame[0]
        i2c = b >> 1
        rw  = b & 1
        _p(f"  [B00] dest_slave_addr : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bits[7:1] = 0x{i2c:02X}   7-bit I2C address of FM (destination){R}")
        _p(f"{D}         bit[0]    = {rw}      R/W direction -- {'0=WRITE: master sends request to FM slave' if rw == 0 else '1=READ'}{R}")

        b = raw_frame[1]
        desc = "MCTP over SMBus command code (fixed=0x0F per DSP0237 ss.4.1.1)" if b == 0x0F else f"UNKNOWN -- expected 0x0F"
        _p(f"  [B01] command_code    : 0x{b:02X}")
        _p(f"{D}         = {desc}{R}")

        b = raw_frame[2]
        overhead = 18   # src_addr(1)+hdr_ver(1)+dest_eid(1)+src_eid(1)+flags(1)+msg_type(1)+CCI_hdr(12)
        plen_calc = b - overhead
        _p(f"  [B02] byte_count      : {b:3d}  (0x{b:02X})")
        _p(f"{D}         = total bytes from B03 to last payload byte (excluding PEC){R}")
        _p(f"{D}           breakdown: src_addr(1)+MCTP_hdr(4)+msg_type(1)+CCI_hdr(12)+CCI_payload({max(plen_calc, 0)}){R}")

        b = raw_frame[3]
        i2c_src = b >> 1
        sf      = b & 1
        _p(f"  [B03] src_slave_addr  : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bits[7:1] = 0x{i2c_src:02X}   7-bit I2C address of sender (device / QEMU){R}")
        _p(f"{D}         bit[0]    = {sf}      source address present flag (per DSP0237 ss.4.1){R}")

        # ================================================================
        # MCTP Transport Header  (DMTF DSP0236 ss.8.1)
        # B04=hdr_ver  B05=dest_eid  B06=src_eid  B07=flags
        # ================================================================
        _p(f"{H}  -- MCTP Transport Header (DSP0236 ss.8.1) -----------{R}")

        b = raw_frame[4]
        ver = b & 0x0F
        _p(f"  [B04] hdr_ver         : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bits[7:4] = {b >> 4}      reserved (must be 0){R}")
        _p(f"{D}         bits[3:0] = {ver}      MCTP version = {ver}  (only version 1 is defined){R}")

        b = raw_frame[5]
        _p(f"  [B05] dest_eid        : 0x{b:02X}  ({b})")
        _p(f"{D}         = destination Endpoint ID -- the FM EID that receives this request{R}")
        _p(f"{D}           EID 0x00 and 0xFF are reserved; assigned by MCTP control protocol{R}")

        b = raw_frame[6]
        _p(f"  [B06] src_eid         : 0x{b:02X}  ({b})")
        _p(f"{D}         = source Endpoint ID -- the device / QEMU EID that sent this request{R}")

        b = raw_frame[7]
        som = (b >> 7) & 1
        eom = (b >> 6) & 1
        seq = (b >> 4) & 3
        to_ = (b >> 3) & 1
        tag = b & 0x7
        _p(f"  [B07] flags           : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bit[7]  SOM    : {som}    Start Of Message -- {'IS start (first or only packet)' if som else 'NOT start (continuation fragment)'}{R}")
        _p(f"{D}         bit[6]  EOM    : {eom}    End Of Message   -- {'IS end (last or only packet -- single-fragment message)' if eom else 'NOT end (more fragments follow)'}{R}")
        _p(f"{D}         bit[5:4] seq   : {seq:02b}   Packet Sequence Number = {seq}  (increments per-fragment, 2-bit wraps at 4){R}")
        _p(f"{D}         bit[3]  TO     : {to_}    Tag Owner bit    -- {'1 = requester (device/QEMU) owns this tag' if to_ else '0 = responder (FM) owns this tag'}{R}")
        _p(f"{D}         bit[2:0] tag   : {tag:03b}  Message Tag = {tag}  (FM must echo same tag in response){R}")

        # ================================================================
        # MCTP Message Type byte  (DSP0236 ss.8.4)
        # B08 = IC(1 bit) | msg_type(7 bits)
        # ================================================================
        _p(f"{H}  -- MCTP Message Type Byte (DSP0236 ss.8.4) ----------{R}")

        b = raw_frame[8]
        ic    = (b >> 7) & 1
        mtype = b & 0x7F
        mtype_name = {
            0x00: "MCTP_CONTROL",
            0x05: "NCSI",
            0x06: "ETHERNET",
            0x07: "NVME_MI",
            0x7E: "CXL_FM_API",
            0x7F: "VENDOR_DEFINED",
        }.get(mtype, f"UNKNOWN(0x{mtype:02X})")
        _p(f"  [B08] msg_type_byte   : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bit[7]  IC     : {ic}    Integrity Check bit -- {'enabled: MCTP-level checksum appended after payload' if ic else 'disabled: no MCTP-level integrity check'}{R}")
        _p(f"{D}         bit[6:0] type  : 0x{mtype:02X}  {mtype_name}  (CXL 4.0 Table 7-1){R}")

        # ================================================================
        # CCI Message Header  (CXL 4.0 ss.7.7.1)  B09..B20  (12 bytes)
        # ================================================================
        _p(f"{H}  -- CCI Message Header (CXL 4.0 ss.7.7.1) [B09..B20]{R}")
        cs = 9   # CCI header start offset for RX frame

        b = raw_frame[cs]         # B09
        cat = b & 0x0F
        cat_name = {0: "REQUEST", 1: "RESPONSE"}.get(cat, f"UNKNOWN({cat})")
        _p(f"  [B09] message_category: 0x{b:02X}")
        _p(f"{D}         bits[7:4] = {b >> 4}     reserved (must be 0){R}")
        _p(f"{D}         bits[3:0] = {cat}     {cat_name}  (0=REQUEST from device, 1=RESPONSE from FM){R}")

        b = raw_frame[cs + 1]     # B10
        _p(f"  [B10] message_tag     : 0x{b:02X}  = {b}")
        _p(f"{D}         tag assigned by the requester (device); FM echoes this value unchanged in response{R}")
        _p(f"{D}         allows device to match async responses to its outstanding requests{R}")

        b = raw_frame[cs + 2]     # B11
        _p(f"  [B11] reserved        : 0x{b:02X}  (must be 0 per CXL 4.0 ss.7.7.1)")

        b12 = raw_frame[cs + 3]
        b13 = raw_frame[cs + 4]
        opcode = int.from_bytes(raw_frame[cs+3:cs+5], "little")
        opcode_str = get_opcode_string(opcode)
        _p(f"  [B12] opcode[7:0]     : 0x{b12:02X}  \\")
        _p(f"  [B13] opcode[15:8]    : 0x{b13:02X}   > opcode = \033[1m0x{opcode:04X}\033[0m  {opcode_str}  (little-endian 16-bit)")
        _p(f"{D}         CCI opcode selects which command the switch executes{R}")

        b14 = raw_frame[cs + 5]
        b15 = raw_frame[cs + 6]
        b16 = raw_frame[cs + 7]
        plen_lo = int.from_bytes(raw_frame[cs+5:cs+7], "little")
        plen_hi = b16 & 0x1F
        plen    = plen_lo | (plen_hi << 16)
        bg_op   = (b16 >> 7) & 1
        bg_desc = "YES -- background command (switch will return BACKGROUND_COMMAND_STARTED)" if bg_op else "no -- foreground command (switch responds inline)"
        _p(f"  [B14] payload_len[7:0] : 0x{b14:02X}  \\")
        _p(f"  [B15] payload_len[15:8]: 0x{b15:02X}   > payload_length[15:0] = {plen_lo}  (little-endian)")
        _p(f"  [B16] {{bg_op|rsvd|len[19:16]}}: 0x{b16:02X}  = 0b{b16 >> 4:04b}_{b16 & 0x0F:04b}")
        _p(f"{D}         bit[7]   bg_op : {bg_op}    Background Operation = {bg_desc}{R}")
        _p(f"{D}         bit[6:5] rsvd  : {(b16 >> 5) & 3:02b}   reserved{R}")
        _p(f"{D}         bit[4:0] extra : {plen_hi:05b} payload_length[19:16] = {plen_hi}  => total payload = {plen} bytes{R}")

        b17 = raw_frame[cs + 8]
        b18 = raw_frame[cs + 9]
        rc  = int.from_bytes(raw_frame[cs+8:cs+10], "little")
        try:
            rc_name = CCI_RETURN_CODE(rc).name
        except ValueError:
            rc_name = "UNKNOWN"
        rc_color = "\033[92m" if rc == 0 else "\033[93m" if rc == 1 else "\033[91m"
        _p(f"  [B17] return_code[7:0] : 0x{b17:02X}  \\")
        _p(f"  [B18] return_code[15:8]: 0x{b18:02X}   > return_code = {rc_color}{rc_name}\033[0m  (0x{rc:04X})  [always 0 in REQUEST]")

        b19 = raw_frame[cs + 10]
        b20 = raw_frame[cs + 11]
        vs  = int.from_bytes(raw_frame[cs+10:cs+12], "little")
        _p(f"  [B19] vendor_spec[7:0] : 0x{b19:02X}  \\")
        _p(f"  [B20] vendor_spec[15:8]: 0x{b20:02X}   > vendor_specific_status = 0x{vs:04X}  [always 0 in REQUEST]")

        # -- CCI Payload [B21..] -------------------------------------------
        payload = req.cci_payload
        if payload:
            pay_start = cs + 12
            pay_end   = pay_start + len(payload) - 1
            _p(f"{H}  -- CCI Payload [B{pay_start:02d}..B{pay_end:02d}]  ({len(payload)} bytes) ----------------{R}")
            _p(self._hex_dump(payload))
            self._decode_request_payload(opcode, payload)

        # -- PEC -----------------------------------------------------------
        pec     = raw_frame[-1]
        pec_exp = crc8_smbus(raw_frame[:-1])
        pec_ok  = pec == pec_exp
        pec_str = "\033[92mOK\033[0m" if pec_ok else f"\033[91mBAD -- computed 0x{pec_exp:02X}\033[0m"
        lb      = len(raw_frame) - 1
        _p(f"{H}  -- PEC / Packet Error Code (DSP0237 ss.4.1.2) ------{R}")
        _p(f"  [B{lb:02d}] PEC (CRC-8)   : 0x{pec:02X}  [{pec_str}]")
        _p(f"{D}         CRC-8 computed over B00..B{lb - 1} using SMBus polynomial 0x07{R}")
        _p(f"{D}         validates that the entire frame (including SMBus headers) is uncorrupted{R}")

        _p(f"\033[1m{'-' * 60}\033[0m\n")

    def _print_tx_packet(
        self,
        resp_frame: bytes,
        req: "SmbusMctpRequest",
    ) -> None:
        """
        Print full per-byte annotated breakdown of every TX (response) frame.
        Green header.  TX layout differs from RX: starts with byte_count (no dest_addr/cmd_code).
        Each byte shown as [Bxx] hex binary + field meaning + spec ref.
        """
        if len(resp_frame) < 20:
            _p(f"\033[91m  [TX] Response frame too short ({len(resp_frame)} bytes)\033[0m")
            return

        sep = "=" * 60
        _p(f"\n\033[1m\033[92m{sep}")
        _p(f"  SMBus+MCTP TX  [{len(resp_frame)} bytes]  port {self._port}")
        _p(f"{sep}\033[0m")

        _p("\033[2m  Raw bytes:\033[0m")
        _p(self._hex_dump(resp_frame))

        H = "\033[92m"   # green -- section headings (TX)
        D = "\033[2m"    # dim   -- detail / annotation
        R = "\033[0m"    # reset

        # ================================================================
        # SMBus Response Header  (DMTF DSP0237 ss.4.2)
        # TX layout (response): B00=byte_count  B01=fm_src_addr
        # (no dest_addr or command_code -- those are in the request frame)
        # ================================================================
        _p(f"{H}  -- SMBus Response Header (DSP0237 ss.4.2) -----------{R}")

        b = resp_frame[0]          # B00
        _p(f"  [B00] byte_count      : {b:3d}  (0x{b:02X})")
        _p(f"{D}         = total bytes from B01 to last payload byte (excluding PEC){R}")
        _p(f"{D}           breakdown: fm_src_addr(1)+hdr_ver(1)+dest_eid(1)+src_eid(1)+flags(1)+msg_type(1)+CCI_hdr(12)+CCI_payload({max(b - 18, 0)}){R}")

        b = resp_frame[1]          # B01
        i2c = b >> 1
        rw  = b & 1
        _p(f"  [B01] fm_src_addr     : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bits[7:1] = 0x{i2c:02X}   7-bit I2C address of FM (source of this response){R}")
        _p(f"{D}         bit[0]    = {rw}      {'1=READ direction (FM is source/slave returning data)' if rw else '0=WRITE'}{R}")

        # ================================================================
        # MCTP Transport Header  (DMTF DSP0236 ss.8.1)  B02..B05
        # ================================================================
        _p(f"{H}  -- MCTP Transport Header (DSP0236 ss.8.1) -----------{R}")

        b = resp_frame[2]          # B02
        ver = b & 0x0F
        _p(f"  [B02] hdr_ver         : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bits[7:4] = {b >> 4}      reserved (must be 0){R}")
        _p(f"{D}         bits[3:0] = {ver}      MCTP version = {ver}  (must equal request hdr_ver){R}")

        b = resp_frame[3]          # B03
        _p(f"  [B03] dest_eid        : 0x{b:02X}  ({b})")
        _p(f"{D}         = destination Endpoint ID -- device/QEMU EID (echoed from request src_eid){R}")

        b = resp_frame[4]          # B04
        _p(f"  [B04] src_eid         : 0x{b:02X}  ({b})")
        _p(f"{D}         = source Endpoint ID -- FM EID that is sending this response{R}")

        b = resp_frame[5]          # B05
        som = (b >> 7) & 1
        eom = (b >> 6) & 1
        seq = (b >> 4) & 3
        to_ = (b >> 3) & 1
        tag = b & 0x7
        _p(f"  [B05] flags           : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bit[7]  SOM    : {som}    Start Of Message -- {'IS start (first or only packet)' if som else 'NOT start'}{R}")
        _p(f"{D}         bit[6]  EOM    : {eom}    End Of Message   -- {'IS end (single-fragment response)' if eom else 'NOT end'}{R}")
        _p(f"{D}         bit[5:4] seq   : {seq:02b}   Packet Sequence Number = {seq}  (must match request seq){R}")
        _p(f"{D}         bit[3]  TO     : {to_}    Tag Owner bit    -- {'0 = responder (FM) owns tag in response' if to_ == 0 else '1 = requester -- unexpected in response'}{R}")
        _p(f"{D}         bit[2:0] tag   : {tag:03b}  Message Tag = {tag}  (MUST match request msg_tag to pair req/resp){R}")

        # ================================================================
        # MCTP Message Type byte  (DSP0236 ss.8.4)  B06
        # ================================================================
        _p(f"{H}  -- MCTP Message Type Byte (DSP0236 ss.8.4) ----------{R}")

        b = resp_frame[6]          # B06
        ic    = (b >> 7) & 1
        mtype = b & 0x7F
        mtype_name = {
            0x00: "MCTP_CONTROL",
            0x05: "NCSI",
            0x06: "ETHERNET",
            0x07: "NVME_MI",
            0x7E: "CXL_FM_API",
            0x7F: "VENDOR_DEFINED",
        }.get(mtype, f"UNKNOWN(0x{mtype:02X})")
        _p(f"  [B06] msg_type_byte   : 0x{b:02X}  = 0b{b >> 4:04b}_{b & 0x0F:04b}")
        _p(f"{D}         bit[7]  IC     : {ic}    Integrity Check bit -- {'enabled' if ic else 'disabled -- no MCTP-level checksum'}{R}")
        _p(f"{D}         bit[6:0] type  : 0x{mtype:02X}  {mtype_name}  (CXL 4.0 Table 7-1){R}")

        # ================================================================
        # CCI Message Header  (CXL 4.0 ss.7.7.1)  B07..B18  (12 bytes)
        # ================================================================
        _p(f"{H}  -- CCI Message Header (CXL 4.0 ss.7.7.1) [B07..B18]{R}")
        cs = 7   # CCI header start offset for TX frame

        b = resp_frame[cs]         # B07
        cat = b & 0x0F
        cat_name = {0: "REQUEST", 1: "RESPONSE"}.get(cat, f"UNKNOWN({cat})")
        _p(f"  [B07] message_category: 0x{b:02X}")
        _p(f"{D}         bits[7:4] = {b >> 4}     reserved (must be 0){R}")
        _p(f"{D}         bits[3:0] = {cat}     {cat_name}  (0=REQUEST, 1=RESPONSE from FM){R}")

        b = resp_frame[cs + 1]     # B08
        _p(f"  [B08] message_tag     : 0x{b:02X}  = {b}")
        _p(f"{D}         FM echoes the exact tag value from the request -- device uses it to match response{R}")

        b = resp_frame[cs + 2]     # B09
        _p(f"  [B09] reserved        : 0x{b:02X}  (must be 0 per CXL 4.0 ss.7.7.1)")

        b10 = resp_frame[cs + 3]
        b11 = resp_frame[cs + 4]
        opcode = int.from_bytes(resp_frame[cs+3:cs+5], "little")
        opcode_str = get_opcode_string(opcode)
        _p(f"  [B10] opcode[7:0]     : 0x{b10:02X}  \\")
        _p(f"  [B11] opcode[15:8]    : 0x{b11:02X}   > opcode = \033[1m0x{opcode:04X}\033[0m  {opcode_str}  (little-endian 16-bit)")
        _p(f"{D}         MUST match the opcode sent in the request; confirms which command this response is for{R}")

        b12 = resp_frame[cs + 5]
        b13 = resp_frame[cs + 6]
        b14 = resp_frame[cs + 7]
        plen_lo = int.from_bytes(resp_frame[cs+5:cs+7], "little")
        plen_hi = b14 & 0x1F
        plen    = plen_lo | (plen_hi << 16)
        is_bg   = bool((b14 >> 7) & 1)
        bg_desc = "YES -- switch started background operation (CONFIGURE_PID_BINDING)" if is_bg else "no -- switch completed command inline"
        _p(f"  [B12] payload_len[7:0] : 0x{b12:02X}  \\")
        _p(f"  [B13] payload_len[15:8]: 0x{b13:02X}   > payload_length[15:0] = {plen_lo}  (little-endian)")
        _p(f"  [B14] {{bg_op|rsvd|len[19:16]}}: 0x{b14:02X}  = 0b{b14 >> 4:04b}_{b14 & 0x0F:04b}")
        _p(f"{D}         bit[7]   bg_op : {int(is_bg)}    Background Operation flag = {bg_desc}{R}")
        _p(f"{D}         bit[6:5] rsvd  : {(b14 >> 5) & 3:02b}   reserved{R}")
        _p(f"{D}         bit[4:0] extra : {plen_hi:05b} payload_length[19:16] = {plen_hi}  => total response payload = {plen} bytes{R}")

        b15 = resp_frame[cs + 8]
        b16 = resp_frame[cs + 9]
        return_code = int.from_bytes(resp_frame[cs+8:cs+10], "little")
        try:
            rc_name = CCI_RETURN_CODE(return_code).name
        except ValueError:
            rc_name = "UNKNOWN"
        rc_color = "\033[92m" if return_code == 0 else "\033[93m" if return_code == 1 else "\033[91m"
        _p(f"  [B15] return_code[7:0] : 0x{b15:02X}  \\")
        _p(f"  [B16] return_code[15:8]: 0x{b16:02X}   > return_code = {rc_color}{rc_name}\033[0m  (0x{return_code:04X})")
        _p(f"{D}         0x0000=SUCCESS, 0x0001=BACKGROUND_COMMAND_STARTED, 0x0002=INVALID_INPUT{R}")
        _p(f"{D}         0x0003=UNSUPPORTED, 0x0004=INTERNAL_ERROR  (CXL 4.0 Table 7-18){R}")

        b17 = resp_frame[cs + 10]
        b18 = resp_frame[cs + 11]
        vs  = int.from_bytes(resp_frame[cs+10:cs+12], "little")
        _p(f"  [B17] vendor_spec[7:0] : 0x{b17:02X}  \\")
        _p(f"  [B18] vendor_spec[15:8]: 0x{b18:02X}   > vendor_specific_status = 0x{vs:04X}")
        _p(f"{D}         implementation-defined; 0x0000 for standard PBR commands{R}")

        # -- CCI Payload [B19..] -------------------------------------------
        cci_payload = resp_frame[cs + 12:cs + 12 + plen]
        if cci_payload:
            pay_start = cs + 12
            pay_end   = pay_start + len(cci_payload) - 1
            _p(f"{H}  -- CCI Response Payload [B{pay_start:02d}..B{pay_end:02d}]  ({len(cci_payload)} bytes) --------{R}")
            _p(self._hex_dump(cci_payload))
            self._decode_response_payload(opcode, cci_payload)

        # -- PEC -----------------------------------------------------------
        pec     = resp_frame[-1]
        pec_exp = crc8_smbus(resp_frame[:-1])
        pec_ok  = pec == pec_exp
        pec_str = "\033[92mOK\033[0m" if pec_ok else f"\033[91mBAD -- computed 0x{pec_exp:02X}\033[0m"
        lb      = len(resp_frame) - 1
        _p(f"{H}  -- PEC / Packet Error Code (DSP0237 ss.4.1.2) ------{R}")
        _p(f"  [B{lb:02d}] PEC (CRC-8)   : 0x{pec:02X}  [{pec_str}]")
        _p(f"{D}         CRC-8 computed over B00..B{lb - 1} using SMBus polynomial 0x07{R}")
        _p(f"{D}         QEMU SMBus master verifies this before accepting the response{R}")

        _p(f"\033[1m{'-' * 60}\033[0m\n")

    # -- Main client loop ---------------------------------------------------

    async def _process_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        Main request/response loop.

        For each incoming SMBus+MCTP frame:
          1. Print the full packet breakdown (BEFORE any processing)
          2. Parse SMBus header + MCTP header + CCI message
          3. Forward to FM CLI path via send_raw_cci()
          4. Wrap response in SMBus+MCTP response frame
          5. Write response back to client (SMBus Master reads it)
        """
        while True:
            # -- Step 1: Read one full SMBus+MCTP frame -----------------
            raw_frame = await read_smbus_frame(reader)

            # -- Step 2: Parse the frame ---------------------------------
            try:
                req = SmbusMctpRequest.parse(
                    raw_frame,
                    verify_pec=self._verify_pec,
                )
            except ValueError as exc:
                # Print the bad packet BEFORE discarding it
                self._print_rx_packet(raw_frame, None, parse_error=str(exc))
                logger.warning(self._create_message(
                    f"Bad SMBus+MCTP frame ({len(raw_frame)} bytes): {exc}"
                ))
                # Build and send an INVALID_INPUT error response
                err_frame = self._build_error_frame(raw_frame)
                if err_frame:
                    writer.write(err_frame)
                    await writer.drain()
                continue

            # -- Step 3: Print full packet breakdown (before execution) --
            self._print_rx_packet(raw_frame, req)

            opcode_str = get_opcode_string(req.cci_opcode)

            # -- Step 4: Forward CCI to FM CLI path ---------------------
            return_code, response_payload, is_background = \
                await self._forward_to_cli(req.cci_opcode, req.cci_payload)

            logger.debug(self._create_message(
                f"TX SMBus opcode={opcode_str} "
                f"rc={CCI_RETURN_CODE(return_code).name} "
                f"bg={is_background} "
                f"resp={len(response_payload)}B"
            ))

            # -- Step 5: Build SMBus+MCTP response frame -----------------
            resp_frame = build_smbus_mctp_response(
                request=req,
                return_code=int(return_code),
                response_payload=response_payload,
                is_background=is_background,
                fm_i2c_addr=self._fm_i2c_addr,
                compute_pec=True,
            )

            # Print full formatted TX response
            self._print_tx_packet(resp_frame, req)

            # -- Step 6: Send to client (SMBus Master reads this) ---------
            writer.write(resp_frame)
            await writer.drain()

    # -- Error frame builder ------------------------------------------------

    def _build_error_frame(self, raw_frame: bytes) -> bytes:
        """
        Build a best-effort SMBus+MCTP error response (INVALID_INPUT)
        from a raw frame that failed to parse.

        Extracts whatever header fields are readable. Returns empty bytes
        if the frame is too short to build any response.
        """
        from opencis.cxl.component.mctp.smbus_mctp_framing import (
            SMBUS_REQ_HDR_SIZE, MCTP_HDR_VERSION, crc8_smbus,
        )
        if len(raw_frame) < SMBUS_REQ_HDR_SIZE:
            return b""  # too short -- cannot form any response

        # Extract what we can
        src_slave_addr  = raw_frame[3]   # device addr | 1
        dest_eid        = raw_frame[5]   # FM EID from request
        src_eid         = raw_frame[6]   # device EID
        flags           = raw_frame[7]
        msg_tag         = flags & 0x7
        msg_type_byte   = raw_frame[8]
        fm_src          = (self._fm_i2c_addr << 1) | 0x01

        # Minimal CCI error header: RESPONSE, tag=0, opcode=0, INVALID_INPUT
        cci_hdr = bytearray(12)
        cci_hdr[0]  = 0x01                                 # RESPONSE
        cci_hdr[8]  = CCI_RETURN_CODE.INVALID_INPUT & 0xFF
        cci_hdr[9]  = (CCI_RETURN_CODE.INVALID_INPUT >> 8) & 0xFF

        resp_flags = 0xC0 | (msg_tag & 0x7)               # SOM|EOM, TO=0
        body = bytes([fm_src, MCTP_HDR_VERSION,
                      src_eid, dest_eid,
                      resp_flags, msg_type_byte]) + bytes(cci_hdr)
        frame = bytes([len(body)]) + body
        frame += bytes([crc8_smbus(frame)])
        return frame

    # -- FM CLI forwarding --------------------------------------------------

    async def _forward_to_cli(
        self,
        opcode: int,
        payload: bytes,
    ) -> "tuple[CCI_RETURN_CODE, bytes, bool]":
        """
        Forward CCI command to FM CLI path via MctpCciApiClient.send_raw_cci().

        Returns (return_code, response_bytes, is_background).
        If no client is bound, returns UNSUPPORTED.
        """
        if self._mctp_client is None:
            logger.warning(self._create_message(
                f"No mctp_client bound -- UNSUPPORTED for opcode 0x{opcode:04X}"
            ))
            return (CCI_RETURN_CODE.UNSUPPORTED, b"", False)

        try:
            return await self._mctp_client.send_raw_cci(opcode, payload)
        except Exception as exc:
            logger.error(self._create_message(
                f"FM CLI path error for opcode 0x{opcode:04X}: {exc}"
            ))
            return (CCI_RETURN_CODE.INTERNAL_ERROR, b"", False)

    # -- Lifecycle ----------------------------------------------------------

    async def _on_server_stop(self) -> None:
        logger.info(self._create_message("TCP server stopped"))

    async def _run(self) -> None:
        server_task = create_task(self._server.run())
        await self._server.wait_for_ready()
        self._port = self._server.get_port()
        logger.info(self._create_message(
            f"FM SMBus+MCTP CCI server listening on "
            f"{self._host}:{self._port} (DSP0237 framing)"
        ))
        await self._change_status_to_running()
        await gather(server_task)

    async def _stop(self) -> None:
        await self._server.stop()
