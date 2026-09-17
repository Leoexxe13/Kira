"""Local personal records. No network, no imports that create user data."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'memory' / 'personal.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS shifts (
 id INTEGER PRIMARY KEY, started TEXT NOT NULL, ended TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_shift ON shifts((1)) WHERE ended IS NULL;
CREATE TABLE IF NOT EXISTS work_events (
 id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, recorded_at TEXT NOT NULL,
 shift_id INTEGER REFERENCES shifts(id), category TEXT NOT NULL,
 description TEXT NOT NULL, person TEXT NOT NULL DEFAULT '', resource TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local', notes TEXT NOT NULL DEFAULT '',
 related_id INTEGER REFERENCES work_events(id)
);
CREATE TABLE IF NOT EXISTS resources (
 resource TEXT PRIMARY KEY, person TEXT NOT NULL, state TEXT NOT NULL,
 event_id INTEGER NOT NULL REFERENCES work_events(id), timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_pending (
 event_id INTEGER PRIMARY KEY REFERENCES work_events(id), state TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS movements (
 id INTEGER PRIMARY KEY, created TEXT NOT NULL, paid_at TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('income','planned','expense','reserve')),
 amount INTEGER NOT NULL CHECK(amount > 0), currency TEXT NOT NULL,
 description TEXT NOT NULL, category TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('open','paid'))
);
CREATE TABLE IF NOT EXISTS wallet_events (
 id INTEGER PRIMARY KEY, movement_id INTEGER NOT NULL REFERENCES movements(id),
 timestamp TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
);
PRAGMA user_version=1;
'''


class PersonalStore:
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.clock = clock or (lambda: datetime.now().astimezone())

    def now(self):
        return self.clock().isoformat(timespec='seconds')

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve an owner-only file before SQLite opens it.
        import os
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise RuntimeError('La base personal pertenece a una versión posterior')
            if version == 0:
                db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            if version < 2:
                from core.personal_edits import install
                install(db)
                db.execute('PRAGMA user_version=2')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
