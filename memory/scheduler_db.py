import sqlite3
import json
import uuid
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path("memory") / "scheduler.db"

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                title TEXT,
                goal TEXT,
                schedule_type TEXT,
                schedule_expr TEXT,
                timezone TEXT,
                status TEXT,
                next_run REAL,
                last_run REAL,
                execution_policy TEXT,
                created_at REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS automation_history (
                id TEXT PRIMARY KEY,
                auto_id TEXT,
                started_at REAL,
                completed_at REAL,
                status TEXT,
                result_summary TEXT
            )
        ''')

def add_automation(title, goal, schedule_type, schedule_expr, timezone, next_run, execution_policy="safe"):
    init_db()
    auto_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            INSERT INTO automations 
            (id, title, goal, schedule_type, schedule_expr, timezone, status, next_run, last_run, execution_policy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (auto_id, title, json.dumps(goal), schedule_type, schedule_expr, timezone, 'active', next_run, 0.0, execution_policy, time.time()))
    return auto_id

def update_status(auto_id, status):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('UPDATE automations SET status = ? WHERE id = ?', (status, auto_id))

def update_next_run(auto_id, next_run, last_run):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('UPDATE automations SET next_run = ?, last_run = ? WHERE id = ?', (next_run, last_run, auto_id))

def get_active_automations():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute('SELECT * FROM automations WHERE status = "active"')
        return [dict(r) for r in cur.fetchall()]

def get_all_automations():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute('SELECT * FROM automations ORDER BY created_at DESC')
        return [dict(r) for r in cur.fetchall()]

def log_history(auto_id, started_at, completed_at, status, summary):
    init_db()
    hist_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            INSERT INTO automation_history (id, auto_id, started_at, completed_at, status, result_summary)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (hist_id, auto_id, started_at, completed_at, status, summary))

init_db()
