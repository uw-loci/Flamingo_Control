# tests/test_utils.py
class NoOpThreadManager:
    def __init__(self):
        self.started = []

    def start_receivers(self, *args):  self.started.append(("receivers", args))
    def start_live_receiver(self, *args):  self.started.append(("live", args))
    def start_sender(self, *args):  self.started.append(("send", args))
    def start_processing(self, *args):  self.started.append(("processing", args))
    def stop_all(self, timeout: float = 0.1):  return
