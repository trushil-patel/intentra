"""Opt-in timing labels contain no commands, content, URLs or window titles."""
import functools
import os
import time
from contextlib import contextmanager

@contextmanager
def measure(stage):
    start = time.perf_counter()
    try:
        yield
    finally:
        if os.getenv('VOXPILOT_PERF', 'false').lower() == 'true':
            print(f'[PERF] {stage} = {(time.perf_counter() - start) * 1000:.2f} ms')

def timed(stage):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(stage):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
