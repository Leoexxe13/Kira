import unittest
import inspect
from pathlib import Path
from core.action_loader import discover_actions

class TestActionContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = discover_actions(Path("actions"))
        
    def test_all_actions_valid(self):
        """No deben haber actions inválidas en el registro."""
        for rec in self.registry._all_records:
            if not rec.valid:
                print(f"FAILED ACTION LOAD: {rec.name} -> {rec.error}")
            self.assertTrue(rec.valid, f"Action {rec.file} failed validation: {rec.error}")

    def test_all_handlers_callable(self):
        """Todos los handlers deben ser invocables y no asíncronos directamente (KIRA espera sincrónicos)."""
        for name, rec in self.registry._actions.items():
            self.assertTrue(callable(rec.handler), f"Handler for {name} is not callable")
            self.assertFalse(inspect.iscoroutinefunction(rec.handler), f"Handler for {name} cannot be async def (KIRA executes in ThreadPool)")

    def test_all_schemas_valid(self):
        """Todos los schemas de parámetros deben ser objetos."""
        for name, rec in self.registry._actions.items():
            p = rec.parameters
            self.assertEqual(p.get("type"), "OBJECT", f"Schema for {name} must be OBJECT")
            self.assertIn("properties", p, f"Schema for {name} must have properties")

    def test_capabilities_count(self):
        """Validar que KIRA reconozca el número esperado de capabilities externas."""
        self.assertGreaterEqual(len(self.registry.names()), 23, "Faltan external actions. Esperadas >= 23")

if __name__ == '__main__':
    unittest.main()
