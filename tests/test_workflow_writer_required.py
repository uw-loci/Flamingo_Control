"""The workflow writer refuses to invent the values that move the stage.

``dict_to_workflow_text`` used to fill a missing frame rate with 100 f/s, a
missing Z velocity with 0.4 mm/s and a missing Start Position Z with 10.0 mm.
The server reads the frame rate and the Z velocity for the sweep and the
positions for where the stage goes, so a default there sends the stage
somewhere nobody asked for. Every live caller (Workflow tab, tile collection,
pipeline workflow dialog) supplies them; a dict that does not now raises.
"""

import copy
import unittest
from types import SimpleNamespace

from py2flamingo.core.errors import WorkflowError
from py2flamingo.utils.workflow_keys import number, whole_number
from py2flamingo.utils.workflow_parser import dict_to_workflow_text
from py2flamingo.utils.workflow_serialization import (
    build_tile_collection_section_dict,
    build_tile_illumination_source,
)


def _section_dict(is_zstack=True):
    camera = {
        "exposure_us": 9002.0,
        "frame_rate": 40.213,
        "aoi_width": 2048,
        "aoi_height": 2048,
        "cam1_capture_percentage": 100,
        "cam1_capture_mode": 0,
        "cam2_capture_percentage": 100,
        "cam2_capture_mode": 0,
    }
    save = {
        "save_drive": "/media/deploy/n7",
        "save_directory": "run",
        "save_mip": False,
        "display_mip": False,
        "save_format": "Raw",
        "save_enabled": True,
        "live_view": False,
    }
    return build_tile_collection_section_dict(
        name="t",
        position=SimpleNamespace(x=1.0, y=2.0, z=10.0, r=0.0),
        camera=camera,
        illumination_source=build_tile_illumination_source(
            [], left_on=True, right_on=False
        ),
        multi_laser=False,
        save_settings=save,
        z_min=10.0,
        z_max=10.5,
        is_zstack=is_zstack,
        z_step_um=5.0,
        z_velocity_mm_s=0.2,
    )


class WriterRequiresStageValues(unittest.TestCase):
    def test_a_complete_dict_writes(self):
        for zstack in (True, False):
            text = dict_to_workflow_text(_section_dict(zstack))
            self.assertIn("Frame rate (f/s) = 40.2", text)
            self.assertIn("Z (mm) = 10.000000", text)

    def _without(self, section, key, blank=False):
        wf = copy.deepcopy(_section_dict())
        if blank:
            wf[section][key] = "  "
        else:
            del wf[section][key]
        return wf

    def test_missing_values_raise_instead_of_defaulting(self):
        for section, key in [
            ("Experiment Settings", "Frame rate (f/s)"),
            ("Stack Settings", "Z stage velocity (mm/s)"),
            ("Start Position", "X (mm)"),
            ("Start Position", "Y (mm)"),
            ("Start Position", "Z (mm)"),
            ("Start Position", "Angle (degrees)"),
            ("End Position", "Z (mm)"),
        ]:
            for blank in (False, True):
                with self.subTest(key=key, blank=blank):
                    with self.assertRaises(WorkflowError) as cm:
                        dict_to_workflow_text(self._without(section, key, blank))
                    self.assertIn(key, str(cm.exception))

    def test_a_missing_end_position_is_the_start(self):
        wf = _section_dict()
        del wf["End Position"]
        text = dict_to_workflow_text(wf)
        start, end = text.split("<End Position>")
        self.assertIn("Z (mm) = 10.000000", end)

    def test_position_text_from_a_parsed_file_writes(self):
        wf = _section_dict()
        wf["Start Position"] = {k: str(v) for k, v in wf["Start Position"].items()}
        self.assertIn("X (mm) = 1.000000", dict_to_workflow_text(wf))


class NumberHelpers(unittest.TestCase):
    def test_number(self):
        self.assertEqual(number("9,002"), 9002.0)
        self.assertEqual(number(" 2.5 "), 2.5)
        self.assertEqual(number(3), 3.0)
        self.assertIsNone(number(""))
        self.assertIsNone(number(None))
        self.assertEqual(number("abc", 7.0), 7.0)

    def test_whole_number_rounds(self):
        self.assertEqual(whole_number("2,048"), 2048)
        self.assertEqual(whole_number("2.6"), 3)
        self.assertEqual(whole_number("", 5), 5)
        self.assertEqual(whole_number("inf", 5), 5)
        self.assertEqual(whole_number("nan", 5), 5)


if __name__ == "__main__":
    unittest.main()
