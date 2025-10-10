# Flamingo MVC Interface - Quick Start Guide

Get up and running with the Flamingo Microscope Control MVC interface in minutes!

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [First Run](#first-run)
4. [Basic Usage](#basic-usage)
5. [Testing Without Hardware](#testing-without-hardware)
6. [Common Tasks](#common-tasks)
7. [Next Steps](#next-steps)

## Prerequisites

Before you begin, ensure you have:

- **Python 3.8-3.11** installed
  ```bash
  python --version  # Should show 3.8.x - 3.11.x
  ```

- **Git** for cloning the repository (or download ZIP)

- **Network access** to microscope (or use mock server for testing)

## Installation

### Step 1: Clone Repository

```bash
# Navigate to your projects directory
cd ~/LSControl

# Repository is already at:
cd Flamingo_Control
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate it
source .venv/bin/activate  # On Linux/Mac
# OR
.venv\Scripts\activate     # On Windows
```

You should see `(.venv)` in your terminal prompt.

### Step 3: Install Dependencies

```bash
# Install required packages
pip install -r requirements.txt

# Install development dependencies (optional, for testing)
pip install -r requirements-dev.txt
```

### Step 4: Verify Installation

```bash
# Check the application is available
python -m py2flamingo --help
```

You should see the help message with available options.

**Installation complete!** ✓

## First Run

### Option A: With Mock Server (Recommended for First Time)

The mock server simulates a microscope without needing hardware.

```bash
# Terminal 1: Start mock server
python mock_server.py

# You should see:
# Mock Flamingo server started on 127.0.0.1:53717
# Command port: 53717, Live port: 53718

# Terminal 2: Start application
python -m py2flamingo

# Or specify mock server explicitly
python -m py2flamingo --ip 127.0.0.1 --port 53717
```

### Option B: With Real Microscope

If you have access to a Flamingo microscope:

```bash
# Find microscope IP in FlamingoMetaData.txt
cat microscope_settings/FlamingoMetaData.txt | grep "Microscope address"

# Start application with microscope IP
python -m py2flamingo --ip <microscope-ip> --port 53717
```

## Basic Usage

### 1. Connecting to Microscope

When the application window opens:

1. **Connection Tab** (should be visible by default)
2. **IP Address**: Enter microscope IP (default: 127.0.0.1)
3. **Port**: Enter command port (default: 53717)
4. **Click "Connect"** button

**Success:** Status shows green "Connected to [IP]:[PORT]"
**Failure:** Status shows red error message

### 2. Loading a Workflow

After connecting:

1. **Switch to Workflow Tab**
2. **Click "Browse"** button
3. **Navigate to** `workflows/` directory
4. **Select** a workflow file (e.g., `Snapshot.txt`)
5. **Click "Open"**

**Success:** File path appears in text box, message shows "Workflow loaded"

### 3. Starting a Workflow

With workflow loaded:

1. **Click "Start Workflow"** button
2. **Wait for completion** (status updates will appear)
3. **Click "Stop Workflow"** when done (or to cancel)

**Success:** Message shows "Workflow started" then "Workflow completed"

### 4. Disconnecting

When finished:

1. **Return to Connection Tab**
2. **Click "Disconnect"** button

**Success:** Status returns to gray "Disconnected"

## Testing Without Hardware

The mock server is perfect for learning and testing.

### Start Mock Server

```bash
# Default configuration (127.0.0.1:53717)
python mock_server.py

# Custom port
python mock_server.py --port 12345

# Custom IP and port
python mock_server.py --ip 0.0.0.0 --port 8080
```

### Use Sample Workflows

Several sample workflows are included:

```bash
ls workflows/
# Snapshot.txt - Single image capture
# ZStack.txt - Z-stack acquisition
```

Try them with the mock server to see how the interface works.

### Mock Server Features

The mock server:
- Accepts all commands without errors
- Logs received commands to console
- Simulates workflow execution
- Saves received workflows to `received_workflow.txt`

## Common Tasks

### Task 1: Quick Test Connection

```bash
# One command to test everything
python -m py2flamingo --ip 127.0.0.1 --port 53717
```

Then:
1. Click Connect
2. Verify green status
3. Click Disconnect

### Task 2: Send a Workflow

```bash
# Start with workflow pre-loaded
python -m py2flamingo --workflow workflows/Snapshot.txt
```

Then:
1. Click Connect
2. Click Start Workflow
3. Wait for completion
4. Click Disconnect

### Task 3: Debug Connection Issues

```bash
# Enable detailed logging
python -m py2flamingo --log-level DEBUG
```

This shows all network communication and internal operations.

### Task 4: Run Tests

```bash
# Check everything works
python -m pytest tests/ -v

# Just unit tests (fast)
python -m pytest tests/ --ignore=tests/integration -v

# With mock server (integration tests)
python mock_server.py &
python -m pytest tests/integration/ -v
```

### Task 5: Programmatic Control

Create a Python script:

```python
# my_script.py
from py2flamingo.core import TCPConnection, ProtocolEncoder
from py2flamingo.services import MVCConnectionService, MVCWorkflowService
from py2flamingo.models import ConnectionConfig

# Setup
tcp = TCPConnection()
encoder = ProtocolEncoder()
conn_service = MVCConnectionService(tcp, encoder)
workflow_service = MVCWorkflowService(conn_service)

# Connect
config = ConnectionConfig("127.0.0.1", 53717)
success, msg = conn_service.connect(config)
print(f"Connect: {msg}")

# Load and start workflow
workflow_service.load_workflow("workflows/Snapshot.txt")
success, msg = workflow_service.start_workflow()
print(f"Start: {msg}")

# Clean up
conn_service.disconnect()
```

Run it:
```bash
PYTHONPATH=src python my_script.py
```

## Troubleshooting

### Problem: "ModuleNotFoundError"

```bash
# Solution: Set PYTHONPATH
export PYTHONPATH=src
python -m py2flamingo
```

### Problem: "Connection refused"

**Check:**
1. Is mock server running? `ps aux | grep mock_server`
2. Is port correct? Default is 53717
3. Is firewall blocking? Try `127.0.0.1` first

**Solution:**
```bash
# Kill old server
pkill -f mock_server

# Start fresh
python mock_server.py
```

### Problem: "PyQt5 not found"

```bash
# Install PyQt5
pip install PyQt5
```

### Problem: Application won't start

```bash
# Check Python version
python --version  # Must be 3.8-3.11

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Problem: Workflow file not found

**Use absolute paths:**
```python
import os
workflow_path = os.path.abspath("workflows/Snapshot.txt")
```

Or **run from project root:**
```bash
cd /home/msnelson/LSControl/Flamingo_Control
python -m py2flamingo
```

## Next Steps

### Learn More

1. **Read Full Documentation**: [README_MVC.md](README_MVC.md)
2. **Explore Architecture**: [CLAUDE.md](CLAUDE.md)
3. **Study Examples**: Check `tests/` directory for usage patterns

### Customize Application

1. **Change Default IP**: Edit in `application.py`
2. **Add Custom Workflows**: Create .txt files in `workflows/`
3. **Extend Functionality**: Add new services/controllers

### Development

1. **Run Tests**: `python -m pytest tests/ -v`
2. **Check Coverage**: `pytest --cov=src/py2flamingo`
3. **Add Features**: Follow patterns in existing code

### Get Help

**Common Issues:**
- Check [README_MVC.md Troubleshooting](README_MVC.md#troubleshooting)
- Review logs with `--log-level DEBUG`
- Search existing tests for examples

**Architecture Questions:**
- Read [CLAUDE.md](CLAUDE.md) for detailed architecture
- Check inline docstrings: `help(MVCConnectionService)`

## Quick Reference Commands

```bash
# Start application (basic)
python -m py2flamingo

# Start with custom settings
python -m py2flamingo --ip 192.168.1.100 --port 53717

# Start with auto-loaded workflow
python -m py2flamingo --workflow workflows/Snapshot.txt

# Enable debug logging
python -m py2flamingo --log-level DEBUG

# Start mock server
python mock_server.py

# Run all tests
python -m pytest tests/ -v

# Run integration tests
python mock_server.py &
python -m pytest tests/integration/ -v

# Check test coverage
python -m pytest tests/ --cov=src/py2flamingo

# Get help
python -m py2flamingo --help
```

## Example Session

Here's a complete session from start to finish:

```bash
# 1. Navigate to project
cd /home/msnelson/LSControl/Flamingo_Control

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Start mock server (Terminal 1)
python mock_server.py
# Output: Mock Flamingo server started on 127.0.0.1:53717

# 4. Start application (Terminal 2)
python -m py2flamingo
# Output: Application window opens

# 5. In the GUI:
#    - Connection Tab: Click "Connect"
#    - Status: Green "Connected to 127.0.0.1:53717"
#    - Workflow Tab: Click "Browse", select "workflows/Snapshot.txt"
#    - Click "Start Workflow"
#    - Status: "Workflow started"
#    - Wait for completion
#    - Click "Disconnect"

# 6. Check mock server log (Terminal 1)
# You'll see received commands:
#    - Received command: 12292 (WORKFLOW_START)
#    - Received workflow (XXX bytes)

# 7. Clean up
# Close application window
# Press Ctrl+C in Terminal 1 to stop mock server
```

**Congratulations!** You've completed your first workflow with the MVC interface.

---

## Summary Checklist

- ✓ Python 3.8-3.11 installed
- ✓ Virtual environment created and activated
- ✓ Dependencies installed (`requirements.txt`)
- ✓ Application runs with `--help`
- ✓ Mock server starts successfully
- ✓ Application connects to mock server
- ✓ Workflow loads and executes
- ✓ Application disconnects cleanly

**You're ready to use the Flamingo MVC interface!**

For detailed information, see [README_MVC.md](README_MVC.md).

---

**Last Updated:** 2025-10-10
**Version:** 1.0
