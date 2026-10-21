import os
import threading


class FileMutationQueue:
    def __init__(self):
        self._locks = {}

        self._registry = threading.Lock()

    @staticmethod
    def _key_for(path):
        return os.path.realpath(os.path.abspath(path))

    def run(self, path, fn):
        key = self._key_for(path)

        with self._registry:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock

        with lock:
            return fn()


mutation_queue = FileMutationQueue()
