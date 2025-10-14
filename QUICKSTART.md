# Quick Start - Testing Without Hardware

Follow these steps to test the minimal interface using the mock server (no microscope needed).

## Step 1: Start the Mock Server

Open a terminal and run:

```bash
# Activate the virtual environment
source .venv/bin/activate

# Start the mock server
python mock_server.py
```

You should see:
```
Mock Flamingo server started on 127.0.0.1:53717
Command port: 53717, Live port: 53718
Press Ctrl+C to stop
```

Leave this terminal running.

## Step 2: Create Test Metadata File

The GUI needs a metadata file. Create one for the mock server:

```bash
# This creates a metadata file pointing to localhost
cat > microscope_settings/FlamingoMetaData_test.txt << 'EOF'
<MetaData>
  <Experiment>
    Name = Test
  </Experiment>
</MetaData>
<Instrument>
  <Type>
    Microscope type = Mock
    Microscope name = Test
    Microscope address = 127.0.0.1 53717
  </Type>
</Instrument>
EOF
```

Or manually create `microscope_settings/FlamingoMetaData_test.txt` with the content above.

## Step 3: Launch the GUI

Open a **new terminal** (keep mock server running) and run:

```bash
# Activate virtual environment
source .venv/bin/activate

# Run the GUI
./run_minimal.sh
```

Or manually:
```bash
source .venv/bin/activate
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
cd src
python -m py2flamingo.minimal_gui
```

## Step 4: Connect to Mock Server

In the GUI:

1. **Load metadata**:
   - Click "Browse..." next to "Metadata File"
   - Select `microscope_settings/FlamingoMetaData_test.txt`
   - Click "Load"
   - IP should show `127.0.0.1` and Port `53717`

2. **Connect**:
   - Click "Connect" button
   - Status should turn green: "Connected"

## Step 5: Send a Workflow

1. **Select workflow**:
   - Click "Refresh" to scan workflows directory
   - Choose `Snapshot.txt` or `ZStack.txt` from dropdown
   - Preview appears in text box

2. **Send**:
   - Click "Send Workflow"
   - Check the mock server terminal - you should see:
     ```
     Received command: 12292
     Expecting workflow data: XXX bytes
     Received workflow (XXX bytes):
     ────────────────────────────────
     <Workflow Settings>
     ...
     ```

3. **Verify**:
   - Check for `received_workflow.txt` in the root directory
   - This is what the mock server received

## Step 6: Test Stop Command

- Click "Stop Workflow" button
- Mock server should log: `Workflow stop requested`

## Troubleshooting

### "Connection failed"

**Mock server not running?**
```bash
# Check if mock server is listening
netstat -an | grep 53717
# Should show: tcp  0.0.0.0:53717  LISTEN
```

**Port already in use?**
```bash
# Use a different port
python mock_server.py --port 54000

# Then in GUI, manually set port to 54000
```

### "No workflow files found"

```bash
# Verify workflows directory exists
ls workflows/
# Should show: Snapshot.txt, ZStack.txt, etc.

# If empty, check you're in the right directory
pwd
# Should end in: /Flamingo_Control
```

### Import errors

```bash
# Reinstall dependencies
source .venv/bin/activate
pip install -r requirements-minimal.txt
```

### GUI doesn't launch

**Missing display?** (e.g., SSH without X11)
```bash
# Set display
export DISPLAY=:0

# Or forward X11
ssh -X user@host
```

**Qt platform plugin error?**
```bash
# Install Qt dependencies (Ubuntu/Debian)
sudo apt-get install libxcb-xinerama0

# Or use offscreen platform (no GUI display)
export QT_QPA_PLATFORM=offscreen
```

## What to Check

✅ Mock server terminal shows received commands
✅ GUI log shows "Connected successfully"
✅ GUI log shows "Workflow sent successfully"
✅ File `received_workflow.txt` created with workflow content
✅ Stop button sends stop command

## Next Steps

Once this works locally:

1. **Test with real hardware**:
   - Update metadata file with real microscope IP
   - Connect to microscope network
   - Follow same steps

2. **Modify workflows**:
   - Edit workflow `.txt` files
   - Adjust positions, laser settings, etc.
   - Test with mock server first

3. **Add features**:
   - Position control
   - Status monitoring
   - Image display

## Cleanup

When done testing:

1. Stop GUI (close window or Ctrl+C)
2. Stop mock server (Ctrl+C in that terminal)
3. Deactivate virtual environment:
   ```bash
   deactivate
   ```

## Files Created During Testing

- `received_workflow.txt` - Last workflow received by mock server
- `*.log` - Log files (if configured)

You can delete these anytime.
