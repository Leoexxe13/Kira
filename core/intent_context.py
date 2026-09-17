"""Ephemeral, bounded conversational/UI references keyed by local database.

Confirmation never survives process restart. IDs are always checked in storage.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time

@dataclass
class Context:
    domain: str = ''
    page: str = ''
    last: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    visible: list = field(default_factory=list)
    filter: str = ''
    pending: object = None
    updated: float = 0
    lock: object = field(default_factory=threading.RLock)

    def fresh(self):
        if time.monotonic()-self.updated >= 300:
            self.pending=None; self.last.clear(); self.results=[]
        self.updated=time.monotonic()

_contexts=OrderedDict()
_lock=threading.RLock()
def context_for(store):
    from memory.user_memory import _active_memory
    memory = _active_memory.get()
    if memory is None:
        import os
        from memory.user_memory import UserMemory
        memory = UserMemory(store, f'local-os:{os.getuid()}', 'desktop')
    key=(str(Path(store.path).resolve()), memory.user_id, memory.session_id)
    with _lock:
        if key not in _contexts:
            if len(_contexts)>=64:_contexts.popitem(last=False)
            _contexts[key]=Context()
        return _contexts[key]

def publish_ui(store, domain='', selected=(), visible=(), filter=''):
    ctx=context_for(store)
    with ctx.lock:
        ctx.page=domain
        if domain:ctx.domain=domain
        ctx.selected=list(selected);ctx.visible=list(visible);ctx.filter=filter
