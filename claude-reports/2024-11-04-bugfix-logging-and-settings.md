# Bug Fix: Logging and Settings Retrieval

**Date**: 2025-11-04
**Issue**: Missing method and insufficient logging
**Commit**: `3eec83e`
**Status**: ✅ Fixed

---

## Issues Identified

### 1. Missing Method Error
**Error Message**:
```
Error loading settings: 'ConnectionController' object has no attribute 'get_microscope_settings'
```

**Cause**:
- `ConnectionView` was calling `controller.get_microscope_settings()`
- Method didn't exist in `ConnectionController`
- No error showing in Python log (silently caught)

### 2. Insufficient Logging
**Issue**: No logging in Python console showing what's happening during:
- Connection attempts
- Settings retrieval
- Button clicks
- UI updates

---

## Fixes Applied

### 1. Added `get_microscope_settings()` to ConnectionController

**File**: `src/py2flamingo/controllers/connection_controller.py`
**Lines**: 446-508 (new method, 63 lines)

**Implementation**:
```python
def get_microscope_settings(self) -> Optional[Dict[str, Any]]:
    """Get current microscope settings from the connected microscope."""
    self._logger.info("Getting microscope settings...")

    # Check if connected
    if not self._service.is_connected():
        self._logger.warning("Cannot get settings: Not connected to microscope")
        return None

    try:
        # Check if service supports settings retrieval
        if hasattr(self._service, 'get_microscope_settings'):
            self._logger.debug("Calling service.get_microscope_settings()")
            pixel_size, settings_dict = self._service.get_microscope_settings()

            # Add pixel size to settings
            if pixel_size and settings_dict:
                if 'Camera' not in settings_dict:
                    settings_dict['Camera'] = {}
                settings_dict['Camera']['Pixel size (mm)'] = pixel_size

            self._logger.info(f"Retrieved settings with {len(settings_dict)} sections")
            return settings_dict
        else:
            # Service doesn't support getting settings yet
            # Return placeholder with connection info
            self._logger.warning("Service does not support get_microscope_settings()")
            return {
                'Connection': {
                    'Status': 'Connected',
                    'IP': self._model.status.ip,
                    'Port': self._model.status.port,
                    'Connected at': str(self._model.status.connected_at)
                },
                'Note': {
                    'Message': 'Full settings retrieval not yet implemented for this connection type'
                }
            }

    except Exception as e:
        self._logger.exception(f"Error getting microscope settings: {e}")
        return None
```

**Features**:
- ✅ Checks connection status before attempting retrieval
- ✅ Handles services that don't support settings retrieval
- ✅ Returns placeholder with connection info when not supported
- ✅ Comprehensive error handling
- ✅ Detailed logging at each step

### 2. Enhanced Logging in ConnectionView

**File**: `src/py2flamingo/views/connection_view.py`

#### Added Logger Instance
```python
def __init__(self, controller, config_manager=None):
    super().__init__()
    # ... existing code ...
    self._logger = logging.getLogger(__name__)  # NEW
    self.setup_ui()
```

#### Enhanced `_on_connect_clicked()`
**Before**:
```python
def _on_connect_clicked(self) -> None:
    ip = self.ip_input.text()
    port = self.port_input.value()
    success, message = self._controller.connect(ip, port)
    # ...
```

**After**:
```python
def _on_connect_clicked(self) -> None:
    ip = self.ip_input.text()
    port = self.port_input.value()

    self._logger.info(f"ConnectionView: Connect button clicked for {ip}:{port}")
    success, message = self._controller.connect(ip, port)
    self._logger.info(f"ConnectionView: Connection result - success={success}, message={message}")

    if success:
        self._logger.info("ConnectionView: Calling _load_and_display_settings()")
        self._load_and_display_settings()
```

#### Enhanced `_on_test_clicked()`
```python
def _on_test_clicked(self) -> None:
    ip = self.ip_input.text()
    port = self.port_input.value()

    self._logger.info(f"ConnectionView: Test connection button clicked for {ip}:{port}")
    success, message = self._controller.test_connection(ip, port)
    self._logger.info(f"ConnectionView: Test result - success={success}, message={message}")

    if success:
        self._logger.info("ConnectionView: Test successful, loading settings...")
        self._load_and_display_settings()
    else:
        self._logger.warning(f"ConnectionView: Test failed, not loading settings")
```

#### Enhanced `_load_and_display_settings()`
**Complete rewrite with comprehensive logging**:
```python
def _load_and_display_settings(self) -> None:
    """Load microscope settings and display them in the text area."""
    self._logger.info("ConnectionView: _load_and_display_settings() called")

    try:
        self._logger.debug("ConnectionView: Calling controller.get_microscope_settings()")
        settings = self._controller.get_microscope_settings()

        self._logger.info(f"ConnectionView: Received settings - type={type(settings)}, is_none={settings is None}")

        if settings:
            self._logger.info(f"ConnectionView: Settings has {len(settings)} top-level keys")
            formatted_text = self._format_settings(settings)
            self._logger.debug(f"ConnectionView: Formatted text length: {len(formatted_text)} chars")
            self.settings_display.setPlainText(formatted_text)
            # ... styling ...
            self._logger.info("ConnectionView: Settings display updated successfully")
        else:
            self._logger.warning("ConnectionView: No settings returned from controller")
            self.settings_display.setPlainText("No settings available.")

    except Exception as e:
        error_msg = f"Error loading settings: {str(e)}"
        self._logger.error(f"ConnectionView: {error_msg}", exc_info=True)
        self.settings_display.setPlainText(error_msg)
```

---

## Expected Log Output (After Fix)

### When Connecting:
```
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Connect button clicked for 192.168.1.1:53717
2025-11-04 17:30:00 - py2flamingo.controllers.connection_controller - INFO - Connected to 192.168.1.1:53717
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Connection result - success=True, message=Connected to 192.168.1.1:53717
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Calling _load_and_display_settings()
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: _load_and_display_settings() called
2025-11-04 17:30:00 - py2flamingo.views.connection_view - DEBUG - ConnectionView: Calling controller.get_microscope_settings()
2025-11-04 17:30:00 - py2flamingo.controllers.connection_controller - INFO - Getting microscope settings...
2025-11-04 17:30:00 - py2flamingo.controllers.connection_controller - WARNING - Service does not support get_microscope_settings()
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Received settings - type=<class 'dict'>, is_none=False
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Settings has 2 top-level keys
2025-11-04 17:30:00 - py2flamingo.views.connection_view - INFO - ConnectionView: Settings display updated successfully
```

### When Testing Connection:
```
2025-11-04 17:29:50 - py2flamingo.views.connection_view - INFO - ConnectionView: Test connection button clicked for 192.168.1.1:53717
2025-11-04 17:29:50 - py2flamingo.controllers.connection_controller - INFO - Testing connection to 192.168.1.1:53717
2025-11-04 17:29:50 - py2flamingo.controllers.connection_controller - INFO - Connection test successful: 192.168.1.1:53717
2025-11-04 17:29:50 - py2flamingo.views.connection_view - INFO - ConnectionView: Test result - success=True, message=Connection test successful! Server is reachable at 192.168.1.1:53717
2025-11-04 17:29:50 - py2flamingo.views.connection_view - INFO - ConnectionView: Test successful, loading settings...
2025-11-04 17:29:50 - py2flamingo.views.connection_view - INFO - ConnectionView: _load_and_display_settings() called
```

---

## Settings Display Behavior

### Current Behavior (After Fix)

When using **MVCConnectionService** (current):
- Service doesn't have `get_microscope_settings()` method yet
- Controller detects this and returns placeholder
- Display shows:
  ```
  ============================================================
  MICROSCOPE SETTINGS
  ============================================================

  [Connection]
  ------------------------------------------------------------
    Status: Connected
    IP: 192.168.1.1
    Port: 53717
    Connected at: 2025-11-04 17:29:59.123456

  [Note]
  ------------------------------------------------------------
    Message: Full settings retrieval not yet implemented for this connection type

  ============================================================
  ```

### Future Behavior (When Service Supports It)

When the service has `get_microscope_settings()`:
- Full microscope parameters retrieved
- Display shows all sections:
  - Type (optical parameters)
  - Stage limits
  - Illumination (lasers)
  - Camera settings
  - System status

---

## What Users Will See

### Before Fix:
- ❌ Error message in red text: "Error loading settings: 'ConnectionController' object has no attribute 'get_microscope_settings'"
- ❌ No logging in Python console
- ❌ Silent failures

### After Fix:
- ✅ Placeholder settings showing connection info
- ✅ Informative message about settings not yet implemented
- ✅ Comprehensive logging showing all steps
- ✅ Clear error messages if problems occur

---

## Testing Performed

1. **Test Connection Button**:
   - ✅ Click shows logging
   - ✅ Success triggers settings load
   - ✅ Failure shows warning in log

2. **Connect Button**:
   - ✅ Click shows logging
   - ✅ Success triggers settings load
   - ✅ Settings display shows placeholder

3. **Error Handling**:
   - ✅ Missing method no longer causes error
   - ✅ Placeholder shows instead of error
   - ✅ All errors logged with stack traces

---

## Next Steps

### To Enable Full Settings Retrieval:

1. **Implement in MVCConnectionService**:
   ```python
   # In src/py2flamingo/services/connection_service.py
   def get_microscope_settings(self) -> Tuple[float, Dict[str, Any]]:
       """Get comprehensive microscope settings."""
       # Implementation needed
       pass
   ```

2. **Or Use MicroscopeInitializationService**:
   ```python
   # In controller or application
   from py2flamingo.services import MicroscopeInitializationService

   init_service = MicroscopeInitializationService(...)
   init_data = init_service.initial_setup()

   # Convert to settings dict for display
   settings = {
       'Camera': {
           'Pixel size (mm)': init_data.pixel_size_mm,
           'Frame size': init_data.fov_parameters['frame_size'],
           'FOV (mm)': init_data.fov_parameters['FOV']
       },
       'Stage limits': init_data.stage_limits,
       # ... etc
   }
   ```

---

## Summary

**Problem**: Missing method causing silent error, no logging visibility

**Solution**:
- Added missing `get_microscope_settings()` method to ConnectionController
- Method handles both supported and unsupported services gracefully
- Added comprehensive logging throughout ConnectionView
- Users now see what's happening at each step

**Result**:
- ✅ No more errors in settings display
- ✅ Informative placeholder shown when settings not available
- ✅ Complete logging of all connection operations
- ✅ Easy debugging of any future issues
- ✅ Clear path forward for full implementation

**Commit**: `3eec83e` - Pushed to main branch
