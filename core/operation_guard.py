"""Short-lived failure circuit. Never caches successful mutations or WhatsApp reads."""
import json
import threading
import time
from dataclasses import dataclass
from enum import Enum

class ResultState(str,Enum):
    REQUESTED='requested'; PLANNED='planned'; EXECUTED='executed'; VERIFIED='verified'; FAILED='failed'; PENDING='pending'

@dataclass
class Outcome:
    state: ResultState
    detail: str
    def verify(self,evidence=False):
        if self.state != ResultState.FAILED and evidence:self.state=ResultState.VERIFIED
        return self

class RetryGuard:
    def __init__(self,clock=time.monotonic,ttl=60):
        self.clock,self.ttl=clock,ttl
        self.failures={};self.lock=threading.Lock()
    def key(self,name,args):return name,json.dumps(args,sort_keys=True,default=str,ensure_ascii=False)
    def blocked(self,name,args,generation=0):
        with self.lock:
            keys=(self.key(name,args),(name,'internal'))
            for key in keys:
                e=self.failures.get(key)
                if e and e[0]>=2 and self.clock()-e[1]<self.ttl and e[2]==generation:
                    return 'Reintento automático bloqueado: la misma estrategia falló dos veces. Cambia las condiciones o espera un minuto.'
        return None
    def record(self,name,args,result,failed,generation=0):
        text=str(result).lower()
        cause='internal' if any(x in text for x in ('unexpected keyword argument','sync api inside','sync api inside the asyncio','typeerror')) else self.key(name,args)[1]
        key=(name,cause)
        with self.lock:
            self.failures = {k:v for k,v in self.failures.items() if self.clock()-v[1]<self.ttl and v[2]==generation}
            if not failed:
                self.failures.pop(self.key(name,args),None);return
            old=self.failures.get(key)
            count=old[0]+1 if old and self.clock()-old[1]<self.ttl and old[2]==generation else 1
            self.failures[key]=(count,self.clock(),generation)

RETRIES=RetryGuard()
