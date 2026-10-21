"""Capture task-thread text output while other threads retain their own streams."""
from contextlib import contextmanager
import sys
import threading

_capture_lock = threading.RLock()


class _ThreadOutput:
    def __init__(self, fallback):
        self.fallback = fallback
        self.local = threading.local()
        self.active = 0

    def stream(self):
        buffers = getattr(self.local, 'buffers', ())
        return buffers[-1] if buffers else self.fallback

    def write(self, text):
        stream = self.stream()
        return stream.write(text) if stream is not None else len(text)

    def flush(self):
        stream = self.stream()
        if stream is not None:
            stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream(), name)

    def push(self, buffer):
        if not hasattr(self.local, 'buffers'):
            self.local.buffers = []
        self.local.buffers.append(buffer)

    def pop(self, buffer):
        if not self.local.buffers or self.local.buffers[-1] is not buffer:
            raise RuntimeError('Task output scopes must exit in their original thread/order')
        self.local.buffers.pop()
        if not self.local.buffers:
            del self.local.buffers


@contextmanager
def capture_task_stdout(buffer):
    with _capture_lock:
        proxy = sys.stdout
        if not isinstance(proxy, _ThreadOutput):
            proxy = _ThreadOutput(sys.stdout)
            sys.stdout = proxy
        proxy.push(buffer)
        proxy.active += 1
    try:
        yield buffer
    finally:
        with _capture_lock:
            proxy.pop(buffer)
            proxy.active -= 1
            if proxy.active == 0 and sys.stdout is proxy:
                sys.stdout = proxy.fallback
