"""Serialize memory-heavy calculation and packing on the shared web host."""
from functools import wraps
import threading
import gc
import sys


def release_temporary_memory():
    """Return released calculation/checkpoint buffers before the next task."""
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        if sys.platform.startswith('linux'):
            trim = libc.malloc_trim
            trim.argtypes, trim.restype = [ctypes.c_size_t], ctypes.c_int
            trim(0)
        elif sys.platform == 'darwin':
            trim = libc.malloc_zone_pressure_relief
            trim.argtypes, trim.restype = [ctypes.c_void_p, ctypes.c_size_t], ctypes.c_size_t
            trim(None, 0)
    except (AttributeError, OSError):
        pass

memory_work = threading.RLock()


def bounded_memory(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with memory_work:
            release_temporary_memory()
            try:
                return fn(*args, **kwargs)
            finally:
                release_temporary_memory()
    return run
