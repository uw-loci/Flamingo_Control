# Claude Report: Contrast Slider for ImagePanel

**Date:** 2026-01-28

## Summary

Added min/max contrast sliders to the ImagePanel component used by MIP Overview and LED 2D Overview dialogs.

## Features

- **Dual sliders**: Min (black point) and Max (white point) sliders
- **Auto-range**: Slider endpoints are image minimum and 95th percentile values
- **Real-time update**: Moving sliders instantly updates display
- **Slider protection**: Min cannot exceed max (and vice versa)

## Files Modified

| File | Changes |
|------|---------|
| `views/dialogs/led_2d_overview_result.py` | Added contrast slider UI and logic to ImagePanel class |

## Implementation Details

### UI Layout

Added between zoom label and Fit/1:1 buttons:
```
[100%] ... [Contrast:] [---min---] [0-100%] [---max---] ... [Fit] [1:1]
```

### Contrast Calculation

```python
# When image is loaded:
self._image_min = float(np.min(flat))
self._image_max_pct = float(np.percentile(flat, 99.5))  # Updated from 95%

# When converting to display:
display_min = self._image_min + (slider_min / 1000.0) * intensity_range
display_max = self._image_min + (slider_max / 1000.0) * intensity_range
img_clipped = np.clip(image, display_min, display_max)
img_8bit = rescale_to_255(img_clipped)
```

### Instance Variables Added

```python
self._contrast_min_slider = 0      # 0-1000 range for precision
self._contrast_max_slider = 1000
self._image_min = 0.0              # Actual image intensity min
self._image_max_pct = 255.0        # Actual image 99.5th percentile
```
