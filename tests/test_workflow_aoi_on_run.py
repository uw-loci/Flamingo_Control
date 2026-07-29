"""Camera AOI is applied to the hardware before a workflow runs.

The server derives the tile field-of-view from the LIVE camera AOI
(CAMERA_IMAGE_SIZE_GET), not the transmitted AOI fields, so
`WorkflowController._apply_workflow_aoi` must crop the sensor to the workflow's
Camera Settings before sending. It must also be strictly best-effort: never
raise and never block the run when the camera service is missing or errors.
"""

import logging
from types import SimpleNamespace

from py2flamingo.controllers.workflow_controller import WorkflowController


class _FakeCameraService:
    def __init__(self, result=None, raises=False):
        self.calls = []
        self._result = result if result is not None else {"success": True}
        self._raises = raises

    def set_centered_aoi(self, width, height, **kwargs):
        self.calls.append((width, height))
        if self._raises:
            raise RuntimeError("boom")
        return self._result


def _controller(service):
    """A WorkflowController with just the attributes the helper touches."""
    ctrl = WorkflowController.__new__(WorkflowController)
    ctrl._logger = logging.getLogger("test_aoi")
    ctrl._camera_controller = (
        SimpleNamespace(camera_service=service) if service is not None else None
    )
    return ctrl


def test_aoi_is_set_from_camera_settings():
    svc = _FakeCameraService()
    _controller(svc)._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": 2048, "AOI height": 1024}}
    )
    assert svc.calls == [(2048, 1024)]


def test_no_aoi_fields_means_no_call():
    svc = _FakeCameraService()
    _controller(svc)._apply_workflow_aoi({"Camera Settings": {}})
    _controller(svc)._apply_workflow_aoi({})
    _controller(svc)._apply_workflow_aoi(None)
    assert svc.calls == []


def test_missing_camera_service_is_a_noop():
    # No camera_controller at all -> must not raise.
    _controller(None)._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": 2048, "AOI height": 1024}}
    )
    # camera_controller present but without a usable service.
    ctrl = _controller(SimpleNamespace())  # SimpleNamespace has no camera_service
    ctrl._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": 2048, "AOI height": 1024}}
    )


def test_service_error_is_swallowed():
    svc = _FakeCameraService(raises=True)
    # Must not propagate — a failed AOI set cannot block the workflow.
    _controller(svc)._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": 1024, "AOI height": 2048}}
    )
    assert svc.calls == [(1024, 2048)]


def test_unsuccessful_result_is_swallowed():
    svc = _FakeCameraService(result={"success": False, "error": "out of range"})
    _controller(svc)._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": 4096, "AOI height": 4096}}
    )
    assert svc.calls == [(4096, 4096)]


def test_non_integer_aoi_is_ignored():
    svc = _FakeCameraService()
    _controller(svc)._apply_workflow_aoi(
        {"Camera Settings": {"AOI width": "", "AOI height": None}}
    )
    assert svc.calls == []
