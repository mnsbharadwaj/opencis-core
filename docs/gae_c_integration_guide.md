# GAE CCI Command Integration Guide (C/C++)

This integration guide explains how to construct and send CXL 4.0 Generic Access Endpoint (GAE) CCI commands from C/C++ clients over MCTP transport to the CXL Fabric Manager.

---

## 1. GAE Command Set Overview

GAE commands allow a host or manager to manage Generic Fabric Attach (GFA) endpoints, query access vectors, and tunnel CCI commands to downstream Generic Fabric Devices (GFDs).

| Command Name | Opcode | Direction | Request Payload | Response Payload | Description |
|---|---|---|---|---|---|
| **Identify GAE** | `0x5800` | Input | None | VCS index list & support | Retrieves vPPB G-FAM capabilities |
| **Get PID Access Vectors** | `0x5802` | Input | 2 bytes (PID) | 20 bytes (GMV + VTV + PID) | Gets memory & virtual access masks |
| **Proxy GFD Mgmt** | `0x5809` | Background | Header + GFD CMD | 2 bytes (Thread ID) | Spawns a proxy task to relay GFD command |
| **Get Proxy Status** | `0x580A` | Input | 2 bytes (Thread ID) | Header + GFD response | Reads status & results of GFD proxy thread |
| **Cancel Proxy Thread** | `0x580B` | Input | 2 bytes (Thread ID) | None | Terminates a running proxy thread |

---

## 2. MCTP framing & Header Layout (16-Byte Header)

Every CciPayloadPacket sent or received over SMBus or TCP uses a standard 16-byte header:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|Payload Type(4)|         Payload Length (12)           |Port Id|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Msg Class (8) |Category (4)|Res (4)|  Msg Tag (8)  | Reserved |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          Command Opcode (16)          | Payload Length Low(16)|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|P_Len_H(5)|R(2)|B(1)|       Return Code (16)       |  VS_Status|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

---

## 3. Step-by-Step Integration Workflow

### Step 1: Define GAE Request Structures in C
Ensure payloads are packed using compiler directives (`__attribute__((packed))`).

```c
/* 1. Get PID Access Vectors (Opcode 0x5802) */
typedef struct __attribute__((packed)) {
    uint16_t pid;             /* Bits[11:0] */
} gae_get_vectors_req_t;

/* 2. Proxy GFD Management Command (Opcode 0x5809) */
typedef struct __attribute__((packed)) {
    uint16_t gfd_opcode;      /* CCI command opcode to proxy (e.g. 0x0001) */
    uint16_t gfd_payload_len;  /* Length of forwarded command payload */
    uint8_t  gfd_payload[0];   /* Forwarded command payload bytes */
} gae_proxy_mgmt_req_t;

/* 3. Get Proxy Thread Status (Opcode 0x580A) */
typedef struct __attribute__((packed)) {
    uint16_t thread_id;       /* ID returned by Proxy GFD Mgmt command */
} gae_get_status_req_t;

/* 4. Cancel Proxy Thread (Opcode 0x580B) */
typedef struct __attribute__((packed)) {
    uint16_t thread_id;       /* Thread ID to cancel */
} gae_cancel_req_t;
```

### Step 2: Build the MCTP Packet Header
Fill in the 16-byte envelope.

```c
typedef struct {
    uint8_t  data[512];
    uint16_t total_len;
} mctp_pkt_t;

void mctp_build_request(mctp_pkt_t *pkt, uint16_t opcode, uint8_t tag,
                        const void *payload, uint32_t payload_len) {
    uint16_t total = 16 + payload_len;
    memset(pkt->data, 0, 16);

    /* SystemHeader: PayloadType=4 (CCI), Length = total */
    pkt->data[0] = (4 & 0x0F) | (uint8_t)((total & 0x0F) << 4);
    pkt->data[1] = (uint8_t)((total >> 4) & 0xFF);

    /* CciHeader: PortIndex=0, MsgClass=1 (REQ) */
    pkt->data[2] = 0;
    pkt->data[3] = 1;

    /* CciMessageHeader */
    pkt->data[4] = 0;                        /* Category: REQUEST */
    pkt->data[5] = tag;                      /* Message Tag */
    pkt->data[7] = (uint8_t)(opcode & 0xFF); /* Opcode Low */
    pkt->data[8] = (uint8_t)(opcode >> 8);   /* Opcode High */
    pkt->data[9] = (uint8_t)(payload_len & 0xFF);  /* Payload Len Low */
    pkt->data[10] = (uint8_t)(payload_len >> 8);

    if (payload && payload_len) {
        memcpy(pkt->data + 16, payload, payload_len);
    }
    pkt->total_len = total;
}
```

### Step 3: Establish Socket Connection
Connect to the Fabric Manager MCTP port (Default: `8300` for raw TCP adapter, or write to the local SMBus Unix Socket `/tmp/smbus_slave.sock` if using the adapter).

```c
int connect_fm(const char *fm_ip, uint16_t fm_port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(fm_port);
    inet_pton(AF_INET, fm_ip, &addr.sin_addr);
    
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect failed");
        return -1;
    }
    return fd;
}
```

### Step 4: Execute Transaction (Request / Response)
Write request, read system header length, read response payload.

```c
int send_and_wait(int fd, mctp_pkt_t *req, mctp_pkt_t *resp) {
    /* Send */
    send(fd, req->data, req->total_len, 0);

    /* Read 2-byte SystemHeader to learn total size */
    uint8_t sys_hdr[2];
    recv(fd, sys_hdr, 2, MSG_WAITALL);
    uint16_t total_len = (uint16_t)(((sys_hdr[0] >> 4) & 0x0F) | (sys_hdr[1] << 4));

    /* Read rest of packet */
    memcpy(resp->data, sys_hdr, 2);
    recv(fd, resp->data + 2, total_len - 2, MSG_WAITALL);
    resp->total_len = total_len;
    return 0;
}
```

---

## 4. End-to-End C Client Example

Below is a complete test program demonstrating the GAE proxy command lifecycle:

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>

#define OPCODE_IDENTIFY_GAE                 0x5800
#define OPCODE_GET_PID_ACCESS_VECTORS       0x5802
#define OPCODE_PROXY_GFD_MGMT               0x5809
#define OPCODE_GET_PROXY_THREAD_STATUS      0x580A

/* ... structures & builders defined in Section 3 ... */

int main() {
    int fd = connect_fm("127.0.0.1", 8300);
    if (fd < 0) return 1;

    mctp_pkt_t req, resp;
    uint8_t tag = 1;

    /* 1. Identify GAE */
    printf("Sending IDENTIFY_GAE...\n");
    mctp_build_request(&req, OPCODE_IDENTIFY_GAE, tag++, NULL, 0);
    send_and_wait(fd, &req, &resp);
    uint16_t rc = (resp.data[12] | (resp.data[13] << 8));
    printf("IDENTIFY_GAE return code: 0x%04X\n", rc);

    /* 2. Proxy GFD Identify command (GFD opcode = 0x0001) */
    printf("Sending PROXY_GFD_MGMT (Opcode=0x0001)...\n");
    gae_proxy_mgmt_req_t proxy = { .gfd_opcode = 0x0001, .gfd_payload_len = 0 };
    mctp_build_request(&req, OPCODE_PROXY_GFD_MGMT, tag++, &proxy, sizeof(proxy));
    send_and_wait(fd, &req, &resp);
    
    /* Parse proxy thread ID from response payload (after 16-byte header) */
    uint16_t thread_id = (resp.data[16] | (resp.data[17] << 8));
    printf("Proxy thread started. Thread ID = %u\n", thread_id);

    /* 3. Poll status of proxy thread */
    printf("Checking status of Thread %u...\n", thread_id);
    gae_get_status_req_t status = { .thread_id = thread_id };
    mctp_build_request(&req, OPCODE_GET_PROXY_THREAD_STATUS, tag++, &status, sizeof(status));
    send_and_wait(fd, &req, &resp);

    printf("Thread status checked successfully.\n");
    close(fd);
    return 0;
}
```
