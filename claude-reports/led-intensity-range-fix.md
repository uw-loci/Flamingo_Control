# LED Intensity Range Fix

**Date:** 2025-11-14
**Status:** ✅ RESOLVED
**Issue:** LED only worked from 75-100% intensity in UI

---

## Problem Description

The LED intensity control in the Flamingo Control application was only functional in the upper 25% of the slider range (75-100% UI). The LED would not light up at all from 0-74% on the UI slider.

## Root Cause

The LED intensity value calculation was incorrect. Initially implemented as:
```python
# INCORRECT - Standard 16-bit range
led_value = int(65535 * (intensity_percent / 100.0))
```

This mapped:
- UI 0% → LED value 0
- UI 50% → LED value 32767
- UI 100% → LED value 65535

The actual LED hardware requires a **double 16-bit range** (0-131070) for full intensity control.

## Solution

Changed LED intensity calculation to use the correct range:

```python
# CORRECT - Double 16-bit range for LED
led_value = int(131070 * (intensity_percent / 100.0))
```

**File Modified:** `src/py2flamingo/services/laser_led_service.py` (lines 269-274)

### New Mapping:
- **UI 0%** → LED value 0 (off)
- **UI 50%** → LED value 65535 (half brightness)
- **UI 100%** → LED value 131070 (full brightness)

## Testing Results

✅ **Confirmed working across full 0-100% range**
✅ User can now see sample with LED at lower intensity settings
✅ LED responds smoothly to slider adjustments throughout entire range

## Technical Notes

### Why 131070?
The LED intensity range is exactly double the standard 16-bit range (2 × 65535 = 131070). This suggests the LED controller may use:
- Two 16-bit channels
- Extended precision control
- Or a specific hardware implementation requiring this range

### Signed vs Unsigned
Initial attempts tried mapping UI 0-100% to server -100% to +100% range, which required signed-to-unsigned conversion. This proved unnecessary - the LED uses a simple unsigned range from 0 to 131070.

## Related Changes

This fix was part of a larger effort to fix laser/LED control issues. See:
- Main commit: `886a5a4` - "Temporary: revert laser index reversal and simplify LED range"
- Previous attempts: `d8aeb4e`, `4e2607c`

## Code Reference

**Location:** `/home/msnelson/LSControl/Flamingo_Control/src/py2flamingo/services/laser_led_service.py`

```python
def set_led_intensity(self, led_color: int, intensity_percent: float) -> bool:
    """Set LED intensity as percentage for specified color."""
    # ... validation code ...

    # LED RANGE FIX: Map UI range 0-100% to full LED range 0-131070
    # UI  0% → 0
    # UI 50% → 65535
    # UI 100% → 131070
    led_value = int(131070 * (intensity_percent / 100.0))

    self.logger.debug(f"LED value mapping: UI {intensity_percent:.1f}% → LED value {led_value}")

    result = self._send_command(
        LaserLEDCommandCode.LED_SET,
        "LED_SET",
        params=[0, 0, 0, led_color, led_value, 0, 0]
    )
```

## Verification

User confirmed: "Great, I can confirm the LED works across the full range, and can now see the sample."

---

**Report Generated:** 2025-11-14
**Author:** Claude Code
