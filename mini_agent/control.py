import threading


class Aborted(Exception):
    def __init__(self, reason="aborted"):
        super().__init__(reason)
        self.reason = reason


class Signal:
    def __init__(self):
        self._event = threading.Event()
        self.reason = None

    def abort(self, reason="user aborted"):
        if not self._event.is_set():
            self.reason = reason
        self._event.set()

    @property
    def aborted(self):
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            raise Aborted(self.reason)

    def reset(self):
        self._event.clear()
        self.reason = None


class SteeringQueue:
    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def push(self, text):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._items.append(text)

    def drain(self):
        with self._lock:
            items, self._items = self._items, []
        return items
