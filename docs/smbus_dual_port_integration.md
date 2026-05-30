# SMBus Dual-Port Server — Integration Guide

**Branch:** `smbus_dual_port`  
**Date:** May 2026  
**Spec:** DMTF DSP0237 v1.2.0 — MCTP over SMBus/I2C  

---

## 1. What Changed and Why

### Old design (single port 8301)
Request and response used the **same TCP connection**.  
The QEMU SMBus Slave sent a frame, then immediately read back the response on the same socket.

### New design (dual port: 8301 + 8302)
| Port | Role | Who connects |
|------|------|-------------|
| **8301** | Request Server | QEMU **SMBus Slave** → FM |
| **8302** | Response Server | QEMU **SMBus Master** ← FM |

This matches real SMBus behaviour where:
- The **Slave** drives data onto the bus (initiates write transaction)
- The **Master** reads back the response (separate read transaction)

A shared `asyncio.Queue` inside the FM decouples the two servers:

```
QEMU SMBus Slave  ──TCP:8301──►  Request Server
                                      │
                                   1. read_smbus_frame()         ← DSP0237 request
                                   2. print hex + all fields
                                   3. parse SMBus/MCTP/CCI
                                   4. send_raw_cci(opcode, pl)   ← FM CLI path
                                   5. build_smbus_mctp_response() ← DSP0237 response
                                   6. print hex + all fields
                                   7. asyncio.Queue.put(frame)
                                      │
                              asyncio.Queue (shared, non-blocking)
                                      │
                                   Queue.get() → writer.write(frame)
                                      │
QEMU SMBus Master  ◄──TCP:8302──  Response Server
```

---

## 2. Full Port Map After This Branch

```
opencis FM (run_pbr_env.py)
│
├── :8100   MctpConnectionManager       ← CXL Switch connects here
├── :8200   FabricManagerSocketIoServer ← pbr_fm_cli.py / web UI
├── :8300   FmMctpCciServer             ← external MCTP tools (CciPayloadPacket)
├── :8301   FmSmbusDualPortServer       ← QEMU SMBus Slave sends requests
├── :8302   FmSmbusDualPortServer       ← QEMU SMBus Master reads responses
└── :8700   ShortMsgConn                ← host FM connection
```

---

## 3. DSP0237 Wire Format

### 3a. REQUEST frame (QEMU SMBus Slave → TCP:8301)

```
Byte  0:  dest_slave_addr  = FM_I2C_ADDR << 1             e.g. 0x20 for addr 0x10
Byte  1:  command_code     = 0x0F                          (MCTP over SMBus, fixed)
Byte  2:  byte_count       = frame_len - 3 - 1            (excl. first 3 bytes + PEC)
─── from here counted by byte_count ──────────────────────────────────
Byte  3:  src_slave_addr   = DEV_I2C_ADDR << 1 | 0x01    e.g. 0x41 for addr 0x20
Byte  4:  hdr_ver          = 0x01
Byte  5:  dest_eid         = FM_EID                        e.g. 0x08
Byte  6:  src_eid          = DEVICE_EID                    e.g. 0x09
Byte  7:  flags            = SOM|EOM|PktSeq|TO|MsgTag      e.g. 0xC9 (SOM=EOM=TO=1, tag=1)
Byte  8:  msg_type         = 0x7E                          (CXL FM API)
─── CCI Message Header (12 bytes) ────────────────────────────────────
Byte  9:  message_category = 0x00                          (REQUEST)
Byte 10:  message_tag      = 0x01                          (0..255, echoed in response)
Byte 11:  reserved         = 0x00
Byte 12:  opcode_low                                       e.g. 0x00 for IDENTIFY_PBR_SWITCH
Byte 13:  opcode_high                                      e.g. 0x57
Byte 14:  payload_len_low                                  (0 if no CCI payload)
Byte 15:  payload_len_mid
Byte 16:  payload_len_high | bg_bit
Byte 17:  return_code_low  = 0x00                          (always 0 in request)
Byte 18:  return_code_high = 0x00
Byte 19:  vendor_status_lo = 0x00
Byte 20:  vendor_status_hi = 0x00
─── CCI Payload (0..N bytes) ──────────────────────────────────────────
Bytes 21+: CCI command payload (if any)
─── PEC ───────────────────────────────────────────────────────────────
Last:      PEC = CRC-8(all preceding bytes)  polynomial 0x07
```

**Minimum frame (no CCI payload): 22 bytes**

---

### 3b. RESPONSE frame (TCP:8302 → QEMU SMBus Master)

```
Byte  0:  byte_count       = frame_len - 1 - 1            (excl. byte_count + PEC)
─── from here counted by byte_count ──────────────────────────────────
Byte  1:  fm_src_addr      = FM_I2C_ADDR << 1 | 0x01     e.g. 0x21
Byte  2:  hdr_ver          = 0x01
Byte  3:  dest_eid         = DEVICE_EID                    (swapped from request)
Byte  4:  src_eid          = FM_EID
Byte  5:  flags            = SOM|EOM|PktSeq=0|TO=0|MsgTag (same tag as request)
Byte  6:  msg_type         = 0x7E                          (same as request)
─── CCI Response Header (12 bytes) ────────────────────────────────────
Byte  7:  message_category = 0x01                          (RESPONSE)
Byte  8:  message_tag                                      (echoed from request)
Byte  9:  reserved         = 0x00
Byte 10:  opcode_low                                       (echoed from request)
Byte 11:  opcode_high
Byte 12:  resp_payload_len_low
Byte 13:  resp_payload_len_mid
Byte 14:  resp_payload_len_high | background_bit
Byte 15:  return_code_low                                  0=SUCCESS, 1=BG, 2=INVALID...
Byte 16:  return_code_high
Byte 17:  vendor_status_lo = 0x00
Byte 18:  vendor_status_hi = 0x00
─── CCI Response Payload ───────────────────────────────────────────────
Bytes 19+: switch response payload (if any)
─── PEC ────────────────────────────────────────────────────────────────
Last:      PEC = CRC-8(all preceding bytes)
```

**Minimum response (no payload): 20 bytes**

---

## 4. CRC-8 PEC Calculation

Polynomial: **x⁸ + x² + x + 1 (0x07)**

### C
```c
uint8_t crc8_smbus(const uint8_t *data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
    }
    return crc;
}
```

### Python
```python
def crc8_smbus(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
```

---

## 5. Step-by-Step: Running the System

### Step 1 — Start FM + Switch (Terminal 1)

```bash
cd /workspace/opencis-core
git checkout smbus_dual_port
python run_pbr_env.py
```

Expected startup output:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PBR Test Environment (all-in-one)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FM MCTP (switch) : 0.0.0.0:8100   ← switch connects here
  FM Socket.IO     : 0.0.0.0:8200   ← pbr_fm_cli.py
  FM MCTP CCI      : 0.0.0.0:8300   ← MCTP clients (CciPayloadPacket)
  FM SMBus Slave   : 0.0.0.0:8301   ← QEMU SMBus Slave sends requests
  FM SMBus Master  : 0.0.0.0:8302   ← QEMU SMBus Master reads responses
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[FmSmbusDualPortServer] Request  port : 8301  ← QEMU SMBus Slave (DSP0237 request frames)
[FmSmbusDualPortServer] Response port : 8302  → QEMU SMBus Master (DSP0237 response frames)
```

### Step 2 — Run the test client (Terminal 2)

```bash
python tests/test_smbus_dual_port_client.py
```

The client:
1. Opens **two sockets**: `req_sock → 8301` and `resp_sock → 8302`
2. For each of the 8 CCI commands:
   - Sends DSP0237 request frame on `req_sock` (8301)
   - Waits for DSP0237 response frame on `resp_sock` (8302)
   - Prints TX hex dump, RX hex dump, all decoded fields, pass/fail

```bash
# Options
python tests/test_smbus_dual_port_client.py --host 127.0.0.1
python tests/test_smbus_dual_port_client.py --req-port 8301 --resp-port 8302
python tests/test_smbus_dual_port_client.py --pec            # verify PEC on responses
python tests/test_smbus_dual_port_client.py --timeout 30     # wait longer for slow switch
python tests/test_smbus_dual_port_client.py --stop-on-error  # stop on first failure
```

### Step 3 — What you see on the FM terminal

For every request received:
```
════════════════════════════════════════════════════════════════
  SMBus+MCTP RX  [22 bytes]  req-port 8301
════════════════════════════════════════════════════════════════
  Raw bytes:
    0000  20 0F 12 41 01 08 09 C9 7E 00 01 00 00 57 00 00  | A.....~....W..
    0010  00 00 00 00 00 XX                                |......|
  ── SMBus Header ─────────────────────────────────────────
    dest_slave_addr : 0x20  (i2c 0x10, dir=WRITE)
    command_code    : 0x0F  (MCTP)
    byte_count      : 18
    src_slave_addr  : 0x41  (i2c 0x20)
  ── MCTP Transport Header ────────────────────────────────
    dest_eid   : 0x08  (FM)
    src_eid    : 0x09  (device)
    SOM:1  EOM:1  pkt_seq:0  TO:1  msg_tag:1
  ── MCTP Message / CCI Header ────────────────────────────
    msg_type : 0x7E (CXL_FM_API)
    opcode   : 0x5700  (IDENTIFY_PBR_SWITCH)
    cci_tag  : 1
    payload  : 0 bytes
    pec      : 0xXX  [OK]
────────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════
  SMBus+MCTP TX  [25 bytes]  resp-port 8302
════════════════════════════════════════════════════════════════
  Raw bytes:
    0000  17 21 01 09 08 C1 7E 01 01 00 00 57 04 00 00 00  |.!.....W....|
    0010  00 00 00 XX XX XX XX XX                          |........|
  ── SMBus Response Header ────────────────────────────────
    byte_count      : 23
    fm_src_addr     : 0x21  (i2c 0x10, dir=READ)
  ── MCTP Transport Header ────────────────────────────────
    dest_eid   : 0x09  (device)
    src_eid    : 0x08  (FM)
    SOM:1  EOM:1  TO:0  msg_tag:1
  ── MCTP Message / CCI Response Header ──────────────────
    msg_type    : 0x7E (CXL_FM_API)
    category    : 1  (RESPONSE)
    opcode      : 0x5700  (IDENTIFY_PBR_SWITCH)
    return_code : SUCCESS  (0x0000)
    background  : False
    payload     : 4 bytes
  ── CCI Response Payload ─────────────────────────────────
    0000  XX XX XX XX                                      |....|
  ── PEC (CRC-8) ──────────────────────────────────────────
    pec        : 0xXX
  → Queuing to resp-port 8302 (QEMU SMBus Master)
────────────────────────────────────────────────────────────────
```

---

## 6. QEMU C Integration

### 6a. Frame builder

```c
#include <stdint.h>
#include <string.h>

#define SMBUS_MCTP_CMD   0x0F
#define MCTP_HDR_VER     0x01
#define MCTP_MSG_TYPE    0x7E
#define CCI_HDR_SIZE     12

static uint8_t crc8_smbus(const uint8_t *data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
    }
    return crc;
}

/*
 * Build a DSP0237 SMBus+MCTP request frame.
 * Returns total frame length.
 */
int smbus_mctp_build_request(
    uint8_t *buf,
    uint16_t opcode,
    const uint8_t *cci_payload, uint16_t payload_len,
    uint8_t cci_tag, uint8_t msg_tag,
    uint8_t fm_i2c_addr,     /* 7-bit, e.g. 0x10 */
    uint8_t dev_i2c_addr,    /* 7-bit, e.g. 0x20 */
    uint8_t fm_eid,          /* e.g. 0x08 */
    uint8_t dev_eid)         /* e.g. 0x09 */
{
    uint8_t cci_hdr[CCI_HDR_SIZE] = {0};
    cci_hdr[0] = 0x00;                    /* REQUEST */
    cci_hdr[1] = cci_tag;
    cci_hdr[3] = opcode & 0xFF;
    cci_hdr[4] = (opcode >> 8) & 0xFF;
    cci_hdr[5] = payload_len & 0xFF;
    cci_hdr[6] = (payload_len >> 8) & 0xFF;

    /* SOM=1, EOM=1, TO=1 (tag owner), msg_tag */
    uint8_t flags = 0xC8 | (msg_tag & 0x07);

    /* Body (everything after byte_count) */
    uint8_t body[512];
    int bi = 0;
    body[bi++] = (dev_i2c_addr << 1) | 0x01;  /* src_slave_addr */
    body[bi++] = MCTP_HDR_VER;
    body[bi++] = fm_eid;
    body[bi++] = dev_eid;
    body[bi++] = flags;
    body[bi++] = MCTP_MSG_TYPE;
    memcpy(body + bi, cci_hdr, CCI_HDR_SIZE); bi += CCI_HDR_SIZE;
    if (cci_payload && payload_len)
        { memcpy(body + bi, cci_payload, payload_len); bi += payload_len; }

    /* Full frame */
    int fi = 0;
    buf[fi++] = (fm_i2c_addr << 1) & 0xFE;   /* dest_slave_addr (write) */
    buf[fi++] = SMBUS_MCTP_CMD;
    buf[fi++] = (uint8_t)bi;                  /* byte_count */
    memcpy(buf + fi, body, bi); fi += bi;
    buf[fi] = crc8_smbus(buf, fi); fi++;
    return fi;
}
```

### 6b. Response parser

```c
typedef struct {
    uint16_t opcode;
    uint8_t  cci_tag;
    uint16_t return_code;
    int      background;
    uint8_t  payload[256];
    uint16_t payload_len;
    int      pec_ok;
} SmbusResponse;

int smbus_mctp_parse_response(const uint8_t *buf, int len, SmbusResponse *r) {
    if (len < 20) return -1;
    uint8_t pec_rx = buf[len - 1];
    r->pec_ok = (crc8_smbus(buf, len - 1) == pec_rx);

    const uint8_t *cci = buf + 7;            /* CCI header starts at byte 7 */
    r->opcode      = cci[3] | ((uint16_t)cci[4] << 8);
    r->cci_tag     = cci[1];
    r->return_code = cci[8] | ((uint16_t)cci[9] << 8);
    r->background  = (cci[7] >> 7) & 1;
    r->payload_len = cci[5] | ((uint16_t)cci[6] << 8);
    if (r->payload_len > sizeof(r->payload)) r->payload_len = sizeof(r->payload);
    memcpy(r->payload, buf + 19, r->payload_len);
    return 0;
}
```

### 6c. TCP socket wrappers

```c
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

#define FM_HOST "127.0.0.1"
#define FM_REQ_PORT 8301
#define FM_RESP_PORT 8302

static int open_tcp(const char *host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port   = htons(port),
    };
    inet_pton(AF_INET, host, &addr.sin_addr);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd); return -1;
    }
    return fd;
}

static int recv_exact(int fd, uint8_t *buf, int n) {
    int got = 0;
    while (got < n) {
        int r = read(fd, buf + got, n - got);
        if (r <= 0) return -1;
        got += r;
    }
    return 0;
}

static int recv_response_frame(int resp_fd, uint8_t *buf) {
    /* First byte = byte_count */
    if (recv_exact(resp_fd, buf, 1) < 0) return -1;
    int byte_count = buf[0];
    /* Read body + PEC */
    if (recv_exact(resp_fd, buf + 1, byte_count + 1) < 0) return -1;
    return 1 + byte_count + 1;  /* total frame length */
}
```

### 6d. Complete CXL device model integration

```c
/* smbus_cxl_ep.c — QEMU CXL endpoint SMBus device model */

typedef struct CxlSmbusEp {
    SMBusDevice parent;
    int req_fd;     /* connected to FM port 8301 */
    int resp_fd;    /* connected to FM port 8302 */
    uint8_t cci_tag;
} CxlSmbusEp;

static void cxl_smbus_ep_realize(SMBusDevice *dev, Error **errp) {
    CxlSmbusEp *s = CXL_SMBUS_EP(dev);

    /* Open persistent connections to both FM ports */
    s->req_fd = open_tcp(FM_HOST, FM_REQ_PORT);
    if (s->req_fd < 0) {
        error_setg(errp, "Cannot connect to FM req port %d", FM_REQ_PORT);
        return;
    }
    s->resp_fd = open_tcp(FM_HOST, FM_RESP_PORT);
    if (s->resp_fd < 0) {
        error_setg(errp, "Cannot connect to FM resp port %d", FM_RESP_PORT);
        return;
    }
    s->cci_tag = 0;
}

static void cxl_smbus_ep_unrealize(SMBusDevice *dev) {
    CxlSmbusEp *s = CXL_SMBUS_EP(dev);
    close(s->req_fd);
    close(s->resp_fd);
}

/*
 * Called by QEMU SMBus Master to send a CCI command.
 * Returns 0 on success.
 */
static int cxl_cci_transaction(
    CxlSmbusEp *s,
    uint16_t opcode,
    const uint8_t *req_payload, uint16_t req_len,
    SmbusResponse *resp)
{
    uint8_t frame[512];
    uint8_t resp_buf[512];

    /* Build request */
    int flen = smbus_mctp_build_request(
        frame, opcode, req_payload, req_len,
        ++s->cci_tag, s->cci_tag & 0x7,
        0x10, 0x20,   /* FM addr, dev addr */
        0x08, 0x09    /* FM EID, dev EID   */
    );

    /* Send on req socket (port 8301) */
    if (write(s->req_fd, frame, flen) != flen) return -1;

    /* Read response on resp socket (port 8302) */
    int rlen = recv_response_frame(s->resp_fd, resp_buf);
    if (rlen < 0) return -1;

    /* Parse response */
    return smbus_mctp_parse_response(resp_buf, rlen, resp);
}

/* QEMU SMBus write callback → send CCI command */
static int cxl_smbus_write_data(SMBusDevice *dev, uint8_t *buf, uint8_t len) {
    CxlSmbusEp *s = CXL_SMBUS_EP(dev);
    /* buf[0]=opcode_low, buf[1]=opcode_high, buf[2..]=payload */
    uint16_t opcode = buf[0] | ((uint16_t)buf[1] << 8);
    SmbusResponse resp;
    return cxl_cci_transaction(s, opcode, buf + 2, len - 2, &resp);
}
```

---

## 7. CCI Command Reference

| Opcode | Command | Request Payload | Response Payload |
|--------|---------|----------------|-----------------|
| `0x5700` | IDENTIFY_PBR_SWITCH | none | switch capabilities |
| `0x5704` | CONFIGURE_PID_ASSIGNMENT | pid + target entries | none |
| `0x5705` | GET_PID_BINDING | vcs_id, vppb_id | binding info |
| `0x5706` | CONFIGURE_PID_BINDING | op + vcs + vppb + pid | **BACKGROUND** |
| `0x5708` | GET_DRT | pid (2B) | DRT entry |
| `0x5709` | SET_DRT | pid + entry | none |
| `0x5800` | IDENTIFY_GAE | none | GAE capabilities |

### Return codes

| Code | Name | Meaning |
|------|------|---------|
| `0x0000` | SUCCESS | Command completed |
| `0x0001` | BACKGROUND_COMMAND_STARTED | Long-running command accepted |
| `0x0002` | INVALID_INPUT | Bad request parameters |
| `0x0003` | UNSUPPORTED | FM not ready / switch not connected |
| `0x0004` | INTERNAL_ERROR | FM internal failure |

---

## 8. Files Changed in This Branch

| File | Change |
|------|--------|
| [`opencis/cxl/component/mctp/fm_smbus_dual_port_server.py`](../opencis/cxl/component/mctp/fm_smbus_dual_port_server.py) | **NEW** — FmSmbusDualPortServer (req:8301, resp:8302) |
| [`opencis/apps/fabric_manager.py`](../opencis/apps/fabric_manager.py) | Replace FmSmbusMctpServer → FmSmbusDualPortServer; add `fm_smbus_req_port`, `fm_smbus_resp_port` params |
| [`run_pbr_env.py`](../run_pbr_env.py) | Add `--smbus-req-port` / `--smbus-resp-port` CLI args; update banner |
| [`tests/test_smbus_dual_port_client.py`](../tests/test_smbus_dual_port_client.py) | **NEW** — integration test client (two sockets: req + resp) |

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` on 8301 | FM not started | `python run_pbr_env.py` first |
| `Connection refused` on 8302 | FM not started or wrong branch | Ensure you are on `smbus_dual_port` branch |
| Response TIMEOUT on 8302 | Switch not connected to FM yet | Wait for FM+Switch to finish startup |
| `Parse ERROR: Bad SMBus command code` | Byte[1] ≠ 0x0F | Set `command_code = 0x0F` in frame |
| `Parse ERROR: SMBus request too short` | Frame < 22 bytes | Ensure full 12-byte CCI header is included |
| Response PEC `BAD` | PEC computed over wrong bytes | Include ALL bytes before PEC (incl. byte_count) |
| `return_code = UNSUPPORTED` | MctpCciApiClient not bound | Wait for switch to connect to FM (port 8100) |
| Request sent but no response | resp_sock not connected before sending | Connect to 8302 BEFORE sending on 8301 |
| Multiple clients on 8302 | Queue drains to first connected master | Use one Master connection per FM session |

---

## 10. Quick Verification Checklist

```bash
# 1. Check out branch
git checkout smbus_dual_port

# 2. Verify both new files exist
ls opencis/cxl/component/mctp/fm_smbus_dual_port_server.py
ls tests/test_smbus_dual_port_client.py

# 3. Verify fabric_manager.py has dual ports
grep "fm_smbus_req_port\|fm_smbus_resp_port\|FmSmbusDualPortServer" \
    opencis/apps/fabric_manager.py

# 4. Start FM (Terminal 1)
python run_pbr_env.py

# 5. Run test client (Terminal 2) — all 8 tests should pass
python tests/test_smbus_dual_port_client.py

# 6. Verify ports are open (while FM is running)
nc -zv 127.0.0.1 8301   # Expected: succeeded
nc -zv 127.0.0.1 8302   # Expected: succeeded
```
