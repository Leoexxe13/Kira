import unittest
import threading
import asyncio
from unittest.mock import MagicMock
from core.action_loader import discover_actions
from pathlib import Path

class TestChaosAndErrorBoundaries(unittest.TestCase):
    def setUp(self):
        self.registry = discover_actions(Path("actions"))
        self.ctx = {
            "player": MagicMock(),
            "speak": MagicMock(),
            "response": MagicMock(),
            "session_memory": MagicMock()
        }

    def test_missing_args(self):
        """Si falta un parámetro requerido o un handler revienta, la tool debe devolver error, no petar la app."""
        # document_maker requires title, action, content, file_path. We send nothing.
        res = self.registry.run("document_maker", {}, self.ctx)
        # Should return an error string, not crash
        self.assertFalse(asyncio.iscoroutine(res), "Result cannot be a coroutine")
        self.assertIn("error", res.lower())

    def test_invalid_tool(self):
        """Llamar a una tool que no existe debe devolver un string de error amistoso para Gemini."""
        res = self.registry.run("fake_tool_xyz", {"data": 1}, self.ctx)
        self.assertIn("is not available", res)

    def test_async_boundary(self):
        """Ninguna acción debe devolver una coroutina. El registry devuelve texto plano."""
        # Todos los test de handlers comprobaron que no son async def.
        res = self.registry.run("web_search", {"action": "search", "query": "test"}, self.ctx)
        self.assertFalse(asyncio.iscoroutine(res), "Result cannot be a coroutine")
        self.assertFalse(asyncio.iscoroutine(res))

if __name__ == '__main__':
    unittest.main()
