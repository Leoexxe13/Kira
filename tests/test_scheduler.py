import unittest
import time
import json
import os
from unittest.mock import MagicMock
from core.scheduler import KiraScheduler
import memory.scheduler_db as db
import actions.automation_manager as am

class TestSchedulerAndAutomations(unittest.TestCase):
    def setUp(self):
        # Clean test DB
        if os.path.exists("memory/scheduler.db"):
            os.remove("memory/scheduler.db")
        db.init_db()

    def tearDown(self):
        if os.path.exists("memory/scheduler.db"):
            os.remove("memory/scheduler.db")

    def test_automation_manager_create(self):
        # Create an interval automation
        args = {
            "action": "create",
            "title": "Test Interval",
            "schedule_type": "interval",
            "schedule_expr": "15m",
            "execution_policy": "safe",
            "goal": {"type": "notify"}
        }
        res = am.handle_action(args)
        self.assertIn("creada", res)

        autos = db.get_active_automations()
        self.assertEqual(len(autos), 1)
        self.assertEqual(autos[0]["title"], "Test Interval")
        self.assertEqual(autos[0]["status"], "active")

    def test_automation_manager_pause_resume(self):
        # Create
        args = {"action": "create", "title": "T2", "schedule_type": "interval", "schedule_expr": "1d"}
        am.handle_action(args)
        
        autos = db.get_active_automations()
        auto_id = autos[0]["id"]
        
        # Pause
        am.handle_action({"action": "pause", "id": auto_id})
        self.assertEqual(len(db.get_active_automations()), 0)
        
        # Resume
        am.handle_action({"action": "resume", "id": auto_id})
        self.assertEqual(len(db.get_active_automations()), 1)

    def test_scheduler_calculation(self):
        sched = KiraScheduler()
        now = time.time()
        # Interval calculation
        next_run = sched.calculate_next_run("interval", "60m", "UTC", base_time=now)
        self.assertTrue(now + 3500 < next_run < now + 3700)
        
        # One shot
        target_iso = "2026-10-01T15:00:00"
        nr2 = sched.calculate_next_run("one_shot", target_iso, "UTC")
        self.assertIsNotNone(nr2)

if __name__ == '__main__':
    unittest.main()
