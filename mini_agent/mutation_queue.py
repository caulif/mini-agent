# -*- coding: utf-8 -*-
"""Coordinate file mutations by normalized path (chapter 3).

Concurrent read-modify-write operations can overwrite each other's changes.
Protect the entire operation with one lock per real path. Different files
retain independent locks. A separate registry lock protects lock creation
so concurrent callers for the same path cannot create different locks."""

import os
import threading


class FileMutationQueue:
    """Serialize file mutations using a lock per normalized absolute path."""

    def __init__(self):
        # Normalized path key -> lock.
        self._locks = {}
        # Protect the lock registry itself.
        self._registry = threading.Lock()

    @staticmethod
    def _key_for(path):
        """Normalize existing or new file paths with abspath and realpath."""
        return os.path.realpath(os.path.abspath(path))

    def run(self, path, fn):
        """Run the no-argument mutation fn under the target path's lock."""
        key = self._key_for(path)

        # Serialize lock creation so same-path callers share one lock.
        with self._registry:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock

        # Serialize operations per path; different paths use different locks.
        with lock:
            return fn()


# Shared instance used by the write and edit tools.
mutation_queue = FileMutationQueue()
