"""The command services log under py2flamingo.*, named after their module.

MicroscopeCommandService used to name its logger after the class, so
CameraService, StageService and LaserLEDService logged outside the
py2flamingo tree and ``parse_log --logger camera_service`` missed them.
"""

import unittest
from unittest.mock import Mock


class TestServiceLoggerNames(unittest.TestCase):
    def test_each_service_logs_under_its_module(self):
        from py2flamingo.services.camera_service import CameraService
        from py2flamingo.services.laser_led_service import LaserLEDService
        from py2flamingo.services.stage_service import StageService

        services = {
            "py2flamingo.services.camera_service": CameraService(Mock()),
            "py2flamingo.services.stage_service": StageService(Mock()),
            "py2flamingo.services.laser_led_service": LaserLEDService(Mock(), Mock()),
        }
        for name, service in services.items():
            self.assertEqual(service.logger.name, name)


if __name__ == "__main__":
    unittest.main()
