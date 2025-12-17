"""
Window Geometry Manager - Persistent window position and size storage.

This module provides the WindowGeometryManager class that handles saving and
restoring window geometry (position, size, state) and splitter positions
using JSON-based storage with Qt's geometry serialization.

The manager uses Qt's saveGeometry()/restoreGeometry() methods which handle
platform-specific quirks like maximized state, multi-monitor setups, and
window decorations.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from PyQt5.QtWidgets import QWidget, QMainWindow, QSplitter
from PyQt5.QtCore import QByteArray


logger = logging.getLogger(__name__)


class WindowGeometryManager:
    """Manages window geometry persistence using JSON storage.

    This class saves and restores window positions, sizes, and splitter
    configurations between application sessions. It uses Qt's native
    geometry serialization (QByteArray) converted to base64 for JSON storage.

    Example:
        manager = WindowGeometryManager()

        # In window's showEvent (first show):
        manager.restore_geometry("MainWindow", self)

        # In window's closeEvent:
        manager.save_geometry("MainWindow", self)

        # For splitters:
        manager.restore_splitter_state("MainWindow", "main_splitter", self.splitter)
        manager.save_splitter_state("MainWindow", "main_splitter", self.splitter)
    """

    def __init__(self, config_file: str = "window_geometry.json"):
        """Initialize the geometry manager.

        Args:
            config_file: Path to JSON file for storing geometry data.
                        Relative paths are resolved from the current working directory.
        """
        self.config_file = Path(config_file)
        self._data: Dict[str, Any] = {
            "version": "1.0",
            "windows": {}
        }
        self._load_from_json()

    def _load_from_json(self) -> None:
        """Load geometry data from JSON file.

        If file doesn't exist, starts with empty configuration.
        """
        if not self.config_file.exists():
            logger.debug(f"Geometry file not found: {self.config_file}. Starting fresh.")
            return

        try:
            with open(self.config_file, 'r') as f:
                self._data = json.load(f)
            logger.info(f"Loaded geometry for {len(self._data.get('windows', {}))} windows")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in geometry file, starting fresh: {e}")
            self._data = {"version": "1.0", "windows": {}}
        except Exception as e:
            logger.error(f"Error loading geometry file: {e}")
            self._data = {"version": "1.0", "windows": {}}

    def _save_to_json(self) -> None:
        """Save current geometry data to JSON file."""
        try:
            # Ensure parent directory exists
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_file, 'w') as f:
                json.dump(self._data, f, indent=2)
            logger.debug(f"Saved geometry for {len(self._data.get('windows', {}))} windows")
        except Exception as e:
            logger.error(f"Error saving geometry file: {e}")

    def _get_window_data(self, window_id: str) -> Dict[str, Any]:
        """Get or create window data entry.

        Args:
            window_id: Unique identifier for the window

        Returns:
            Dictionary containing window's geometry data
        """
        windows = self._data.setdefault("windows", {})
        if window_id not in windows:
            windows[window_id] = {
                "geometry": None,
                "state": None,
                "splitters": {}
            }
        return windows[window_id]

    def save_geometry(self, window_id: str, widget: QWidget) -> None:
        """Save a widget's geometry to storage.

        Uses Qt's saveGeometry() which captures position, size, and
        window state (maximized, etc.) in a platform-independent format.

        For QMainWindow widgets, also saves the window state (toolbar/dock positions).

        Args:
            window_id: Unique identifier for the window
            widget: The QWidget to save geometry from
        """
        try:
            window_data = self._get_window_data(window_id)

            # Save geometry (position, size, maximized state)
            geometry_bytes = widget.saveGeometry()
            window_data["geometry"] = base64.b64encode(geometry_bytes.data()).decode('ascii')

            # For QMainWindow, also save state (toolbar/dock positions)
            if isinstance(widget, QMainWindow):
                state_bytes = widget.saveState()
                window_data["state"] = base64.b64encode(state_bytes.data()).decode('ascii')

            logger.debug(f"Saved geometry for '{window_id}'")

        except Exception as e:
            logger.error(f"Error saving geometry for '{window_id}': {e}")

    def restore_geometry(self, window_id: str, widget: QWidget) -> bool:
        """Restore a widget's geometry from storage.

        Uses Qt's restoreGeometry() which handles platform-specific quirks
        like window decorations and multi-monitor setups.

        For QMainWindow widgets, also restores the window state.

        Args:
            window_id: Unique identifier for the window
            widget: The QWidget to restore geometry to

        Returns:
            True if geometry was restored, False if no saved data exists
        """
        try:
            windows = self._data.get("windows", {})
            if window_id not in windows:
                logger.debug(f"No saved geometry for '{window_id}'")
                return False

            window_data = windows[window_id]

            # Restore geometry
            geometry_b64 = window_data.get("geometry")
            if geometry_b64:
                geometry_bytes = QByteArray(base64.b64decode(geometry_b64))
                widget.restoreGeometry(geometry_bytes)
                logger.debug(f"Restored geometry for '{window_id}'")

            # For QMainWindow, also restore state
            if isinstance(widget, QMainWindow):
                state_b64 = window_data.get("state")
                if state_b64:
                    state_bytes = QByteArray(base64.b64decode(state_b64))
                    widget.restoreState(state_bytes)
                    logger.debug(f"Restored state for '{window_id}'")

            return True

        except Exception as e:
            logger.error(f"Error restoring geometry for '{window_id}': {e}")
            return False

    def save_splitter_state(self, window_id: str, splitter_id: str,
                            splitter: QSplitter) -> None:
        """Save a splitter's sizes to storage.

        Args:
            window_id: Unique identifier for the parent window
            splitter_id: Unique identifier for the splitter within the window
            splitter: The QSplitter to save state from
        """
        try:
            window_data = self._get_window_data(window_id)
            splitters = window_data.setdefault("splitters", {})

            # Save splitter sizes as list of integers
            sizes = splitter.sizes()
            splitters[splitter_id] = sizes

            logger.debug(f"Saved splitter '{splitter_id}' for '{window_id}': {sizes}")

        except Exception as e:
            logger.error(f"Error saving splitter state: {e}")

    def restore_splitter_state(self, window_id: str, splitter_id: str,
                               splitter: QSplitter) -> bool:
        """Restore a splitter's sizes from storage.

        Args:
            window_id: Unique identifier for the parent window
            splitter_id: Unique identifier for the splitter within the window
            splitter: The QSplitter to restore state to

        Returns:
            True if state was restored, False if no saved data exists
        """
        try:
            windows = self._data.get("windows", {})
            if window_id not in windows:
                return False

            window_data = windows[window_id]
            splitters = window_data.get("splitters", {})

            if splitter_id not in splitters:
                logger.debug(f"No saved state for splitter '{splitter_id}'")
                return False

            sizes = splitters[splitter_id]
            if sizes and len(sizes) == splitter.count():
                splitter.setSizes(sizes)
                logger.debug(f"Restored splitter '{splitter_id}': {sizes}")
                return True
            else:
                logger.warning(f"Splitter size mismatch for '{splitter_id}': "
                             f"saved {len(sizes) if sizes else 0}, "
                             f"current {splitter.count()}")
                return False

        except Exception as e:
            logger.error(f"Error restoring splitter state: {e}")
            return False

    def save_all(self) -> None:
        """Save all pending geometry data to disk.

        This should be called when the application is shutting down
        to ensure all geometry changes are persisted.
        """
        self._save_to_json()
        logger.info("Saved all window geometry data")

    def get_registered_windows(self) -> List[str]:
        """Get list of all windows with saved geometry.

        Returns:
            List of window IDs
        """
        return list(self._data.get("windows", {}).keys())

    def clear_window(self, window_id: str) -> None:
        """Clear saved geometry for a specific window.

        Args:
            window_id: Unique identifier for the window
        """
        windows = self._data.get("windows", {})
        if window_id in windows:
            del windows[window_id]
            logger.info(f"Cleared geometry for '{window_id}'")

    def clear_all(self) -> None:
        """Clear all saved geometry data."""
        self._data = {"version": "1.0", "windows": {}}
        logger.info("Cleared all window geometry data")


class GeometryPersistenceMixin:
    """Mixin class to add geometry persistence to widgets.

    Widgets can inherit from this mixin to automatically save/restore
    their geometry. The widget must call _setup_geometry_persistence()
    during initialization.

    Example:
        class MyWindow(QWidget, GeometryPersistenceMixin):
            def __init__(self, geometry_manager):
                super().__init__()
                self._setup_geometry_persistence(geometry_manager, "MyWindow")
    """

    _geometry_manager: Optional[WindowGeometryManager] = None
    _window_id: Optional[str] = None
    _geometry_restored: bool = False
    _splitters: Dict[str, QSplitter] = None

    def _setup_geometry_persistence(self, geometry_manager: WindowGeometryManager,
                                    window_id: str,
                                    splitters: Optional[Dict[str, QSplitter]] = None) -> None:
        """Set up geometry persistence for this widget.

        Args:
            geometry_manager: The WindowGeometryManager instance
            window_id: Unique identifier for this window
            splitters: Optional dict mapping splitter_id to QSplitter instances
        """
        self._geometry_manager = geometry_manager
        self._window_id = window_id
        self._geometry_restored = False
        self._splitters = splitters or {}

    def _restore_geometry_on_show(self) -> None:
        """Restore geometry if not already done. Call from showEvent."""
        if self._geometry_manager and self._window_id and not self._geometry_restored:
            self._geometry_manager.restore_geometry(self._window_id, self)

            # Restore splitter states
            for splitter_id, splitter in self._splitters.items():
                self._geometry_manager.restore_splitter_state(
                    self._window_id, splitter_id, splitter
                )

            self._geometry_restored = True

    def _save_geometry_on_close(self) -> None:
        """Save geometry. Call from closeEvent."""
        if self._geometry_manager and self._window_id:
            self._geometry_manager.save_geometry(self._window_id, self)

            # Save splitter states
            for splitter_id, splitter in self._splitters.items():
                self._geometry_manager.save_splitter_state(
                    self._window_id, splitter_id, splitter
                )

            # Persist to disk immediately
            self._geometry_manager.save_all()
