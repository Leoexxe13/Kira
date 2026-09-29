import unittest
import json
import os
import tempfile
import asyncio
from unittest.mock import MagicMock
from core.action_loader import discover_actions
from pathlib import Path

class TestIntegrationSmoke(unittest.TestCase):
    def setUp(self):
        self.registry = discover_actions(Path("actions"))
        self.ctx = {
            "player": MagicMock(),
            "speak": MagicMock(),
            "response": MagicMock(),
            "session_memory": MagicMock()
        }

    def test_regression_document_maker_kwargs(self):
        """
        Reproduce específicamente:
        document_maker { "title": "Tabla de Ventas", "action": "create_xlsx", ... }
        pasando por action_loader.run(...)
        Demuestra que 'unexpected keyword argument parameters' no reaparece.
        """
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        
        args = {
            "action": "create_xlsx",
            "file_path": path,
            "title": "Tabla de Ventas",
            "content": [["Vendedor", "Producto", "Cantidad", "Precio Unitario", "Total"],
                        ["Juan", "A", 10, 15.5, ""]],
            "format_options": {
                "calculated_columns": {"E": "=C{row}*D{row}"},
                "totals_row": True
            }
        }
        
        # Test invocation adapter
        result = self.registry.run("document_maker", args, self.ctx)
        
        self.assertNotIn("unexpected keyword argument", result)
        self.assertNotIn("failed:", result.lower())
        self.assertIn("DELIVERABLE_CREATED", result)
        
        import openpyxl
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        self.assertEqual(ws["E2"].value, "=C2*D2")
        self.assertEqual(ws["A3"].value, "TOTAL")
        wb.close()
        
        os.remove(path)

    def test_smoke_tool_failure_boundary(self):
        """
        Verifica que un fallo interno de una tool se propague estructuradamente
        como un string de error y no tumbe el registry ni el loop.
        """
        # Hacemos que action sea "invalid_action" de document_maker
        args = {
            "action": "formato_falso",
            "file_path": "fake.xyz",
            "title": "Falso",
            "content": []
        }
        result = self.registry.run("document_maker", args, self.ctx)
        self.assertTrue(isinstance(result, str))
        self.assertIn("Error:", result)
        
if __name__ == '__main__':
    unittest.main()
