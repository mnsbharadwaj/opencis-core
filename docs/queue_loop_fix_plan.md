# Implementation Plan: Asyncio Queue Event Loop Fix

This document describes the event loop desynchronization bug identified in the CXL/MCTP queue properties, the implementation details of the fix, and how it resolves the issue.

---

## 1. Problem & Root Cause

In the original codebase, both `MctpConnection` and `FifoPair` implemented lazy queue initialization properties that recreated queues whenever accessed under a different running event loop:

```python
# mctp_connection.py / fifo_pair.py (Old Property Pattern)
@property
def controller_to_ep(self) -> Queue:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if self._controller_to_ep is None:
            self._controller_to_ep = Queue()
        return self._controller_to_ep
    if self._controller_to_ep is None or getattr(self._controller_to_ep, "_loop", None) != loop:
        self._controller_to_ep = Queue()
    return self._controller_to_ep
```

This design has a major flaw when working with components that cache properties:
1. During initialization, `MctpPacketProcessor` caches the queue references (e.g., `self._incoming = self._mctp_connection.controller_to_ep`).
2. Later, if the property is accessed from another event loop context (e.g., during mock setup, test checks, or log formatting), `getattr(..., "_loop") != loop` evaluates to `True`.
3. The property **silently creates a new Queue instance** and overwrites the connection attribute.
4. The packet processor continues reading/writing to the **old cached Queue**, while the executor or client reads/writes to the **new Queue**.
5. As a result, the two sides become disconnected. Packets written by one side are never received by the other, causing a **permanent hang/deadlock** in `MctpPacketReader`.

---

## 2. Proposed Changes

We replace the active loop comparison with an **event loop status check** (`is_closed()`). Rather than recreating queues whenever a property is read under a different loop, we only recreate the queue if the loop it was originally bound to has actually been closed.

### 2.1 Component: `opencis/cxl/component/mctp/mctp_connection.py`

Modify the properties for `controller_to_ep` and `ep_to_controller` to prevent silent queue recreation:

```python
# Modified mctp_connection.py
    @property
    def controller_to_ep(self) -> Queue:
        loop_closed = False
        if self._controller_to_ep is not None:
            queue_loop = getattr(self._controller_to_ep, "_loop", None)
            if queue_loop is not None and queue_loop.is_closed():
                loop_closed = True
        if self._controller_to_ep is None or loop_closed:
            self._controller_to_ep = Queue()
        return self._controller_to_ep

    @property
    def ep_to_controller(self) -> Queue:
        loop_closed = False
        if self._ep_to_controller is not None:
            queue_loop = getattr(self._ep_to_controller, "_loop", None)
            if queue_loop is not None and queue_loop.is_closed():
                loop_closed = True
        if self._ep_to_controller is None or loop_closed:
            self._ep_to_controller = Queue()
        return self._ep_to_controller
```

### 2.2 Component: `opencis/pci/component/fifo_pair.py`

Apply the same modified property pattern to `host_to_target` and `target_to_host`:

```python
# Modified fifo_pair.py
    @property
    def host_to_target(self) -> Queue:
        loop_closed = False
        if self._host_to_target is not None:
            queue_loop = getattr(self._host_to_target, "_loop", None)
            if queue_loop is not None and queue_loop.is_closed():
                loop_closed = True
        if self._host_to_target is None or loop_closed:
            self._host_to_target = Queue()
        return self._host_to_target

    @property
    def target_to_host(self) -> Queue:
        loop_closed = False
        if self._target_to_host is not None:
            queue_loop = getattr(self._target_to_host, "_loop", None)
            if queue_loop is not None and queue_loop.is_closed():
                loop_closed = True
        if self._target_to_host is None or loop_closed:
            self._target_to_host = Queue()
        return self._target_to_host
```

---

## 3. Why This Fix Works & Resolves the Bug

1. **Maintains Queue Identity:** Because healthy queues are no longer replaced when accessed from other threads or loop contexts, the cached queue reference in `MctpPacketProcessor` stays identical to the queue accessed by `MctpCciExecutor`.
2. **Eliminates Deadlocks:** Relayed TCP packets are successfully pushed into the same queue that the executor is listening to.
3. **Preserves Test Re-usability:** Between sequential pytest runs, when the old event loop is closed and a new one starts, the property detects the closed loop status (`is_closed() == True`) and correctly recreates fresh queues bound to the new event loop.
