"""Workflow laser power is a percent, and is checked as one.

The Workflow tab's spinbox is 0-100 % and the value goes to the server's
percent field ("Laser 3 3: 561 nm MLE = 37.50 1"). The field holding it was
named ``laser_power_mw`` and the workflow validator checked it against
milliwatt limits (0-100 "mW", a "safe" 50 "mW" warning), which happened to
share the numbers but not the meaning. It is ``laser_power_percent`` now,
checked against 0-100 %.
"""

import unittest
from types import SimpleNamespace

from py2flamingo.core.errors import ValidationError
from py2flamingo.models.data.workflow import IlluminationSettings, Workflow
from py2flamingo.utils.workflow_serialization import build_tile_illumination_source
from py2flamingo.workflows.workflow_validator import WorkflowValidator


def _illumination(percent):
    return IlluminationSettings(
        laser_channel="Laser 3 488 nm", laser_power_percent=percent, laser_enabled=True
    )


class LaserPowerPercent(unittest.TestCase):
    def test_the_percent_reaches_the_server_field(self):
        src = build_tile_illumination_source(
            [
                SimpleNamespace(
                    laser_channel="Laser 3 561 nm",
                    laser_power_percent=37.5,
                    laser_enabled=True,
                    led_enabled=False,
                )
            ],
            left_on=True,
            right_on=False,
        )
        self.assertEqual(src["Laser 3 3: 561 nm MLE"], "37.50 1")

    def test_the_model_refuses_a_power_outside_0_100_percent(self):
        _illumination(0.0)
        _illumination(100.0)
        for bad in (-1.0, 100.5):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                _illumination(bad)

    def test_the_validator_checks_percent_not_milliwatts(self):
        validator = WorkflowValidator()
        result = validator.validate_detailed(Workflow(illumination=_illumination(60.0)))
        messages = " ".join(result.errors + result.warnings)
        self.assertNotIn("mW", messages)
        self.assertNotIn("safe limit", messages)

        illum = _illumination(10.0)
        illum.laser_power_percent = 120.0  # past the model's own check
        result = validator.validate_detailed(Workflow(illumination=illum))
        self.assertTrue(any("0-100%" in e for e in result.errors), result.errors)


if __name__ == "__main__":
    unittest.main()
