# TO DO? Create initial dialog to ask about which microscope to connect to. Create named files based on the microscope (settings, workflows)
# TODO Running from command line currently not supported but should be the goal
# TODO Feedback window indicating status
# Run 
# black .
# isort . --profile black
# TODO create chatGPT prompt that allows the creation of new functions and buttons.
# TODO TODO TODO Cancel button stopped working at some point during the restructuring. It does cancel but does not leave the program in a workable state.
######################################

# Keep backward compatibility imports during migration
try:
    from .application import Application
except Exception:  # PyQt may be unavailable in headless tests
    Application = None

try:
    from .napari import NapariFlamingoGui
except Exception:
    NapariFlamingoGui = None
try:
    from .core.legacy_adapter import (
        LegacyMicroscopeController,
        LegacyWorkflowController,
        LegacySampleController,
        LegacyPositionController,
    )
except ImportError:
    # Legacy adapter might not be present
    LegacyMicroscopeController = LegacyWorkflowController = LegacySampleController = LegacyPositionController = None

# Provide access to legacy GUI (for backward compatibility)
try:
    from .GUI import Py2FlamingoGUI
except ImportError:
    Py2FlamingoGUI = None

# Mark legacy imports as available
__all__ = [
    "Application",
    "NapariFlamingoGui",
    "Py2FlamingoGUI",
    "LegacyMicroscopeController",
    "LegacyWorkflowController",
    "LegacySampleController",
    "LegacyPositionController",
]

# Napari plugin entry point (if Napari is used)
try:
    from .napari_plugin import create_flamingo_widget
except ImportError:
    create_flamingo_widget = None
