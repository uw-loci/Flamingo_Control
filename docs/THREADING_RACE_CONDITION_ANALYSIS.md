# Threading Race Condition Analysis: Callback Listener vs Command Response

## Issue Summary

**Question:** Is there a potential conflict between the command response thread and the unsolicited listening thread?

**Answer:** Yes, there IS a potential race condition, but it's currently **NOT ACTIVE** because the CallbackListener is not being used.

---

## Current State (No Race Condition)

### What's Actually Running

Currently in `MicroscopeCommandService._query_command()` (src/py2flamingo/services/microscope_command_service.py:35-125):

```python
def _query_command(self, command_code, command_name, params=None, value=0.0):
    # 1. Encode command
    cmd_bytes = self.connection.encoder.encode_command(...)

    # 2. Get socket from connection service
    command_socket = self.connection._command_socket

    # 3. Send command
    command_socket.sendall(cmd_bytes)

    # 4. Read response SYNCHRONOUSLY in calling thread
    ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)

    # 5. Parse and return
    parsed = self._parse_response(ack_response)
    return {'success': True, 'parsed': parsed}
```

**Key Point:** The calling thread (usually the main/GUI thread) does **both** the send AND the receive. There's no separate receiver thread running.

### CallbackListener Status

```python
# In MVCConnectionService (connection_service.py:322)
self._callback_listener: Optional['CallbackListener'] = None  # Declared but NEVER instantiated
```

**Search results show:**
- `CallbackListener` class is defined (callback_listener.py)
- Variable placeholder exists in MVCConnectionService
- **BUT:** No code ever does `self._callback_listener = CallbackListener(...)`
- **AND:** No code ever calls `self._callback_listener.start()`

**Conclusion:** Currently there is NO race condition because the callback listener thread is not running.

---

## Future Problem: IF CallbackListener Gets Activated

### The Race Condition Scenario

If someone were to activate the CallbackListener like this:

```python
# In MVCConnectionService.connect() - NOT currently done
self._callback_listener = CallbackListener(self._command_socket)
self._callback_listener.start()  # Starts background thread
```

Then you'd have **TWO threads reading from the same socket:**

```
Thread 1 (Main/GUI):                Thread 2 (CallbackListener):
=====================               ============================
                                    [Running in background]
                                    command_socket.recv(128)  ← Waiting

User clicks "Get Image Size"
  ↓
_query_command() executes
  ↓
command_socket.sendall(cmd)  ─────→ [Microscope receives]
  ↓                                 [Microscope sends response]
command_socket.recv(128)
  ↓
RACE CONDITION!
Who gets the response?
Could be Thread 1 (correct)
Could be Thread 2 (WRONG - steals the response!)
```

### The Problem

When two threads call `recv()` on the same socket:

1. **Unpredictable**: Whichever thread's `recv()` call is active when data arrives gets it
2. **Non-deterministic**: The OS scheduler decides, not your code
3. **Data loss**: If CallbackListener gets a command response, the query times out
4. **Wrong dispatch**: If main thread gets an unsolicited message, it might interpret it incorrectly

### Example Failure Case

```
Time    Main Thread                     CallbackListener Thread       Socket Buffer
====    ===========                     =======================       =============
T0      [Idle]                          recv(128) [BLOCKING]          [Empty]
T1      Send IMAGE_SIZE_GET             [Still blocking...]
T2      recv(128) [BLOCKING]            [Still blocking...]
T3                                                                    128 bytes arrive
T4      [Blocked]                       ← GETS DATA! ✗                [Empty]
T5      [Still blocked...]              Dispatches as "unsolicited"
T6      [Timeout after 3s] ✗            Logs "no handler for 12327"
```

Result:
- Main thread times out with error
- CallbackListener logs "no handler for command 12327"
- User sees: "Failed to get image size: timeout"

---

## Evidence in Code

### 1. Socket Lock Exists But Unused

In `MVCConnectionService` (connection_service.py:323):

```python
self._socket_lock = threading.Lock()  # Coordinate send_command and callback listener
```

**Comment says it's for coordination, but:**

Search for `_socket_lock` usage:
```bash
$ grep -r "_socket_lock" src/py2flamingo/services/
connection_service.py:323:  self._socket_lock = threading.Lock()
```

**Only one line!** The lock is **declared but never used**.

### 2. Send/Receive Has No Locking

In `MicroscopeCommandService._query_command()` (microscope_command_service.py:92-96):

```python
# Send command - NO LOCK
command_socket.sendall(cmd_bytes)

# Read 128-byte response - NO LOCK
ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)
```

### 3. CallbackListener Would Also Have No Locking

In `CallbackListener._listen_loop()` (callback_listener.py:124-125):

```python
# Try to receive a message (128 bytes) - NO LOCK
data = self._receive_message(128)
```

Which calls (callback_listener.py:177):

```python
chunk = self.command_socket.recv(size - len(data))  # NO LOCK
```

---

## Why This Might Have Been Designed This Way

### Possible Intent: Non-Overlapping Usage

The developers may have intended:

1. **During query operations**: CallbackListener is paused/stopped
2. **During idle periods**: CallbackListener is active
3. **Coordination**: Some mechanism switches between modes

But this coordination **is not implemented**.

### Or: CallbackListener Is a Future Feature

More likely: CallbackListener was:
- Designed and implemented
- Never integrated
- Left in codebase for future use
- Documentation/comments reference it, but it's not active

---

## Solutions If CallbackListener Needs To Be Activated

### Option 1: Mutex Lock (Simple, but blocking)

```python
class MVCConnectionService:
    def __init__(self, ...):
        self._socket_lock = threading.Lock()  # Already exists!

    def send_command(self, cmd, timeout=5.0):
        with self._socket_lock:  # Acquire lock
            # Send command
            self._command_socket.sendall(cmd_bytes)
            # Receive response
            response = self._receive_full_response(...)
        return response

# In CallbackListener
def _listen_loop(self):
    while not self._stop_event.is_set():
        with self.socket_lock:  # Need to pass lock to CallbackListener
            data = self._receive_message(128)
        # Process data...
```

**Pros:**
- Simple to implement
- Thread-safe

**Cons:**
- CallbackListener can't receive unsolicited messages while command/response in progress
- If a stage movement takes 10 seconds, STAGE_MOTION_STOPPED won't be received until after
- Defeats the purpose of background listening

### Option 2: Pause CallbackListener During Commands

```python
def send_command(self, cmd, timeout=5.0):
    # Pause callback listener
    if self._callback_listener:
        self._callback_listener.pause()

    try:
        # Send and receive
        self._command_socket.sendall(cmd_bytes)
        response = self._receive_full_response(...)
        return response
    finally:
        # Resume callback listener
        if self._callback_listener:
            self._callback_listener.resume()
```

**Pros:**
- No race condition
- Clear separation

**Cons:**
- Complex state management
- Missed unsolicited messages during queries
- Need pause/resume mechanism in CallbackListener

### Option 3: Separate Socket for Unsolicited Messages (BEST)

**Architecture change:** Microscope sends unsolicited messages on a different socket.

```
Client                          Microscope
======                          ==========
Command Socket (port N)   <-->  Command handler
Live Socket (port N+1)    <-->  Image stream
Callback Socket (port N+2) <--  Unsolicited messages (NEW)
```

**Pros:**
- Complete separation
- No race conditions
- No coordination needed
- Both can receive simultaneously

**Cons:**
- Requires microscope firmware change
- Three sockets instead of two

### Option 4: Message Tagging (Requires Protocol Change)

Add a "solicited" flag to the protocol:

```
params[6] bit 30: 0 = Unsolicited, 1 = Response to query
```

Then a single receiver thread dispatches based on the flag.

**Cons:**
- Protocol change required
- Doesn't solve the socket recv() race

### Option 5: Current Approach - Don't Use CallbackListener (CURRENT)

Just don't activate the CallbackListener. Handle everything synchronously.

**For unsolicited messages like STAGE_MOTION_STOPPED:**
- Poll with periodic GET_STAGE_POSITION commands
- Or use a separate application-level protocol

**Pros:**
- No race condition
- Simple
- Works (current implementation)

**Cons:**
- Polling overhead
- Delayed notification of events

---

## Recommendation for Your Meeting

### For the C++ Server Developer

**Tell them:**

1. **Current implementation:**
   - Python client does synchronous request/response
   - NO background receiver thread
   - Each query blocks until response received

2. **Future consideration:**
   - If unsolicited messages (like STAGE_MOTION_STOPPED) are needed
   - Consider one of these approaches:
     - **A.** Separate socket for unsolicited messages (cleanest)
     - **B.** Message sequence numbers to match responses to requests
     - **C.** Explicit "solicited vs unsolicited" flag in protocol

3. **Current protocol works fine for:**
   - Request/response queries
   - No race conditions
   - Simple and deterministic

### Testing Recommendation

If you want to test the race condition to demonstrate it:

```python
# In MVCConnectionService.connect(), add:
from py2flamingo.services.callback_listener import CallbackListener

self._callback_listener = CallbackListener(self._command_socket)
self._callback_listener.start()

# Then try to get image size - you may see intermittent failures!
```

---

## Conclusion

**Current state:** No race condition (CallbackListener not active)

**Future risk:** Race condition WILL occur if CallbackListener is activated without proper coordination

**Your observation was correct:** This is a real design issue that needs to be addressed before the CallbackListener can be used.

**Best solution:** Separate socket for unsolicited messages (requires microscope firmware support)

**Practical solution:** Continue with current approach (synchronous request/response, no CallbackListener)

---

**Document Author:** Analysis based on code review of Flamingo_Control
**Date:** 2025-11-10
**Files Analyzed:**
- `src/py2flamingo/services/callback_listener.py`
- `src/py2flamingo/services/connection_service.py:318-323, 475-618`
- `src/py2flamingo/services/microscope_command_service.py:35-125, 157-247`
