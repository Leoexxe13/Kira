import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.mac_orchestrator import mac_orchestrator, TOOL
from core import confirm as confirm_gate

class TestMacOrchestrator(unittest.TestCase):
    def setUp(self):
        # Reset and bind confirm gate
        confirm_gate._pending = None
        confirm_gate.bind(lambda t,d: None, lambda: None, lambda m: None)
        
    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator._run_applescript")
    def test_list_apps(self, mock_script):
        mock_script.return_value = {"success": True, "output": "Finder, Safari, System Settings"}
        res = mac_orchestrator({"action": "list_apps"})
        self.assertTrue(res["success"])
        self.assertEqual(res["apps"], ["Finder", "Safari", "System Settings"])

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator._run_applescript")
    def test_get_active_app(self, mock_script):
        mock_script.return_value = {"success": True, "output": "Safari"}
        res = mac_orchestrator({"action": "get_active_app"})
        self.assertTrue(res["success"])
        self.assertEqual(res["active_app"], "Safari")

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator.subprocess.run")
    def test_open_app(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        res = mac_orchestrator({"action": "open_app", "app_name": "Terminal"})
        self.assertTrue(res["success"])
        mock_run.assert_called_with(["open", "-a", "Terminal"], check=True, capture_output=True, timeout=10)

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator._run_applescript")
    def test_close_app_requires_confirm(self, mock_script):
        # Should return a confirmation request string, not a dict
        res = mac_orchestrator({"action": "close_app", "app_name": "Safari"})
        self.assertTrue(isinstance(res, str))
        self.assertIn("Close application Safari", res)
        self.assertIsNotNone(confirm_gate.pending_title())
        
        # Accept confirm
        mock_script.return_value = {"success": True}
        run_func = confirm_gate._pending.run
        res2 = run_func()
        self.assertEqual(res2["action"], "close_app")
        self.assertTrue(res2["success"])

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator.subprocess.run")
    def test_open_file(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        res = mac_orchestrator({"action": "open_file", "app_name": "Preview", "file_path": "/tmp/test.pdf"})
        self.assertTrue(res["success"])
        mock_run.assert_called_with(["open", "-a", "Preview", "/tmp/test.pdf"], check=True, capture_output=True, timeout=10)

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator._run_applescript")
    def test_list_windows(self, mock_script):
        mock_script.return_value = {"success": True, "output": "Inbox, Message"}
        res = mac_orchestrator({"action": "list_windows", "app_name": "Mail"})
        self.assertTrue(res["success"])
        self.assertEqual(res["windows"], ["Inbox", "Message"])

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator.subprocess.run")
    def test_clipboard_read(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="hello unicode 😊")
        res = mac_orchestrator({"action": "clipboard_read"})
        self.assertTrue(res["success"])
        self.assertEqual(res["content"], "hello unicode 😊")

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator.psutil")
    def test_system_info(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 15.5
        mock_ram = MagicMock()
        mock_ram.percent = 45.0
        mock_ram.used = 8 * 1024**3
        mock_ram.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_ram
        
        mock_disk = MagicMock()
        mock_disk.percent = 80.0
        mock_disk.free = 100 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        
        mock_psutil.boot_time.return_value = 1000000
        mock_psutil.pids.return_value = [1, 2, 3]
        
        res = mac_orchestrator({"action": "system_info"})
        self.assertTrue(res["success"])
        self.assertEqual(res["metrics"]["cpu_percent"], 15.5)
        self.assertEqual(res["metrics"]["process_count"], 3)

    @patch("actions.mac_orchestrator._IS_MAC", True)
    @patch("actions.mac_orchestrator.subprocess.run")
    def test_applescript_permission_error(self, mock_run):
        # When subprocess fails with -54
        mock_run.return_value = MagicMock(returncode=1, stderr="execution error: Not authorized to send Apple events (-1743)")
        res = mac_orchestrator({"action": "get_active_app"})
        self.assertFalse(res["success"])
        self.assertIn("Permission Denied", res["error"])

if __name__ == "__main__":
    unittest.main()
