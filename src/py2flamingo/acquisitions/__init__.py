"""Client-side acquisitions: scans the client drives itself, plane by plane.

These are not server workflows (a Workflow.txt the server runs on its own);
see ``py2flamingo.workflows`` and docs/glossary.md.

- ``led_2d_overview.LED2DOverviewAcquisition``: the LED 2D overview scan.
- ``volume_scan.VolumeScanAcquisition``: serpentine volume scan. Not reachable
  from the GUI.

Import each from its module; this package imports nothing, so importing it
loads no Qt.
"""
