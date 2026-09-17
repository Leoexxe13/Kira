"""Process-wide transport state. No probes on import; one bounded recovery probe."""
from enum import Enum
import socket
import threading
import time
import errno

class State(str, Enum):
    ONLINE='ONLINE'
    DEGRADED='DEGRADED'
    OFFLINE='OFFLINE'
    RECONNECTING='RECONNECTING'

def transport_failure(error):
    children = getattr(error, 'exceptions', ())
    if children: return any(transport_failure(e) for e in children)
    if isinstance(error, socket.gaierror): return True
    if isinstance(error, OSError) and error.errno in (errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH):
        return True
    reason = getattr(error, 'reason', None)
    if isinstance(reason, BaseException) and reason is not error:
        return transport_failure(reason)
    text = str(error).lower()
    return any(x in text for x in ('getaddrinfo', 'nodename nor servname', 'name or service not known', 'network is unreachable', 'network is down', 'temporary failure in name resolution', 'errno 8', 'errno 101', 'errno 65'))

class NetworkState:
    def __init__(self, clock=time.monotonic, probe=None):
        self.state = State.ONLINE
        self.clock = clock
        self.probe = probe or self._probe
        self._lock = threading.RLock()
        self._probing = False
        self._next_probe = 0
        self.generation = 0
        self.listener = None
    @property
    def allowed(self):
        return self.state in (State.ONLINE, State.DEGRADED)
    def transition(self, state):
        with self._lock:
            if self.state == state: return
            self.state = state
            self.generation += 1
            listener = self.listener
        if listener: listener(state)
    def failure(self, error):
        if transport_failure(error):
            self.transition(State.OFFLINE)
            return True
        if self.state == State.ONLINE: self.transition(State.DEGRADED)
        return False
    def success(self):
        self.transition(State.ONLINE)
    @staticmethod
    def _probe():
        # TCP connectivity and DNS, no provider API requests or credentials.
        for host in ('www.apple.com', 'www.microsoft.com'):
            try:
                with socket.create_connection((host,443),timeout=2): return True
            except OSError: pass
        return False
    def recover(self):
        with self._lock:
            if self.allowed: return True
            if self._probing or self.clock() < self._next_probe: return False
            self._probing = True
            self._next_probe = self.clock()+15
        self.transition(State.RECONNECTING)
        try:
            ok = bool(self.probe())
            self.transition(State.ONLINE if ok else State.OFFLINE)
            return ok
        except OSError:
            self.transition(State.OFFLINE)
            return False
        finally:
            with self._lock: self._probing=False

NETWORK = NetworkState()
