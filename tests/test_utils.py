# tests/test_utils.py
class NoOpThreadManager:
    def __init__(self, *args, **kwargs):
        self.started = []
        self.kwargs = kwargs

    # New: legacy compatibility – some code calls this in one shot
    def start_all_threads(self, *args, **kwargs):
        self.started.append(("all", args, kwargs))
        # Return 4 dummy “threads”
        from unittest.mock import MagicMock
        return (MagicMock(name="cmd-listen"),
                MagicMock(name="live-listen"),
                MagicMock(name="sender"),
                MagicMock(name="processing"))

    # New: legacy compatibility
    def stop_all_threads(self, *args, **kwargs):
        return

    # Granular starters (if your prod code calls these)
    def start_receivers(self, *args):      self.started.append(("receivers", args))
    def start_live_receiver(self, *args):  self.started.append(("live", args))
    def start_sender(self, *args):         self.started.append(("sender", args))
    def start_processing(self, *args):     self.started.append(("processing", args))
    def stop_all(self, timeout: float = 0.1):  return
