# CXL PBR CCI Command Integration Guide (No GFD/GAE)

This guide provides an incremental, command-by-command approach to integrate CXL 4.0 Port-Based Routing (PBR) commands into your C/C++ codebase. It is specifically designed for setups **without Generic Fabric Device (GFD) or Generic Access Endpoint (GAE)** implementations.

By implementing and testing these commands one by one, you can ensure byte-level compatibility, field alignment, and transport layer correctness in your client setup before moving on.

---

## 1. Transport Layer Framing Options

The OpenCIS Fabric Manager (FM) supports two TCP-based transport methods for receiving CCI commands. Choose the one that matches your architecture:

### Option A: Direct MCTP-over-TCP (Port 8300)
This transport uses a simple 16-byte header wrapping the raw CXL CCI payload. The packet is sent directly over a standard TCP connection.

* **SystemHeader (2 bytes):**
  * `bits [3:0]` = Payload Type (`4` for CXL CCI)
  * `bits [15:4]` = Total Packet Length (Header + Payload)
* **CciHeader (2 bytes):**
  * `bits [7:0]` = Switch Port Index (typically `0` for control port)
  * `bits [15:8]` = Message Class (`1` for Request, `2` for Response)
* **CciMessageHeader (12 bytes):**
  * `bits [3:0]` = Message Category (`0` for Request, `1` for Response)
  * `bits [7:4]` = Reserved
  * `bits [15:8]` = Message Tag (Echoed back by FM)
  * `bits [23:16]` = Reserved
  * `bits [39:24]` = Command Opcode (Little-endian)
  * `bits [55:40]` = Payload Length Low (Little-endian)
  * `bits [60:56]` = Payload Length High (5 bits)
  * `bits [62:61]` = Reserved
  * `bit [63]` = Background Operation flag
  * `bits [79:64]` = Return Code (Little-endian, always `0` in Request)
  * `bits [95:80]` = Vendor Extended Status (always `0`)

### Option B: SMBus Dual-Port MCTP-over-TCP (Ports 8301 & 8302)
This transport mirrors DMTF DSP0237 SMBus framing. It separates requests and responses onto two sockets:
* **Port 8301 (Request Server):** Your client connects as an SMBus Slave and sends requests.
* **Port 8302 (Response Server):** Your client connects as an SMBus Master and reads responses.

SMBus frames include an extra SMBus envelope (destination address, command code `0x0F`, byte count, source address, EID, and a final CRC-8 Packet Error Code (PEC) byte).

---

## 2. Shared C Definitions (`mctp_packet.h`)

Create a header file named `mctp_packet.h` containing the byte-level pack/unpack helpers. This code handles the little-endian field assembly safely on both 32-bit and 64-bit platforms.

```c
#ifndef MCTP_PACKET_H
#define MCTP_PACKET_H

#include <stdint.h>
#include <string.h>

#define MCTP_PAYLOAD_TYPE_CCI       4
#define MCTP_MSG_CLASS_REQ          1
#define MCTP_MSG_CLASS_RSP          2
#define MCTP_MSG_CATEGORY_REQUEST   0
#define MCTP_MSG_CATEGORY_RESPONSE  1

/* PBR Opcodes */
#define OPCODE_IDENTIFY_PBR_SWITCH          0x5700
#define OPCODE_CONFIGURE_PID_ASSIGNMENT     0x5704
#define OPCODE_GET_PID_BINDING              0x5705
#define OPCODE_CONFIGURE_PID_BINDING        0x5706
#define OPCODE_GET_DRT                      0x5708
#define OPCODE_SET_DRT                      0x5709

/* Return Codes */
#define CCI_RC_SUCCESS                      0x0000
#define CCI_RC_BACKGROUND_COMMAND_STARTED   0x0001
#define CCI_RC_INVALID_INPUT                0x0002
#define CCI_RC_UNSUPPORTED                  0x0003
#define CCI_RC_INTERNAL_ERROR               0x0004

#define MCTP_HEADER_SIZE    16
#define MCTP_MAX_PAYLOAD    480
#define MCTP_MAX_PACKET     (MCTP_HEADER_SIZE + MCTP_MAX_PAYLOAD)

typedef struct {
    uint8_t  data[MCTP_MAX_PACKET];
    uint16_t total_len;
    uint32_t payload_len;
} mctp_pkt_t;

/* SystemHeader Pack/Unpack */
static inline uint16_t mctp_get_payload_length(const uint8_t *pkt) {
    return (uint16_t)(((pkt[0] >> 4) & 0x0F) | ((uint16_t)pkt[1] << 4));
}

static inline void mctp_set_system_header(uint8_t *pkt, uint16_t payload_length) {
    pkt[0] = (MCTP_PAYLOAD_TYPE_CCI & 0x0F) | (uint8_t)((payload_length & 0x0F) << 4);
    pkt[1] = (uint8_t)((payload_length >> 4) & 0xFF);
}

/* CciHeader Pack */
static inline void mctp_set_cci_header(uint8_t *pkt, uint8_t port_index, uint8_t msg_class) {
    pkt[2] = port_index;
    pkt[3] = msg_class;
}

/* CciMessageHeader Pack/Unpack */
#define MSG_HDR_OFF 4

static inline uint8_t mctp_get_msg_tag(const uint8_t *pkt) {
    return pkt[MSG_HDR_OFF + 1];
}

static inline uint16_t mctp_get_opcode(const uint8_t *pkt) {
    return (uint16_t)pkt[MSG_HDR_OFF + 3] | ((uint16_t)pkt[MSG_HDR_OFF + 4] << 8);
}

static inline uint32_t mctp_get_payload_len(const uint8_t *pkt) {
    uint32_t low  = (uint32_t)pkt[MSG_HDR_OFF + 5] | ((uint32_t)pkt[MSG_HDR_OFF + 6] << 8);
    uint32_t high = (uint32_t)(pkt[MSG_HDR_OFF + 7] & 0x1F);
    return low | (high << 16);
}

static inline uint8_t mctp_get_background(const uint8_t *pkt) {
    return (pkt[MSG_HDR_OFF + 7] >> 7) & 0x01;
}

static inline uint16_t mctp_get_return_code(const uint8_t *pkt) {
    return (uint16_t)pkt[MSG_HDR_OFF + 8] | ((uint16_t)pkt[MSG_HDR_OFF + 9] << 8);
}

static inline void mctp_set_msg_header(uint8_t *pkt, uint8_t tag, uint16_t opcode,
                                       uint32_t payload_len, uint8_t bg, uint16_t rc) {
    uint8_t *m = pkt + MSG_HDR_OFF;
    memset(m, 0, 12);
    m[0] = MCTP_MSG_CATEGORY_REQUEST;
    m[1] = tag;
    m[3] = (uint8_t)(opcode & 0xFF);
    m[4] = (uint8_t)(opcode >> 8);
    m[5] = (uint8_t)(payload_len & 0xFF);
    m[6] = (uint8_t)((payload_len >> 8) & 0xFF);
    m[7] = (uint8_t)((payload_len >> 16) & 0x1F) | (uint8_t)((bg & 0x01) << 7);
    m[8] = (uint8_t)(rc & 0xFF);
    m[9] = (uint8_t)(rc >> 8);
}

static inline void mctp_build_request(mctp_pkt_t *pkt, uint16_t opcode, uint8_t tag,
                                      const void *payload, uint32_t payload_len) {
    uint16_t total = MCTP_HEADER_SIZE + payload_len;
    mctp_set_system_header(pkt->data, total);
    mctp_set_cci_header(pkt->data, 0, MCTP_MSG_CLASS_REQ);
    mctp_set_msg_header(pkt->data, tag, opcode, payload_len, 0, 0);
    
    if (payload && payload_len) {
        memcpy(pkt->data + MCTP_HEADER_SIZE, payload, payload_len);
    }
    pkt->total_len = total;
    pkt->payload_len = payload_len;
}

/* CRC-8 SMBus PEC calculation helper */
static inline uint8_t crc8_smbus(const uint8_t *data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
        }
    }
    return crc;
}

#endif /* MCTP_PACKET_H */
```

---

## 3. Incremental Integration Sequence

To avoid copying all structures at once, we integrate the control plane step-by-step. 

### Step 1: Query Capabilities via `IDENTIFY_PBR_SWITCH` (`0x5700`)
Query the switch configuration. This command does not change switch state, making it ideal for validating basic socket connectivity and packet formatting.

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint8_t  routing_caps;      /* Bits[1:0]: PBR routing caps (01b=PBR) */
    uint8_t  reserved[3];
    uint16_t num_drts;          /* Total DRT tables (typically 1 or 2) */
    uint16_t num_rgts;          /* Total RGT tables (0 for basic setups) */
    uint64_t gae_support_map;   /* 64-bit capability map (will be 0 in your setup) */
} pbr_identify_resp_t;
```

#### Test Loop (C snippet)
```c
void test_step1_identify(int fd) {
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_IDENTIFY_PBR_SWITCH, 1, NULL, 0);
    
    // Write request
    send(fd, req.data, req.total_len, 0);
    
    // Read 16-byte header
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint32_t payload_len = mctp_get_payload_len(resp_hdr);
    uint16_t rc = mctp_get_return_code(resp_hdr);
    
    printf("[Step 1] IDENTIFY_PBR_SWITCH Sent.\n");
    printf("   Return Code: 0x%04X\n", rc);
    printf("   Payload Size: %u bytes\n", payload_len);
    
    if (rc == CCI_RC_SUCCESS && payload_len >= sizeof(pbr_identify_resp_t)) {
        pbr_identify_resp_t resp;
        recv(fd, &resp, sizeof(pbr_identify_resp_t), MSG_WAITALL);
        printf("   → Success! num_drts = %d, gae_support = 0x%llX\n", 
               resp.num_drts, (unsigned long long)resp.gae_support_map);
    } else {
        printf("   → Failed Step 1.\n");
    }
}
```

---

### Step 2: Assign PID via `CONFIGURE_PID_ASSIGNMENT` (`0x5704`)
Before routing traffic, we must map a 12-bit Port Identifier (PID) to a physical port on the switch. 

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint8_t  operation;      /* 0 = Assign/Set, 1 = Clear */
    uint8_t  reserved;
    uint16_t num_entries;    /* Number of mappings to configure */
    struct __attribute__((packed)) {
        uint16_t pid;        /* 12-bit target PID (e.g. 0x010) */
        uint16_t target_id;  /* Target physical port index (e.g. 1) */
    } entries[1];            /* VLA or array of size num_entries */
} pbr_cfg_pid_req_t;
```

#### Test Loop (C snippet)
```c
void test_step2_configure_pid(int fd) {
    pbr_cfg_pid_req_t req_data;
    req_data.operation = 0; // ASSIGN
    req_data.num_entries = 1;
    req_data.entries[0].pid = 0x010;      // Assign PID 0x010
    req_data.entries[0].target_id = 1;    // Bind to Physical Port 1
    
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_CONFIGURE_PID_ASSIGNMENT, 2, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint16_t rc = mctp_get_return_code(resp_hdr);
    printf("[Step 2] CONFIGURE_PID_ASSIGNMENT Sent (PID 0x010 -> Port 1).\n");
    printf("   Return Code: 0x%04X (%s)\n", rc, rc == 0 ? "SUCCESS" : "ERROR");
}
```

---

### Step 3: Query Initial Binding via `GET_PID_BINDING` (`0x5705`)
Query the binding of a virtual port (VCS/vPPB) before setting it. Since we haven't bound it yet, the FM should return `0xFFF` (unbound).

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint8_t vcs_id;     /* Virtual Control Switch ID (usually 0) */
    uint8_t vppb_id;    /* Virtual Physical Port Bridge ID (usually 0) */
} pbr_get_bind_req_t;

typedef struct __attribute__((packed)) {
    uint16_t bound_pid; /* 12-bit PID, or 0xFFF if unbound */
} pbr_get_bind_resp_t;
```

#### Test Loop (C snippet)
```c
void test_step3_get_binding_initial(int fd) {
    pbr_get_bind_req_t req_data = { .vcs_id = 0, .vppb_id = 0 };
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_GET_PID_BINDING, 3, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint32_t payload_len = mctp_get_payload_len(resp_hdr);
    uint16_t rc = mctp_get_return_code(resp_hdr);
    
    printf("[Step 3] GET_PID_BINDING (Initial) Sent.\n");
    if (rc == CCI_RC_SUCCESS && payload_len >= sizeof(pbr_get_bind_resp_t)) {
        pbr_get_bind_resp_t resp;
        recv(fd, &resp, sizeof(pbr_get_bind_resp_t), MSG_WAITALL);
        printf("   → bound_pid: 0x%03X (Expected 0xFFF / Unbound)\n", resp.bound_pid);
    } else {
        printf("   → Error querying initial binding. RC = 0x%04X\n", rc);
    }
}
```

---

### Step 4: Write Routing Entry via `SET_DRT` (`0x5709`)
The Destination Routing Table (DRT) handles egress routing. We program the DRT entry corresponding to PID `0x010` to point to physical Port 1.

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint16_t pid;               /* 12-bit index into the DRT table */
    uint8_t  num_entries;       /* Number of entries to program (usually 1) */
    uint8_t  reserved;
    struct __attribute__((packed)) {
        uint8_t  entry_type;    /* 0 = PHYSICAL_PORT, 1 = RGT_INDEX, 2 = INVALID */
        uint8_t  reserved;
        uint16_t target;        /* Destination physical port index */
    } entries[1];
} pbr_set_drt_req_t;
```

#### Test Loop (C snippet)
```c
void test_step4_set_drt(int fd) {
    pbr_set_drt_req_t req_data;
    req_data.pid = 0x010;
    req_data.num_entries = 1;
    req_data.entries[0].entry_type = 0;   // PHYSICAL_PORT
    req_data.entries[0].target = 1;       // Target Port 1
    
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_SET_DRT, 4, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint16_t rc = mctp_get_return_code(resp_hdr);
    printf("[Step 4] SET_DRT Sent (DRT[0x010] = Port 1).\n");
    printf("   Return Code: 0x%04X (%s)\n", rc, rc == 0 ? "SUCCESS" : "ERROR");
}
```

---

### Step 5: Read and Verify Routing Entry via `GET_DRT` (`0x5708`)
Verify that the entry programmed in Step 4 is correctly written.

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint16_t pid;   /* DRT entry index to read */
} pbr_get_drt_req_t;

typedef struct __attribute__((packed)) {
    uint8_t  entry_type;    /* 0 = PHYSICAL_PORT, 1 = RGT, 2 = INVALID */
    uint8_t  reserved;
    uint16_t target;        /* Target port index */
} pbr_get_drt_resp_t;
```

#### Test Loop (C snippet)
```c
void test_step5_get_drt(int fd) {
    pbr_get_drt_req_t req_data = { .pid = 0x010 };
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_GET_DRT, 5, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint32_t payload_len = mctp_get_payload_len(resp_hdr);
    uint16_t rc = mctp_get_return_code(resp_hdr);
    
    printf("[Step 5] GET_DRT Sent for PID 0x010.\n");
    if (rc == CCI_RC_SUCCESS && payload_len >= sizeof(pbr_get_drt_resp_t)) {
        pbr_get_drt_resp_t resp;
        recv(fd, &resp, sizeof(pbr_get_drt_resp_t), MSG_WAITALL);
        printf("   → DRT Type  : %d (Expected 0 / PHYSICAL_PORT)\n", resp.entry_type);
        printf("   → DRT Target: %d (Expected 1)\n", resp.target);
    } else {
        printf("   → DRT read failed. RC = 0x%04X\n", rc);
    }
}
```

---

### Step 6: Bind Virtual Port via `CONFIGURE_PID_BINDING` (`0x5706`)
This binds Virtual Port Bridge (vPPB) 0 to the physical endpoint PID `0x010`. 

> [!IMPORTANT]
> `CONFIGURE_PID_BINDING` is a **Background Command**. 
> The FM will return the code `0x0001` (`BACKGROUND_COMMAND_STARTED`) immediately. The binding happens asynchronously. 

#### Struct Definition
```c
typedef struct __attribute__((packed)) {
    uint8_t  operation;   /* 0 = Bind, 1 = Unbind */
    uint8_t  vcs_id;      /* Virtual Switch (usually 0) */
    uint8_t  vppb_id;     /* Virtual Port Bridge (usually 0) */
    uint8_t  reserved;
    uint16_t pid;         /* Target PID (e.g. 0x010) */
} pbr_cfg_bind_req_t;
```

#### Test Loop (C snippet)
```c
void test_step6_bind_vppb(int fd) {
    pbr_cfg_bind_req_t req_data;
    req_data.operation = 0; // BIND
    req_data.vcs_id = 0;
    req_data.vppb_id = 0;
    req_data.pid = 0x010;
    
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_CONFIGURE_PID_BINDING, 6, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint16_t rc = mctp_get_return_code(resp_hdr);
    printf("[Step 6] CONFIGURE_PID_BINDING Sent (Bind vPPB 0 -> PID 0x010).\n");
    printf("   Return Code: 0x%04X (Expected 0x0001 / BACKGROUND_COMMAND_STARTED)\n", rc);
    
    // Give background task a small sleep to ensure state settles
    usleep(50000); // 50ms
}
```

---

### Step 7: Verify Binding via `GET_PID_BINDING` (`0x5705`)
Read back the virtual port binding. Since the background command has completed, it should now return the bound PID `0x010` instead of `0xFFF`.

#### Test Loop (C snippet)
```c
void test_step7_verify_binding(int fd) {
    pbr_get_bind_req_t req_data = { .vcs_id = 0, .vppb_id = 0 };
    mctp_pkt_t req;
    mctp_build_request(&req, OPCODE_GET_PID_BINDING, 7, &req_data, sizeof(req_data));
    
    send(fd, req.data, req.total_len, 0);
    
    uint8_t resp_hdr[16];
    recv(fd, resp_hdr, 16, MSG_WAITALL);
    
    uint32_t payload_len = mctp_get_payload_len(resp_hdr);
    uint16_t rc = mctp_get_return_code(resp_hdr);
    
    printf("[Step 7] GET_PID_BINDING (Final verification) Sent.\n");
    if (rc == CCI_RC_SUCCESS && payload_len >= sizeof(pbr_get_bind_resp_t)) {
        pbr_get_bind_resp_t resp;
        recv(fd, &resp, sizeof(pbr_get_bind_resp_t), MSG_WAITALL);
        printf("   → bound_pid: 0x%03X (Expected 0x010 / BOUND)\n", resp.bound_pid);
        if (resp.bound_pid == 0x010) {
            printf("   → SUCCESS: Switch Control Plane Commissioning Complete!\n");
        } else {
            printf("   → FAIL: Expected 0x010 but got 0x%03X\n", resp.bound_pid);
        }
    } else {
        printf("   → Error querying final binding. RC = 0x%04X\n", rc);
    }
}
```

---

## 4. Standalone Test Code: Direct MCTP-over-TCP (`test_mctp.c`)

Save the following file as `test_mctp.c`. You can compile it and run it directly against the FM control port `8300`. It executes the steps sequentially, allowing you to debug them one by one.

```c
#include "mctp_packet.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define FM_IP   "127.0.0.1"
#define FM_PORT 8300

// Declare step functions
void test_step1_identify(int fd);
void test_step2_configure_pid(int fd);
void test_step3_get_binding_initial(int fd);
void test_step4_set_drt(int fd);
void test_step5_get_drt(int fd);
void test_step6_bind_vppb(int fd);
void test_step7_verify_binding(int fd);

int main() {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(FM_PORT),
    };
    inet_pton(AF_INET, FM_IP, &addr.sin_addr);
    
    printf("Connecting to Fabric Manager on %s:%d...\n", FM_IP, FM_PORT);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("Connect failed");
        return 1;
    }
    printf("Connected.\n\n");
    
    // Execute steps incrementally
    test_step1_identify(fd);
    usleep(10000); // 10ms gap
    
    test_step2_configure_pid(fd);
    usleep(10000);
    
    test_step3_get_binding_initial(fd);
    usleep(10000);
    
    test_step4_set_drt(fd);
    usleep(10000);
    
    test_step5_get_drt(fd);
    usleep(10000);
    
    test_step6_bind_vppb(fd);
    usleep(10000);
    
    test_step7_verify_binding(fd);
    
    close(fd);
    return 0;
}
```

Compile with:
```bash
gcc -Wall -O2 -o test_mctp test_mctp.c
```

---

## 5. Standalone Test Code: SMBus Dual-Port (`test_smbus.c`)

If your hardware utilizes the SMBus transport layer instead, save the following as `test_smbus.c`. 
It opens two TCP sockets—one to **8301 (Request Server)** and one to **8302 (Response Server)**—and implements the DMTF DSP0237 envelope and CRC-8 PEC verification.

```c
#include "mctp_packet.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define FM_IP         "127.0.0.1"
#define PORT_REQ      8301
#define PORT_RESP     8302

#define FM_I2C_ADDR   0x10
#define DEV_I2C_ADDR  0x20
#define FM_EID        0x08
#define DEV_EID       0x09

static int connect_tcp(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(port),
    };
    inet_pton(AF_INET, FM_IP, &addr.sin_addr);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        return -1;
    }
    return fd;
}

/* 
 * Build and transmit a DSP0237 SMBus envelope enclosing the MCTP/CCI request
 */
int send_smbus_request(int req_fd, uint16_t opcode, uint8_t tag, const void *payload, uint32_t plen) {
    mctp_pkt_t cci;
    mctp_build_request(&cci, opcode, tag, payload, plen);
    
    uint8_t frame[512] = {0};
    int fi = 0;
    
    // SMBus Envelope Header
    frame[fi++] = (FM_I2C_ADDR << 1) & 0xFE;  // Destination address (Write)
    frame[fi++] = 0x0F;                       // Command Code (MCTP)
    
    // Byte count = Source address (1) + MCTP Header (4) + Message Type (1) + CCI Header (12) + Payload
    uint8_t byte_count = 1 + 4 + 1 + 12 + plen;
    frame[fi++] = byte_count;
    
    // Body (counted by byte_count)
    int body_start = fi;
    frame[fi++] = (DEV_I2C_ADDR << 1) | 0x01; // Source address
    frame[fi++] = 0x01;                       // MCTP Header Version
    frame[fi++] = FM_EID;                     // EID Destination
    frame[fi++] = DEV_EID;                    // EID Source
    frame[fi++] = 0xC8 | (tag & 0x07);        // Flags (SOM=1, EOM=1, TO=1)
    frame[fi++] = 0x7E;                       // Message Type (CXL FM API)
    
    // CCI Header & Payload
    memcpy(frame + fi, cci.data + 4, 12 + plen); // Skip the SystemHeader/CciHeader
    fi += (12 + plen);
    
    // CRC-8 PEC
    frame[fi] = crc8_smbus(frame, fi);
    fi++;
    
    return send(req_fd, frame, fi, 0) == fi ? 0 : -1;
}

/*
 * Receive and parse a DSP0237 SMBus response
 */
int recv_smbus_response(int resp_fd, uint16_t *rc, uint8_t *payload, uint32_t *plen) {
    uint8_t byte_count;
    if (recv(resp_fd, &byte_count, 1, MSG_WAITALL) <= 0) return -1;
    
    uint8_t body[512];
    // Read the body (byte_count) + 1 byte of PEC
    if (recv(resp_fd, body, byte_count + 1, MSG_WAITALL) < byte_count + 1) return -1;
    
    // Verify PEC
    uint8_t check_buf[513];
    check_buf[0] = byte_count;
    memcpy(check_buf + 1, body, byte_count);
    uint8_t calculated_pec = crc8_smbus(check_buf, byte_count + 1);
    uint8_t received_pec = body[byte_count];
    
    if (calculated_pec != received_pec) {
        printf("   [SMBus] PEC check failed (calculated=0x%02X, received=0x%02X)\n", 
               calculated_pec, received_pec);
        return -2;
    }
    
    // CCI Response Header is inside body at offset 6 (after SrcAddr, Version, DestEID, SrcEID, Flags, MsgType)
    uint8_t *cci = body + 6;
    *rc = cci[8] | (cci[9] << 8);
    *plen = cci[5] | (cci[6] << 8);
    
    if (*plen > 0) {
        memcpy(payload, body + 6 + 12, *plen);
    }
    return 0;
}

// Implement your test cases here matching the step-by-step logic in test_mctp.c
int main() {
    printf("Connecting to Dual-Port SMBus Servers...\n");
    int req_fd = connect_tcp(PORT_REQ);
    int resp_fd = connect_tcp(PORT_RESP);
    
    if (req_fd < 0 || resp_fd < 0) {
        printf("Connection failed. Make sure FM is running in dual-port mode.\n");
        return 1;
    }
    printf("Connected.\n\n");
    
    // Example test: Step 1 (Identify PBR Switch)
    printf("[Step 1] Sending Identify PBR Switch via SMBus...\n");
    if (send_smbus_request(req_fd, OPCODE_IDENTIFY_PBR_SWITCH, 1, NULL, 0) == 0) {
        uint16_t rc = 0xFF;
        uint8_t payload[256];
        uint32_t plen = 0;
        
        int status = recv_smbus_response(resp_fd, &rc, payload, &plen);
        if (status == 0) {
            printf("   Return Code: 0x%04X\n", rc);
            if (rc == CCI_RC_SUCCESS) {
                pbr_identify_resp_t *resp = (pbr_identify_resp_t *)payload;
                printf("   → Success! num_drts = %d\n", resp->num_drts);
            }
        } else {
            printf("   → Read failed with status %d\n", status);
        }
    }
    
    close(req_fd);
    close(resp_fd);
    return 0;
}
```

Compile with:
```bash
gcc -Wall -O2 -o test_smbus test_smbus.c
```

---

## 6. How to Run and Verify

1. **Start the Fabric Manager Server:**
   In your workspace terminal, start the environment. Make sure to specify the config file that defines your switch topology.
   ```bash
   python run_pbr_env.py --config-file configs/1vcs_1mld.yaml
   ```
   *Expected Server Banner:*
   ```text
   FM MCTP (switch) : 0.0.0.0:8100
   FM Socket.IO     : 0.0.0.0:8200
   FM MCTP CCI      : 0.0.0.0:8300   ← test_mctp will connect here
   FM SMBus Slave   : 0.0.0.0:8301   ← test_smbus req_fd will connect here
   FM SMBus Master  : 0.0.0.0:8302   ← test_smbus resp_fd will connect here
   ```

2. **Run the Test Executable:**
   In another terminal, run your compiled test executable.
   ```bash
   ./test_mctp
   ```
   
3. **Verify the Output Logs:**
   Verify that each step outputs exactly the expected values (e.g. `bound_pid = 0xFFF` on initial query, and `bound_pid = 0x010` on final query after the background binding command completes).
