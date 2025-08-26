# tests/test_views_napari_adapter.py
import types
import sys
import numpy as np
import pytest

def _install_fake_napari(monkeypatch):
    """Install a minimal fake napari module into sys.modules."""
    class _Layer:
        def __init__(self, data, name, metadata):
            self.data = data
            self.name = name
            self.metadata = metadata

    class _Layers(dict):
        def __contains__(self, name):
            return dict.__contains__(self, name)

        def __getitem__(self, name):
            return dict.__getitem__(self, name)

        def add_or_replace(self, layer):
            self[layer.name] = layer

    class _Viewer:
        def __init__(self):
            self.layers = _Layers()
        def add_image(self, data, name="", metadata=None):
            self.layers.add_or_replace(_Layer(data, name or "Flamingo Live", metadata or {}))

    fake = types.SimpleNamespace(Viewer=_Viewer, run=lambda: None)
    monkeypatch.setitem(sys.modules, "napari", fake)
    return fake

def test_napari_viewer_add_and_update(monkeypatch):
    fake_napari = _install_fake_napari(monkeypatch)

    # Import after patching so py2flamingo.views.napari_view sees our fake napari
    from py2flamingo.views.napari_view import NapariViewer

    v = fake_napari.Viewer()
    adapter = NapariViewer(v)

    img1 = np.zeros((4, 4), dtype=np.uint8)
    adapter.display_image(img1, title="Live", metadata={"a": 1})
    assert "Live" in v.layers
    assert v.layers["Live"].data is img1
    assert v.layers["Live"].metadata == {"a": 1}

    # Update same layer
    img2 = np.ones((4, 4), dtype=np.uint8)
    adapter.display_image(img2, title="Live", metadata={"a": 2})

    assert "Live" in v.layers
    assert v.layers["Live"].data is img2  # data updated
    # name remains "Live"; metadata may or may not be updated by adapter (we don't rely on it)
