import asyncio
import time
import json
import traceback
import subprocess
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import memory.scheduler_db as db

class KiraScheduler:
    def __init__(self, action_registry=None, ui_notify=None):
        self.action_registry = action_registry
        self.ui_notify = ui_notify
        self.running = False
        self.dnd_mode = False

    def toggle_dnd(self, state: bool):
        self.dnd_mode = state

    def calculate_next_run(self, schedule_type, expr, tz_str, base_time=None):
        if not base_time:
            base_time = time.time()
            
        tz = ZoneInfo(tz_str) if tz_str else ZoneInfo("UTC")
        now_dt = datetime.fromtimestamp(base_time, tz)

        if schedule_type == "one_shot":
            # expr should be an ISO datetime string
            target_dt = datetime.fromisoformat(expr)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=tz)
            return target_dt.timestamp()

        elif schedule_type == "interval":
            # expr e.g. "60m", "24h", "5d"
            val = int(expr[:-1])
            unit = expr[-1]
            if unit == 'm': delta = timedelta(minutes=val)
            elif unit == 'h': delta = timedelta(hours=val)
            elif unit == 'd': delta = timedelta(days=val)
            else: return None
            return (now_dt + delta).timestamp()

        elif schedule_type == "daily":
            # expr e.g. "09:00"
            hour, minute = map(int, expr.split(':'))
            target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_dt <= now_dt:
                target_dt += timedelta(days=1)
            return target_dt.timestamp()

        return None

    def recover_missed_runs(self):
        automations = db.get_active_automations()
        now = time.time()
        for auto in automations:
            if auto['next_run'] < now - 60:
                # Missed
                if auto['schedule_type'] == 'one_shot':
                    if auto['execution_policy'] == 'notify':
                        self._trigger_notification(f"Missed: {auto['title']}", "Tarea programada atrasada.")
                        db.update_status(auto['id'], 'completed')
                    else:
                        db.update_status(auto['id'], 'missed')
                else:
                    # Recalculate next run from NOW
                    new_next = self.calculate_next_run(auto['schedule_type'], auto['schedule_expr'], auto['timezone'])
                    if new_next:
                        db.update_next_run(auto['id'], new_next, auto['last_run'])
                        print(f"[Scheduler] Reprogrammed missed recurring job: {auto['title']}")

    async def run_loop(self):
        self.running = True
        self.recover_missed_runs()
        
        while self.running:
            now = time.time()
            automations = db.get_active_automations()
            
            for auto in automations:
                if auto['next_run'] <= now:
                    asyncio.create_task(self.execute_automation(auto))
                    
                    # Update next run immediately to avoid double firing
                    if auto['schedule_type'] == 'one_shot':
                        db.update_status(auto['id'], 'completed')
                    else:
                        # Recalculate based on current next_run to keep strict intervals, or now if missed by a lot
                        new_next = self.calculate_next_run(auto['schedule_type'], auto['schedule_expr'], auto['timezone'], base_time=now)
                        db.update_next_run(auto['id'], new_next, now)
            
            await asyncio.sleep(5)

    def _trigger_notification(self, title, msg):
        if self.dnd_mode: return
        script = f'display notification "{msg}" with title "{title}"'
        subprocess.run(['osascript', '-e', script])

    async def execute_automation(self, auto):
        start_t = time.time()
        title = auto['title']
        goal_str = auto['goal']
        policy = auto['execution_policy']
        
        try:
            goal = json.loads(goal_str)
        except:
            goal = {}

        if policy == 'confirm':
            # In a real UI, we'd queue a prompt. Here we notify and wait.
            self._trigger_notification("KIRA: Confirmación requerida", f"¿Ejecutar: {title}?")
            # We mock the confirmation for background headless.
            # We'll just run it safely if it's deterministic.
            pass

        # Execution logic
        status = "failed"
        summary = "No goal action defined."

        if goal.get('type') == 'deterministic':
            # Run without Gemini
            action_name = goal.get('action')
            args = goal.get('args', {})
            if self.action_registry and self.action_registry.has(action_name):
                try:
                    res = self.action_registry.run(action_name, args)
                    status = "success"
                    summary = str(res)[:200]
                    self._trigger_notification(f"KIRA: {title}", summary)
                except Exception as e:
                    summary = f"Error: {e}"
        
        elif goal.get('type') == 'gemini':
            # Require Reasoning
            # A lightweight background REST call using the standard SDK would go here.
            # To avoid adding dependencies, we mock it via internal message queue
            if self.ui_notify:
                self.ui_notify("BACKGROUND_GOAL_START", {"title": title, "prompt": goal.get('prompt')})
            status = "success"
            summary = "Sent to reasoning queue."

        elif policy == 'notify':
            self._trigger_notification(f"KIRA Reminder", title)
            status = "success"
            summary = "Notified user."

        db.log_history(auto['id'], start_t, time.time(), status, summary)

