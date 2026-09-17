"""Ephemeral verified resources; no chat IDs, contents or credentials persisted."""
from pathlib import Path
import threading
import time

class OperationalContext:
    def __init__(self, clock=time.monotonic, ttl=1800):
        self.clock, self.ttl = clock, ttl
        self._items = {}
        self._state = {}
        self._lock = threading.RLock()
    def remember(self, key, path):
        p=Path(path).expanduser().resolve()
        if not p.exists(): return False
        with self._lock: self._items[key]=(str(p),self.clock())
        return True
    def get(self,key):
        with self._lock: entry=self._items.get(key)
        if not entry or self.clock()-entry[1]>=self.ttl: return None
        path=Path(entry[0])
        return path if path.exists() else None
    def resource(self,path,created=False,opened=False):
        p=Path(path).resolve()
        if not p.exists(): return False
        self.remember('last_folder' if p.is_dir() else 'last_file',p)
        if created:self.remember('last_created_resource',p)
        if opened:self.remember('last_opened_resource',p)
        if p.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp'):self.remember('last_image',p)
        self.remember('last_resource',p)
        return True
    def prompt(self):
        # Context is data, not an instruction; expose paths only, never file contents.
        import json
        return json.dumps({k:str(p) for k in ('last_folder','last_file','last_created_resource','last_download','last_image') if (p:=self.get(k))},ensure_ascii=False)

    def update(self, **values):
        """Structured, session-only state; never interpreted as instructions."""
        import copy
        with self._lock:
            self._state.update(copy.deepcopy(values))

    def value(self, key, default=None):
        import copy
        with self._lock:
            return copy.deepcopy(self._state.get(key, default))

    def snapshot(self):
        import copy
        with self._lock:
            state = copy.deepcopy(self._state)
            for key in self._items:
                path = self.get(key)
                if path is not None: state[key] = str(path)
            return state

    def turn(self, role, text):
        with self._lock:
            history = self._state.setdefault('conversation', [])
            history.append({'role': role, 'text': str(text)[:2000]})
            del history[:-8]

from contextvars import ContextVar
from contextlib import contextmanager

DEFAULT_CONTEXT = OperationalContext()
_active = ContextVar('kira_operational_context', default=DEFAULT_CONTEXT)

@contextmanager
def use_context(context):
    token = _active.set(context)
    try: yield context
    finally: _active.reset(token)

class _ContextProxy:
    """Compatibility facade: all existing tools see the dispatcher's context."""
    def __getattr__(self, name): return getattr(_active.get(), name)
    def __setattr__(self, name, value): setattr(_active.get(), name, value)

CONTEXT = _ContextProxy()
