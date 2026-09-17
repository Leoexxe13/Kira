"""SQLite memory owned by a trusted principal, never by model arguments."""
import json
import time
import uuid
import re
from memory.memory_manager import _score

SCHEMA = '''
CREATE TABLE IF NOT EXISTS memory_profiles (
 user_id TEXT PRIMARY KEY, principal TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS memory_revisions (user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memory_sessions (
 user_id TEXT NOT NULL REFERENCES memory_profiles(user_id), session_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT '{}', summary TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(user_id, session_id));
CREATE TABLE IF NOT EXISTS memory_facts (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES memory_profiles(user_id),
 kind TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL, origin TEXT NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL,
 UNIQUE(user_id,kind,name));
CREATE TABLE IF NOT EXISTS memory_messages (
 id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
 role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL,
 FOREIGN KEY(user_id,session_id) REFERENCES memory_sessions(user_id,session_id));
'''

_CREDENTIAL = re.compile(r"(?i)(?:password|contraseña|api[_ -]?key|access[_ -]?token|secret|clave)\s*[:=]\s*\S+|\b(?:sk-|gsk_)[A-Za-z0-9_-]{16,}")

def redact(value):
    if isinstance(value,str):
        return _CREDENTIAL.sub('[credencial omitida]',value)
    if isinstance(value,list):
        return [redact(v) for v in value]
    if isinstance(value,dict):
        return {k:('[credencial omitida]' if any(w in k.casefold() for w in ('password','api_key','token','secret','contraseña')) else redact(v)) for k,v in value.items()}
    return value

class UserMemory:
    def __init__(self, store, principal, session_id):
        if not principal or not session_id:
            raise ValueError('Se requiere identidad y sesión confiables')
        self.store, self.session_id = store, session_id
        with store.transaction() as db:
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO memory_profiles VALUES (?,?)', (uuid.uuid4().hex, principal))
            self.user_id = db.execute('SELECT user_id FROM memory_profiles WHERE principal=?', (principal,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO memory_sessions(user_id,session_id) VALUES (?,?)', self.scope)
            db.execute('INSERT OR IGNORE INTO memory_revisions(user_id) VALUES (?)',(self.user_id,))

    @property
    def scope(self):
        return self.user_id, self.session_id

    def remember(self, kind, name, content, origin, expires_at=None):
        if kind not in ('identity','preferences','projects','relationships','wishes','notes'):
            raise ValueError('Tipo de memoria no permitido')
        if not all(isinstance(v,str) and v.strip() for v in (name,content,origin)):
            raise ValueError('Memoria incompleta')
        if redact(content) != content:
            raise ValueError('No se almacenan credenciales en memoria')
        if len(content)>2000 or len(name)>200:
            raise ValueError('Memoria demasiado extensa')
        # Structured secret labels are rejected even if proposed by a model.
        if any(w in name.casefold() for w in ('password','contraseña','token','api_key','secret','clave')):
            raise ValueError('No se almacenan credenciales en memoria')
        now=time.time()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO memory_facts VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,kind,name) DO UPDATE SET content=excluded.content,
                origin=excluded.origin,updated_at=excluded.updated_at,expires_at=excluded.expires_at''',
                (uuid.uuid4().hex,self.user_id,kind,name,content,origin,now,now,expires_at))
            # Summaries may contain an earlier value. Invalidate derived data.
            db.execute("UPDATE memory_sessions SET summary='' WHERE user_id=?",(self.user_id,))
            db.execute("UPDATE memory_revisions SET revision=revision+1 WHERE user_id=?",(self.user_id,))
        return self.recall(name)

    def recall(self, query='', limit=8):
        with self.store.transaction() as db:
            rows=[dict(r) for r in db.execute('''SELECT * FROM memory_facts WHERE user_id=?
                AND (expires_at IS NULL OR expires_at>?) ORDER BY updated_at DESC''',(self.user_id,time.time()))]
        words=query.lower().split()
        ranked=[(_score(words,r['kind'],r['name'],r['content']) if words else 1,r) for r in rows]
        return [r for score,r in sorted(ranked,key=lambda pair:pair[0],reverse=True) if score>0][:limit]

    def forget(self, kind, name):
        with self.store.transaction() as db:
            count=db.execute('DELETE FROM memory_facts WHERE user_id=? AND kind=? AND name=?',(self.user_id,kind,name)).rowcount
            db.execute("UPDATE memory_sessions SET summary='' WHERE user_id=?",(self.user_id,))
            db.execute("UPDATE memory_revisions SET revision=revision+1 WHERE user_id=?",(self.user_id,))
        return count

    def revision(self):
        with self.store.transaction() as db:
            return db.execute('SELECT revision FROM memory_revisions WHERE user_id=?',(self.user_id,)).fetchone()[0]

    def invalidate_derived(self, context):
        revision=self.revision()
        if context.value('memory_revision') != revision:
            # Raw messages remain in the scoped audit history, but are no longer
            # fed back as derived memories after a correction or deletion.
            context.update(conversation=[], user_memories=[], memory_revision=revision)
            if context.value('last_tool') == 'user_memory':
                context.update(last_query_results=[], last_tool_result=None)

    def save_state(self, state):
        state=redact(dict(state))
        state.pop('user_memories',None)
        with self.store.transaction() as db:
            db.execute('UPDATE memory_sessions SET state=? WHERE user_id=? AND session_id=?',
                       (json.dumps(state,ensure_ascii=False),*self.scope))

    def load_state(self):
        with self.store.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM memory_sessions WHERE user_id=? AND session_id=?',self.scope).fetchone()[0])
        if state.get('pending_operation'):
            from core.semantic import normalize_pending_operation
            state['pending_operation']=normalize_pending_operation(state['pending_operation'])
            # Restoring data is never restoring authorization.
            state['pending_operation']['created_at']=0
        return state

    def message(self, role, content):
        with self.store.transaction() as db:
            db.execute('INSERT INTO memory_messages(user_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)',
                       (*self.scope,role,redact(content[:4000]),time.time()))

    def context(self, query, budget=2500):
        rows=self.recall(query,8)
        seen={r['id'] for r in rows}
        rows += [r for r in self.recall('',20) if r['kind'] in ('identity','preferences') and r['id'] not in seen]
        selected=[]
        for row in rows:
            item={k:row[k] for k in ('kind','name','content','origin')}
            cost=len(json.dumps(item,ensure_ascii=False))
            if cost<=budget:
                selected.append(item); budget-=cost
        return selected

from contextvars import ContextVar
from contextlib import contextmanager
_active_memory=ContextVar('kira_user_memory',default=None)

@contextmanager
def use_memory(memory):
    token=_active_memory.set(memory)
    try:
        yield memory
    finally:
        _active_memory.reset(token)

def active_memory():
    memory=_active_memory.get()
    if memory is None:
        raise RuntimeError('No hay una identidad de memoria vinculada a esta petición')
    return memory
