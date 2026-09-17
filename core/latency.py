"""Bounded timing diagnostics; no prompts, contact IDs or personal payloads."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
import uuid

CURRENT = ContextVar('kira_latency', default=None)
RECENT = deque(maxlen=100)
_LOCK = threading.Lock()


class Trace:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.id = uuid.uuid4().hex[:8]
        self.stages = {}
        self.done = False

    @contextmanager
    def stage(self, name):
        start = self.clock()
        try:
            yield
        finally:
            with _LOCK:
                self.stages[name] = self.stages.get(name, 0) + max(0, self.clock()-start)

    def finish(self):
        with _LOCK:
            if self.done:return
            self.done = True
            result = dict(id=self.id, stages=dict(self.stages), total=max(0,self.clock()-self.started))
            RECENT.append(result)
        stages = dict(result['stages'])
        for key in ('STT observado','intent/router','context/local','planner','tool','provider/LLM','TTS'):
            stages.setdefault(key,None)
        print('[Latency] ' + self.id + ' | ' + ' | '.join(f'{k} {v:.3f}s' if v is not None else f'{k} N/D' for k,v in stages.items()) + f" | Total medido {result['total']:.3f}s")


@contextmanager
def stage(name):
    trace = CURRENT.get()
    if trace:
        with trace.stage(name):yield
    else:yield
