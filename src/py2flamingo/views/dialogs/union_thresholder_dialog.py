"""
Union of Thresholders dialog.

Per-channel threshold sliders create a 3D boolean mask displayed as a napari
Labels layer.  The mask can then be used to generate variable-Z-depth tile
acquisition profiles at one or more rotation angles.
"""

import json
import logging
import os
from typing import Optional, List, Tuple

import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSlider, QCheckBox,
    QSpinBox, QDoubleSpinBox, QLineEdit, QPushButton, QMessageBox,
    QSizePolicy, QFileDialog,
)

from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)

# Mask Labels layer name in napari
_MASK_LAYER_NAME = "Threshold Mask"


class UnionThresholderDialog(PersistentDialog):
    """Dialog for threshold-based 3D mask generation and acquisition profiling."""

    def __init__(self, app, parent=None):
        super().__init__(parent=parent)
        self._app = app
        self._sample_view = app.sample_view
        self._voxel_storage = self._sample_view.voxel_storage
        self._config = self._sample_view._config
        self._invert_x = self._sample_view._invert_x

        # Debounce timer for threshold updates
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(100)
        self._update_timer.timeout.connect(self._recompute_mask)

        # Channel slider state: {ch_id: (checkbox, slider, value_label)}
        self._channel_controls = {}
        # Current mask (kept for statistics / profile generation)
        self._current_mask: Optional[np.ndarray] = None

        self.setWindowTitle("Union of Thresholders")
        self.setMinimumWidth(420)
        self._setup_ui()
        self._restore_dialog_state()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Channel thresholds ---
        thresh_group = QGroupBox("Channel Thresholds")
        thresh_layout = QVBoxLayout()
        thresh_layout.setSpacing(4)
        self._setup_channel_sliders(thresh_layout)
        thresh_group.setLayout(thresh_layout)
        layout.addWidget(thresh_group)

        # --- Presets ---
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout()
        save_preset_btn = QPushButton("Save Preset...")
        save_preset_btn.clicked.connect(self._on_save_preset)
        preset_layout.addWidget(save_preset_btn)
        load_preset_btn = QPushButton("Load Preset...")
        load_preset_btn.clicked.connect(self._on_load_preset)
        preset_layout.addWidget(load_preset_btn)
        preset_group.setLayout(preset_layout)
        layout.addWidget(preset_group)

        # --- Mask display ---
        display_group = QGroupBox("Mask Display")
        display_layout = QHBoxLayout()

        display_layout.addWidget(QLabel("Opacity:"))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(50)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        display_layout.addWidget(self._opacity_slider)
        self._opacity_label = QLabel("0.50")
        self._opacity_label.setFixedWidth(36)
        display_layout.addWidget(self._opacity_label)

        self._show_mask_cb = QCheckBox("Show mask")
        self._show_mask_cb.setChecked(True)
        self._show_mask_cb.toggled.connect(self._on_show_mask_toggled)
        display_layout.addWidget(self._show_mask_cb)

        display_group.setLayout(display_layout)
        layout.addWidget(display_group)

        # --- Mask statistics ---
        stats_group = QGroupBox("Mask Statistics")
        stats_layout = QVBoxLayout()
        self._stats_label = QLabel("No mask computed")
        self._stats_label.setWordWrap(True)
        stats_layout.addWidget(self._stats_label)
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # --- Acquisition profile ---
        profile_group = QGroupBox("Acquisition Profile")
        profile_layout = QVBoxLayout()
        profile_layout.setSpacing(4)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("FOV buffer:"))
        self._buffer_spin = QDoubleSpinBox()
        self._buffer_spin.setRange(0.0, 2.0)
        self._buffer_spin.setSingleStep(0.05)
        self._buffer_spin.setValue(0.25)
        self._buffer_spin.setToolTip("Fraction of FOV to add as buffer around mask")
        row1.addWidget(self._buffer_spin)
        row1.addStretch()
        profile_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Angles:"))
        self._angles_edit = QLineEdit("0, 90")
        self._angles_edit.setToolTip("Comma-separated rotation angles in degrees")
        row2.addWidget(self._angles_edit)
        profile_layout.addLayout(row2)

        self._generate_btn = QPushButton("Generate Profile")
        self._generate_btn.setEnabled(False)
        self._generate_btn.clicked.connect(self._on_generate_profile)
        profile_layout.addWidget(self._generate_btn)

        profile_group.setLayout(profile_layout)
        layout.addWidget(profile_group)

        # --- Close ---
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _setup_channel_sliders(self, parent_layout):
        """Create a threshold slider row for each channel that has data."""
        channels_config = self._config.get('channels', [])

        for ch_cfg in channels_config:
            ch_id = ch_cfg['id']
            ch_name = ch_cfg.get('name', f'Channel {ch_id}')

            has_data = self._voxel_storage.has_data(ch_id)

            row = QHBoxLayout()
            cb = QCheckBox(ch_name)
            cb.setChecked(has_data)
            cb.setEnabled(has_data)
            row.addWidget(cb)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 65535)
            slider.setValue(0)
            slider.setEnabled(has_data)
            row.addWidget(slider)

            val_label = QLabel("0")
            val_label.setFixedWidth(50)
            val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(val_label)

            if has_data:
                # Set slider max to observed max intensity in display volume
                try:
                    vol = self._voxel_storage.get_display_volume(ch_id)
                    max_val = int(vol.max()) if vol.size > 0 else 65535
                    slider.setRange(0, max(1, max_val))
                except Exception:
                    pass

            # Connect signals
            slider.valueChanged.connect(lambda v, lbl=val_label: lbl.setText(str(v)))
            slider.valueChanged.connect(self._schedule_update)
            cb.toggled.connect(self._schedule_update)

            self._channel_controls[ch_id] = (cb, slider, val_label)
            parent_layout.addLayout(row)

        if not self._channel_controls:
            parent_layout.addWidget(QLabel("No channels configured"))

    # ------------------------------------------------------------------
    # Threshold / mask computation
    # ------------------------------------------------------------------

    def _schedule_update(self, *_args):
        """Debounce threshold changes."""
        self._update_timer.start()

    def _recompute_mask(self):
        """Compute union of per-channel thresholds and update napari."""
        combined: Optional[np.ndarray] = None

        for ch_id, (cb, slider, _) in self._channel_controls.items():
            if not cb.isChecked() or not cb.isEnabled():
                continue
            threshold = slider.value()
            if threshold <= 0:
                continue

            try:
                vol = self._voxel_storage.get_display_volume(ch_id)
            except Exception as e:
                logger.warning(f"Could not get display volume for ch {ch_id}: {e}")
                continue

            ch_mask = vol >= threshold

            if combined is None:
                combined = ch_mask
            else:
                # Ensure shapes match (all display volumes should be same size)
                if combined.shape == ch_mask.shape:
                    combined = combined | ch_mask
                else:
                    logger.warning(
                        f"Shape mismatch ch {ch_id}: {ch_mask.shape} vs {combined.shape}"
                    )

        self._current_mask = combined
        self._update_statistics(combined)
        self._generate_btn.setEnabled(bool(combined is not None and combined.any()))

        if self._show_mask_cb.isChecked():
            self._update_napari_mask(combined)
        else:
            self._remove_napari_mask()

    def _update_statistics(self, mask: Optional[np.ndarray]):
        """Update the statistics label from the current mask."""
        if mask is None or not mask.any():
            self._stats_label.setText("No voxels above threshold")
            return

        count = int(mask.sum())

        # Volume in mm³
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])
        if isinstance(voxel_size_um, list):
            vz, vy, vx = voxel_size_um[0], voxel_size_um[1], voxel_size_um[2]
        else:
            vz = vy = vx = float(voxel_size_um)
        voxel_vol_mm3 = (vz / 1000.0) * (vy / 1000.0) * (vx / 1000.0)
        volume_mm3 = count * voxel_vol_mm3

        # Bounding box in voxels
        nz_indices = np.where(mask.any(axis=(1, 2)))[0]
        ny_indices = np.where(mask.any(axis=(0, 2)))[0]
        nx_indices = np.where(mask.any(axis=(0, 1)))[0]

        # Convert bbox to stage mm
        stage_config = self._config.get('stage_control', {})
        x_range = stage_config.get('x_range_mm', [1.0, 12.31])
        y_range = stage_config.get('y_range_mm', [0.0, 14.0])
        z_range = stage_config.get('z_range_mm', [12.5, 26.0])
        voxel_size_mm = vx / 1000.0

        z_lo = z_range[0] + nz_indices[0] * voxel_size_mm
        z_hi = z_range[0] + nz_indices[-1] * voxel_size_mm
        y_lo = y_range[1] - ny_indices[-1] * voxel_size_mm
        y_hi = y_range[1] - ny_indices[0] * voxel_size_mm
        if self._invert_x:
            x_lo = x_range[1] - nx_indices[-1] * voxel_size_mm
            x_hi = x_range[1] - nx_indices[0] * voxel_size_mm
        else:
            x_lo = x_range[0] + nx_indices[0] * voxel_size_mm
            x_hi = x_range[0] + nx_indices[-1] * voxel_size_mm

        self._stats_label.setText(
            f"Voxels: {count:,}  |  Volume: {volume_mm3:.2f} mm\u00b3\n"
            f"X [{x_lo:.2f}, {x_hi:.2f}]  "
            f"Y [{y_lo:.2f}, {y_hi:.2f}]  "
            f"Z [{z_lo:.2f}, {z_hi:.2f}] mm"
        )

    # ------------------------------------------------------------------
    # Napari mask layer management
    # ------------------------------------------------------------------

    def _update_napari_mask(self, mask: Optional[np.ndarray]):
        """Add or update the Labels layer in napari."""
        viewer = self._sample_view.viewer
        if viewer is None:
            return

        if mask is None or not mask.any():
            self._remove_napari_mask()
            return

        labels = mask.astype(np.int32)
        opacity = self._opacity_slider.value() / 100.0

        # Check if layer already exists
        existing = self._find_mask_layer()
        if existing is not None:
            existing.data = labels
            existing.opacity = opacity
        else:
            viewer.add_labels(labels, name=_MASK_LAYER_NAME, opacity=opacity)

    def _remove_napari_mask(self):
        """Remove the mask Labels layer if it exists."""
        layer = self._find_mask_layer()
        if layer is not None:
            try:
                self._sample_view.viewer.layers.remove(layer)
            except Exception:
                pass

    def _find_mask_layer(self):
        """Find our Labels layer in the napari viewer."""
        viewer = self._sample_view.viewer
        if viewer is None:
            return None
        for layer in viewer.layers:
            if layer.name == _MASK_LAYER_NAME:
                return layer
        return None

    def _on_opacity_changed(self, value):
        self._opacity_label.setText(f"{value / 100:.2f}")
        layer = self._find_mask_layer()
        if layer is not None:
            layer.opacity = value / 100.0

    def _on_show_mask_toggled(self, checked):
        if checked and self._current_mask is not None:
            self._update_napari_mask(self._current_mask)
        else:
            self._remove_napari_mask()

    # ------------------------------------------------------------------
    # Acquisition profile generation (Part B)
    # ------------------------------------------------------------------

    def _get_actual_fov(self) -> Optional[float]:
        """Get field of view from camera service."""
        try:
            if (not self._app or not hasattr(self._app, 'camera_service')
                    or not self._app.camera_service):
                return None

            pixel_size_mm = self._app.camera_service.get_pixel_field_of_view()
            width, height = self._app.camera_service.get_image_size()
            frame_size = min(width, height)

            if frame_size <= 0 or pixel_size_mm <= 0:
                return None

            fov = pixel_size_mm * frame_size
            if fov < 0.01 or fov > 50:
                return None
            return fov
        except Exception:
            return None

    def _get_tip_position(self) -> Optional[Tuple[float, float]]:
        """Get tip of sample mount from position presets."""
        try:
            from py2flamingo.services.position_preset_service import PositionPresetService
            preset_service = PositionPresetService()
            preset = preset_service.get_preset("Tip of sample mount")
            if preset is not None:
                return (preset.x, preset.z)
            return None
        except Exception as e:
            logger.error(f"Error loading tip position: {e}")
            return None

    def _parse_angles(self) -> List[float]:
        """Parse the angle list from the text field."""
        text = self._angles_edit.text().strip()
        if not text:
            return [0.0]
        angles = []
        for part in text.split(','):
            part = part.strip()
            if part:
                try:
                    angles.append(float(part))
                except ValueError:
                    pass
        return angles if angles else [0.0]

    def _make_voxel_to_stage_fn(self):
        """Create a voxel→stage coordinate conversion function.

        Uses the same convention as chamber_visualization_manager:
          stage_z = z_range[0] + voxel_z * voxel_size_mm
          stage_y = y_range[1] - voxel_y * voxel_size_mm   (Y inverted)
          stage_x = x_range[1] - voxel_x * voxel_size_mm   if invert_x
                    x_range[0] + voxel_x * voxel_size_mm   otherwise
        """
        stage_config = self._config.get('stage_control', {})
        x_range = stage_config.get('x_range_mm', [1.0, 12.31])
        y_range = stage_config.get('y_range_mm', [0.0, 14.0])
        z_range = stage_config.get('z_range_mm', [12.5, 26.0])

        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])
        if isinstance(voxel_size_um, list):
            vs_mm = voxel_size_um[0] / 1000.0
        else:
            vs_mm = float(voxel_size_um) / 1000.0

        invert_x = self._invert_x

        def voxel_to_stage(z_voxel: int, y_voxel: int, x_voxel: int
                           ) -> Tuple[float, float, float]:
            stage_z = z_range[0] + z_voxel * vs_mm
            stage_y = y_range[1] - y_voxel * vs_mm
            if invert_x:
                stage_x = x_range[1] - x_voxel * vs_mm
            else:
                stage_x = x_range[0] + x_voxel * vs_mm
            return (stage_x, stage_y, stage_z)

        return voxel_to_stage, vs_mm

    def _on_generate_profile(self):
        """Generate tile acquisition profile from current mask."""
        if self._current_mask is None or not self._current_mask.any():
            QMessageBox.information(self, "No Mask", "No voxels above threshold.")
            return

        # FOV
        fov_mm = self._get_actual_fov()
        if fov_mm is None:
            QMessageBox.warning(
                self, "FOV Unavailable",
                "Could not determine field of view from camera.\n"
                "Make sure the microscope is connected."
            )
            return

        # Angles & tip
        angles = self._parse_angles()
        has_nonzero_angle = any(abs(a) > 0.01 for a in angles)
        tip_pos = self._get_tip_position() if has_nonzero_angle else None

        if has_nonzero_angle and tip_pos is None:
            QMessageBox.warning(
                self, "Tip Not Calibrated",
                "Multi-angle profiles require the 'Tip of sample mount' "
                "position preset.\nPlease calibrate the tip first, or use "
                "a single angle of 0."
            )
            return

        buffer_fraction = self._buffer_spin.value()

        voxel_to_stage_fn, voxel_size_mm = self._make_voxel_to_stage_fn()

        # Generate profiles
        from py2flamingo.utils.acquisition_profile_generator import generate_tile_profile

        try:
            profiles = generate_tile_profile(
                mask=self._current_mask,
                voxel_to_stage_fn=voxel_to_stage_fn,
                fov_mm=fov_mm,
                voxel_size_mm=voxel_size_mm,
                buffer_fraction=buffer_fraction,
                rotation_angles=angles,
                tip_position=tip_pos,
            )
        except Exception as e:
            logger.error(f"Profile generation failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Profile generation failed:\n{e}")
            return

        if not profiles:
            QMessageBox.information(
                self, "No Tiles",
                "No tiles were generated. The mask may be too small or the "
                "FOV too large."
            )
            return

        logger.info(f"Generated {len(profiles)} tile profiles")

        # Convert to TileResults and open TileCollectionDialog
        self._open_tile_collection(profiles, angles)

    def _open_tile_collection(self, profiles, angles: List[float]):
        """Convert TileProfiles to TileResults and open TileCollectionDialog."""
        from py2flamingo.models.data.overview_results import TileResult
        from py2flamingo.views.dialogs.tile_collection_dialog import TileCollectionDialog

        # Group profiles by angle
        by_angle = {}
        for p in profiles:
            by_angle.setdefault(p.rotation_angle, []).append(p)

        def _to_tile_results(tile_profiles) -> List[TileResult]:
            results = []
            for tp in tile_profiles:
                results.append(TileResult(
                    x=tp.x,
                    y=tp.y,
                    z=tp.z_center,
                    tile_x_idx=tp.tile_x_idx,
                    tile_y_idx=tp.tile_y_idx,
                    images={},
                    rotation_angle=tp.rotation_angle,
                    z_stack_min=tp.z_min,
                    z_stack_max=tp.z_max,
                ))
            return results

        # Process in pairs: angles[0] → left, angles[1] → right, etc.
        angle_list = sorted(by_angle.keys())

        for i in range(0, len(angle_list), 2):
            left_angle = angle_list[i]
            right_angle = angle_list[i + 1] if i + 1 < len(angle_list) else None

            left_tiles = _to_tile_results(by_angle[left_angle])
            right_tiles = _to_tile_results(by_angle[right_angle]) if right_angle is not None else []

            dialog = TileCollectionDialog(
                left_tiles=left_tiles,
                right_tiles=right_tiles,
                left_rotation=left_angle,
                right_rotation=right_angle if right_angle is not None else 0.0,
                app=self._app,
                parent=None,
            )
            dialog.show()

            # Keep reference to prevent garbage collection
            if not hasattr(self, '_tile_dialogs'):
                self._tile_dialogs = []
            self._tile_dialogs.append(dialog)

        logger.info(
            f"Opened TileCollectionDialog for {len(angle_list)} angle(s) "
            f"with {len(profiles)} total tiles"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Save state, remove mask layer, and clean up on dialog close."""
        self._save_dialog_state()
        self._update_timer.stop()
        self._remove_napari_mask()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Dialog state persistence
    # ------------------------------------------------------------------

    def _get_geometry_manager(self):
        """Get WindowGeometryManager from application."""
        if self._app and hasattr(self._app, 'geometry_manager'):
            return self._app.geometry_manager
        return None

    def _get_config_service(self):
        """Get ConfigurationService from application."""
        if self._app and hasattr(self._app, 'config_service'):
            return self._app.config_service
        return None

    def _save_dialog_state(self) -> None:
        """Save all dialog settings for persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        state = {
            'channels': {
                str(ch_id): {
                    'enabled': cb.isChecked(),
                    'threshold': slider.value(),
                }
                for ch_id, (cb, slider, _) in self._channel_controls.items()
            },
            'opacity': self._opacity_slider.value(),
            'show_mask': self._show_mask_cb.isChecked(),
            'buffer_fraction': self._buffer_spin.value(),
            'angles': self._angles_edit.text(),
        }

        try:
            gm.save_dialog_state("UnionThresholderDialog", state)
            gm.save_all()
            logger.debug("Saved UnionThresholderDialog state")
        except Exception as e:
            logger.warning(f"Failed to save dialog state: {e}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog settings from persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        try:
            state = gm.restore_dialog_state("UnionThresholderDialog")
        except Exception as e:
            logger.warning(f"Failed to restore dialog state: {e}")
            state = None

        if not state:
            return

        self._apply_preset_dict(state)

    # ------------------------------------------------------------------
    # Preset save / load
    # ------------------------------------------------------------------

    def _build_preset_dict(self) -> dict:
        """Build a preset dictionary from current dialog state."""
        return {
            'version': 1,
            'channels': {
                str(ch_id): {
                    'enabled': cb.isChecked(),
                    'threshold': slider.value(),
                }
                for ch_id, (cb, slider, _) in self._channel_controls.items()
            },
            'display': {
                'opacity': self._opacity_slider.value() / 100.0,
                'show_mask': self._show_mask_cb.isChecked(),
            },
            'profile': {
                'buffer_fraction': self._buffer_spin.value(),
                'angles': self._angles_edit.text(),
            },
        }

    def _apply_preset_dict(self, state: dict) -> None:
        """Apply a preset/state dictionary to the dialog controls.

        Blocks signals during restore to avoid N intermediate recomputes,
        then triggers one recompute at the end.
        """
        # --- Channel controls ---
        channels = state.get('channels', {})
        for ch_id, (cb, slider, val_label) in self._channel_controls.items():
            ch_state = channels.get(str(ch_id))
            if ch_state is None:
                continue
            if not cb.isEnabled():
                continue

            cb.blockSignals(True)
            slider.blockSignals(True)
            try:
                cb.setChecked(ch_state.get('enabled', cb.isChecked()))
                threshold = ch_state.get('threshold', slider.value())
                threshold = max(slider.minimum(), min(slider.maximum(), threshold))
                slider.setValue(threshold)
                val_label.setText(str(slider.value()))
            finally:
                cb.blockSignals(False)
                slider.blockSignals(False)

        # --- Display settings (preset JSON nests under 'display', dialog state is flat) ---
        display = state.get('display', {})
        opacity_val = display.get('opacity')
        if opacity_val is not None:
            # Preset stores 0.0-1.0, convert to 0-100
            self._opacity_slider.blockSignals(True)
            self._opacity_slider.setValue(int(round(opacity_val * 100)))
            self._opacity_label.setText(f"{opacity_val:.2f}")
            self._opacity_slider.blockSignals(False)
        elif 'opacity' in state:
            # Dialog state stores raw slider value (0-100)
            self._opacity_slider.blockSignals(True)
            self._opacity_slider.setValue(state['opacity'])
            self._opacity_label.setText(f"{state['opacity'] / 100:.2f}")
            self._opacity_slider.blockSignals(False)

        show_mask = display.get('show_mask')
        if show_mask is None:
            show_mask = state.get('show_mask')
        if show_mask is not None:
            self._show_mask_cb.blockSignals(True)
            self._show_mask_cb.setChecked(show_mask)
            self._show_mask_cb.blockSignals(False)

        # --- Profile settings (preset nests under 'profile', dialog state is flat) ---
        profile = state.get('profile', {})
        buffer_val = profile.get('buffer_fraction')
        if buffer_val is None:
            buffer_val = state.get('buffer_fraction')
        if buffer_val is not None:
            self._buffer_spin.blockSignals(True)
            self._buffer_spin.setValue(float(buffer_val))
            self._buffer_spin.blockSignals(False)

        angles_val = profile.get('angles')
        if angles_val is None:
            angles_val = state.get('angles')
        if angles_val is not None:
            self._angles_edit.blockSignals(True)
            self._angles_edit.setText(str(angles_val))
            self._angles_edit.blockSignals(False)

        # One recompute at the end
        self._recompute_mask()

    def _on_save_preset(self):
        """Save current threshold settings to a JSON preset file."""
        start_dir = ''
        cs = self._get_config_service()
        if cs:
            start_dir = cs.get_thresholder_preset_path() or ''

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Threshold Preset", start_dir,
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return

        preset = self._build_preset_dict()
        try:
            with open(path, 'w') as f:
                json.dump(preset, f, indent=2)
            logger.info(f"Saved threshold preset to {path}")
        except Exception as e:
            logger.error(f"Failed to save preset: {e}")
            QMessageBox.critical(self, "Save Error", f"Failed to save preset:\n{e}")
            return

        if cs:
            cs.set_thresholder_preset_path(os.path.dirname(path))

    def _on_load_preset(self):
        """Load threshold settings from a JSON preset file."""
        start_dir = ''
        cs = self._get_config_service()
        if cs:
            start_dir = cs.get_thresholder_preset_path() or ''

        path, _ = QFileDialog.getOpenFileName(
            self, "Load Threshold Preset", start_dir,
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return

        try:
            with open(path, 'r') as f:
                preset = json.load(f)
            logger.info(f"Loaded threshold preset from {path}")
        except Exception as e:
            logger.error(f"Failed to load preset: {e}")
            QMessageBox.critical(self, "Load Error", f"Failed to load preset:\n{e}")
            return

        self._apply_preset_dict(preset)

        if cs:
            cs.set_thresholder_preset_path(os.path.dirname(path))
