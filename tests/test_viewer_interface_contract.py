# tests/test_viewer_interface_contract.py
import pytest

def test_contract_signature():
    from py2flamingo.views.viewer_interface import ViewerInterface
    # Create a minimal implementing class
    class DummyViewer(ViewerInterface):
        def __init__(self):
            self.calls = []
        def display_image(self, image, title: str = "", metadata: dict = None):
            self.calls.append((image, title, metadata))

    dv = DummyViewer()
    arr = [[0, 0], [0, 0]]
    dv.display_image(arr, title="T", metadata={"k": "v"})
    assert dv.calls and dv.calls[0][0] == arr

