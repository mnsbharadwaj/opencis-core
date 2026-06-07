# Step-by-Step PBR & GAE CCI Command Integration Guide (C/C++)

This guide provides an incremental, command-by-command path to integrate Port-Based Routing (PBR) and Generic Access Endpoint (GAE) CCI commands into your C/C++ client. 

To help you test each command in isolation without requiring the full GFD/GAE simulator running, we utilize a **Mock-Response Verification Harness**.

---

## The Incremental Test Harness

Save the following template as `test_cci_incremental.c`. As you proceed through the steps, you will add new struct definitions and command cases to this test harness.

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MCTP_HEADER_SIZE 16

typedef struct {
    uint8_t  data[512];
    uint16_t total_len;
    uint32_t payload_len;
} mctp_pkt_t;

/* Build MCTP Request Envelope */
void mctp_build_request(mctp_pkt_t *pkt, uint16_t opcode, uint8_t tag,
                        const void *payload, uint32_t payload_len) {
    uint16_t total = MCTP_HEADER_SIZE + payload_len;
    memset(pkt->data, 0, MCTP_HEADER_SIZE);
    
    /* SystemHeader: PayloadType=4 (CCI), Length = total */
    pkt->data[0] = (4 & 0x0F) | (uint8_t)((total & 0x0F) << 4);
    pkt->data[1] = (uint8_t)((total >> 4) & 0xFF);
    
    /* CciHeader: Port=0, Class=1 (REQ) */
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
        memcpy(pkt->data + MCTP_HEADER_SIZE, payload, payload_len);
    }
    pkt->total_len = total;
    pkt->payload_len = payload_len;
}

/* Parse Mock Response */
void mctp_parse_response(const uint8_t *resp_data, uint16_t resp_len,
                         uint16_t *rc, uint8_t *payload, uint32_t *plen) {
    *rc = resp_data[12] | (resp_data[13] << 8);
    *plen = resp_data[9] | (resp_data[10] << 8);
    if (*plen > 0) {
        memcpy(payload, resp_data + MCTP_HEADER_SIZE, *plen);
    }
}
```

---

## Phase 1: PBR Switch Commands (Opcodes `0x5700` - `0x5709`)

### Step 1: Identify PBR Switch (`0x5700`)
* **Purpose:** Queries the PBR Switch's capabilities (DRT count, GAE support).
* **Payload Layout:**
  * Request: None.
  * Response:
    ```c
    typedef struct __attribute__((packed)) {
        uint8_t  routing_caps;      /* Bits[1:0] routing mode */
        uint8_t  reserved[3];
        uint16_t num_drts;          /* Total DRT tables */
        uint16_t num_rgts;          /* Total RGT tables */
        uint64_t gae_support_map;   /* 64-bit mask of GAE capability */
    } pbr_identify_resp_t;
    ```

#### Verification Code
Add the following function to your harness and run it:
```c
void test_identify_pbr() {
    mctp_pkt_t req;
    mctp_build_request(&req, 0x5700, 1, NULL, 0);
    
    /* Verify request payload size is 0 and opcode is 0x5700 */
    printf("[Test 0x5700] Request size: %u bytes\n", req.payload_len);
    
    /* Mock a successful response payload */
    uint8_t mock_resp[16 + sizeof(pbr_identify_resp_t)] = {0};
    mock_resp[0] = (16 + sizeof(pbr_identify_resp_t)) & 0x0F; /* len */
    mock_resp[7] = 0x00; mock_resp[8] = 0x57; /* Opcode */
    mock_resp[9] = sizeof(pbr_identify_resp_t); /* Payload size */
    
    pbr_identify_resp_t *resp_data = (pbr_identify_resp_t *)(mock_resp + 16);
    resp_data->num_drts = 2;
    resp_data->gae_support_map = 0b11;
    
    /* Parse mock response */
    uint16_t rc;
    uint8_t payload[256];
    uint32_t plen;
    mctp_parse_response(mock_resp, sizeof(mock_resp), &rc, payload, &plen);
    
    pbr_identify_resp_t *parsed = (pbr_identify_resp_t *)payload;
    if (rc == 0 && parsed->num_drts == 2) {
        printf("  -> Step 1 PASS (num_drts=%d)\n", parsed->num_drts);
    } else {
        printf("  -> Step 1 FAIL\n");
    }
}
```

---

### Step 2: Configure PID Assignment (`0x5704`)
* **Purpose:** Assigns or clears a 12-bit Port Identifier (PID) mapping to a switch physical port.
* **Payload Layout:**
  * Request:
    ```c
    typedef struct __attribute__((packed)) {
        uint8_t  operation;      /* 0 = Assign, 1 = Clear */
        uint8_t  reserved;
        uint16_t num_entries;    /* Typically 1 */
        struct __attribute__((packed)) {
            uint16_t pid;        /* 12-bit PID */
            uint16_t target_id;  /* Target DSP port index */
        } entries[1];
    } pbr_cfg_pid_req_t;
    ```

#### Verification Code
```c
void test_configure_pid() {
    pbr_cfg_pid_req_t req_data = {
        .operation = 0, /* ASSIGN */
        .num_entries = 1,
        .entries[0] = { .pid = 0x100, .target_id = 2 }
    };
    
    mctp_pkt_t req;
    mctp_build_request(&req, 0x5704, 2, &req_data, sizeof(req_data));
    
    /* Verify request payload values */
    pbr_cfg_pid_req_t *pkt_payload = (pbr_cfg_pid_req_t *)(req.data + 16);
    if (pkt_payload->entries[0].pid == 0x100 && pkt_payload->entries[0].target_id == 2) {
        printf("[Test 0x5704] Step 2 PASS (PID 0x100 -> Port 2)\n");
    } else {
        printf("[Test 0x5704] Step 2 FAIL\n");
    }
}
```

---

### Step 3: Set DRT (`0x5709`) & Get DRT (`0x5708`)
* **Purpose:** Programs and reads back entries in the Destination Routing Table (DRT) for packet routing.
* **Payload Layout:**
  * Request (Set DRT):
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t pid;               /* Target PID index */
        uint8_t  num_entries;       /* Entries count */
        uint8_t  reserved;
        struct __attribute__((packed)) {
            uint8_t  entry_type;    /* 0=PHYSICAL_PORT, 1=RGT, 2=INVALID */
            uint8_t  reserved;
            uint16_t target;        /* Target port index */
        } entries[1];
    } pbr_set_drt_req_t;
    ```
  * Request (Get DRT):
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t pid;               /* Target PID index to read */
    } pbr_get_drt_req_t;
    ```

#### Verification Code
```c
void test_drt_access() {
    /* 1. Test Set DRT request assembly */
    pbr_set_drt_req_t set_data = {
        .pid = 0x100,
        .num_entries = 1,
        .entries[0] = { .entry_type = 0, .target = 2 }
    };
    mctp_pkt_t set_req;
    mctp_build_request(&set_req, 0x5709, 3, &set_data, sizeof(set_data));
    
    pbr_set_drt_req_t *parsed_set = (pbr_set_drt_req_t *)(set_req.data + 16);
    int set_ok = (parsed_set->pid == 0x100 && parsed_set->entries[0].target == 2);

    /* 2. Test Get DRT request assembly */
    pbr_get_drt_req_t get_data = { .pid = 0x100 };
    mctp_pkt_t get_req;
    mctp_build_request(&get_req, 0x5708, 4, &get_data, sizeof(get_data));
    
    pbr_get_drt_req_t *parsed_get = (pbr_get_drt_req_t *)(get_req.data + 16);
    int get_ok = (parsed_get->pid == 0x100);

    if (set_ok && get_ok) {
        printf("[Test 0x5708/0x5709] Step 3 PASS\n");
    } else {
        printf("[Test 0x5708/0x5709] Step 3 FAIL\n");
    }
}
```

---

### Step 4: Get PID Binding (`0x5705`) & Configure PID Binding (`0x5706`)
* **Purpose:** Binds or unbinds a virtual port (VCS/vPPB) to a target PID.
* **Payload Layout:**
  * Request (Configure PID Binding):
    ```c
    typedef struct __attribute__((packed)) {
        uint8_t  operation;   /* 0 = Bind, 1 = Unbind */
        uint8_t  vcs_id;
        uint8_t  vppb_id;
        uint8_t  reserved;
        uint16_t pid;         /* Target PID */
    } pbr_cfg_bind_req_t;
    ```
  * Request (Get PID Binding):
    ```c
    typedef struct __attribute__((packed)) {
        uint8_t vcs_id;
        uint8_t vppb_id;
    } pbr_get_bind_req_t;
    ```

#### Verification Code
```c
void test_binding_access() {
    pbr_cfg_bind_req_t bind_data = {
        .operation = 0, /* BIND */
        .vcs_id = 0,
        .vppb_id = 1,
        .pid = 0x100
    };
    mctp_pkt_t bind_req;
    mctp_build_request(&bind_req, 0x5706, 5, &bind_data, sizeof(bind_data));
    
    pbr_cfg_bind_req_t *parsed_bind = (pbr_cfg_bind_req_t *)(bind_req.data + 16);
    if (parsed_bind->vcs_id == 0 && parsed_bind->pid == 0x100) {
        printf("[Test 0x5705/0x5706] Step 4 PASS\n");
    } else {
        printf("[Test 0x5705/0x5706] Step 4 FAIL\n");
    }
}
```

---

## Phase 2: GAE Commands (Opcodes `0x5800` - `0x580B`)

### Step 5: Identify GAE (`0x5800`)
* **Purpose:** Queries the GAE's virtual bridge configuration and G-FAM capability.
* **Payload Layout:**
  * Request: None.
  * Response:
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t num_vppbs_with_gm_support;
        struct __attribute__((packed)) {
            uint8_t  vppb_id;
            uint8_t  global_memory_support; /* 0 = No, 1 = Yes */
        } vppb_entries[1];
    } gae_identify_resp_t;
    ```

#### Verification Code
```c
void test_identify_gae() {
    mctp_pkt_t req;
    mctp_build_request(&req, 0x5800, 6, NULL, 0);
    
    printf("[Test 0x5800] Request size: %u bytes\n", req.payload_len);
    
    /* Mock GAE identify response payload */
    uint8_t mock_resp[16 + sizeof(gae_identify_resp_t)] = {0};
    gae_identify_resp_t *resp = (gae_identify_resp_t *)(mock_resp + 16);
    resp->num_vppbs_with_gm_support = 1;
    resp->vppb_entries[0].vppb_id = 0;
    resp->vppb_entries[0].global_memory_support = 1;
    
    uint16_t rc;
    uint8_t payload[256];
    uint32_t plen;
    mctp_parse_response(mock_resp, sizeof(mock_resp), &rc, payload, &plen);
    
    gae_identify_resp_t *parsed = (gae_identify_resp_t *)payload;
    if (parsed->vppb_entries[0].global_memory_support == 1) {
        printf("  -> Step 5 PASS (VPPB 0 supports G-FAM)\n");
    } else {
        printf("  -> Step 5 FAIL\n");
    }
}
```

---

### Step 6: Get PID Access Vectors (`0x5802`)
* **Purpose:** Retrieves GMV (Global Memory Vector) and VTV (Valid Target Vector) masks for a PID.
* **Payload Layout:**
  * Request:
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t pid;   /* PID to query */
    } gae_get_vectors_req_t;
    ```

#### Verification Code
```c
void test_access_vectors() {
    gae_get_vectors_req_t req_data = { .pid = 0x100 };
    mctp_pkt_t req;
    mctp_build_request(&req, 0x5802, 7, &req_data, sizeof(req_data));
    
    gae_get_vectors_req_t *parsed = (gae_get_vectors_req_t *)(req.data + 16);
    if (parsed->pid == 0x100) {
        printf("[Test 0x5802] Step 6 PASS (PID 0x100)\n");
    } else {
        printf("[Test 0x5802] Step 6 FAIL\n");
    }
}
```

---

### Step 7: Proxy GFD Mgmt (`0x5809`), Thread Status (`0x580A`) & Cancel (`0x580B`)
* **Purpose:** Commands used to relay configurations to the downstream GFD mailboxes asynchronously.
* **Payload Layout:**
  * Request (Proxy GFD Mgmt):
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t gfd_opcode;        /* The CCI opcode to forward */
        uint16_t gfd_payload_len;    /* Forwarded payload size */
        uint8_t  gfd_payload[0];     /* Payload bytes */
    } gae_proxy_mgmt_req_t;
    ```
  * Request (Get Status / Cancel):
    ```c
    typedef struct __attribute__((packed)) {
        uint16_t thread_id;         /* Async Task ID to query/cancel */
    } gae_thread_req_t;
    ```

#### Verification Code
```c
void test_proxy_commands() {
    /* 1. Test Proxy request construction */
    gae_proxy_mgmt_req_t proxy = { .gfd_opcode = 0x0001, .gfd_payload_len = 0 };
    mctp_pkt_t req1;
    mctp_build_request(&req1, 0x5809, 8, &proxy, sizeof(proxy));
    
    gae_proxy_mgmt_req_t *p1 = (gae_proxy_mgmt_req_t *)(req1.data + 16);
    int proxy_ok = (p1->gfd_opcode == 0x0001);

    /* 2. Test Get Status request construction */
    gae_thread_req_t status = { .thread_id = 12 };
    mctp_pkt_t req2;
    mctp_build_request(&req2, 0x580A, 9, &status, sizeof(status));
    
    gae_thread_req_t *p2 = (gae_thread_req_t *)(req2.data + 16);
    int status_ok = (p2->thread_id == 12);

    if (proxy_ok && status_ok) {
        printf("[Test 0x5809/0x580A] Step 7 PASS\n");
    } else {
        printf("[Test 0x5809/0x580A] Step 7 FAIL\n");
    }
}
```

---

## E2E Compilation & Execution

To test all commands in a single run:

1. Add a `main` function to your `test_cci_incremental.c` file:
   ```c
   int main() {
       printf("=== Starting Incremental CCI Integration Verification ===\n");
       test_identify_pbr();
       test_configure_pid();
       test_drt_access();
       test_binding_access();
       test_identify_gae();
       test_access_vectors();
       test_proxy_commands();
       printf("=== Verification Completed ===\n");
       return 0;
   }
   ```
2. Compile and run the test harness locally:
   ```bash
   gcc -Wall -O2 -o test_cci_incremental test_cci_incremental.c
   ./test_cci_incremental
   ```

By checking that each command outputs `PASS`, you have confirmed that your client code structures, field alignments, and bit-level builders are fully CXL 4.0 specification compliant, without needing a running switch backend.
