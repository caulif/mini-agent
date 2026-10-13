# -*- coding: utf-8 -*-
"""Cancellation and steering (chapter 4).

Signal handles cooperative cancellation, SteeringQueue stores follow-up
instructions, and the loop handles the turn limit. This independent module
can be imported by both the model transport and the agent loop."""

import threading


class Aborted(Exception):
    """Raise at cancellation checkpoints to unwind nested operations."""

    def __init__(self, reason="aborted"):
        super().__init__(reason)
        self.reason = reason


class Signal:
    """Share cancellation state across threads with threading.Event."""

    def __init__(self):
        self._event = threading.Event()
        self.reason = None

    def abort(self, reason="user aborted"):
        """Request cancellation; preserve the first recorded reason."""
        if not self._event.is_set():
            self.reason = reason
        self._event.set()

    @property
    def aborted(self):
        """Return whether cancellation has been requested, without raising."""
        return self._event.is_set()

    def check(self):
        """Raise Aborted if cancellation has been requested."""
        if self._event.is_set():
            raise Aborted(self.reason)

    def reset(self):
        """Clear cancellation state so the next interactive turn can reuse it."""
        self._event.clear()
        self.reason = None


class SteeringQueue:
    """Store follow-up instructions until the next model request.
    
    The main thread pushes input while the agent thread drains the queue.
    A list preserves multiple instructions instead of overwriting older input."""

    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def push(self, text):
        """Enqueue one instruction from the input thread."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._items.append(text)

    def drain(self):
        """Atomically take all instructions and clear the queue."""
        with self._lock:
            items, self._items = self._items, []
        return items
