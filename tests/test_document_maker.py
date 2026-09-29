import unittest
import os
import json
import tempfile
from actions.document_maker import handle_action

class TestDocumentMaker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_txt(self):
        path = os.path.join(self.temp_dir.name, "test.txt")
        args = {
            "action": "create_txt",
            "file_path": path,
            "title": "Test TXT",
            "content": [{"text": "Hello KIRA"}, {"text": "Line 2"}]
        }
        res = handle_action(args)
        self.assertIn("DELIVERABLE_CREATED", res)
        self.assertTrue(os.path.exists(path))
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("Hello KIRA", content)

    def test_create_md(self):
        path = os.path.join(self.temp_dir.name, "test.md")
        args = {
            "action": "create_md",
            "file_path": path,
            "title": "Test MD",
            "content": [{"text": "# Heading"}, {"text": "**Bold**"}]
        }
        res = handle_action(args)
        self.assertIn("DELIVERABLE_CREATED", res)
        self.assertTrue(os.path.exists(path))

    def test_create_docx_mock(self):
        # Even if python-docx isn't installed, the handler catches the exception or returns error.
        path = os.path.join(self.temp_dir.name, "test.docx")
        args = {
            "action": "create_docx",
            "file_path": path,
            "title": "Test DOCX",
            "content": [{"type": "heading", "text": "H1", "level": 1}]
        }
        res = handle_action(args)
        # If it's missing, it returns an Error string instead of DELIVERABLE_CREATED json
        if "Error: python-docx not installed" not in res:
            self.assertIn("DELIVERABLE_CREATED", res)

    def test_create_xlsx_mock(self):
        path = os.path.join(self.temp_dir.name, "test.xlsx")
        args = {
            "action": "create_xlsx",
            "file_path": path,
            "title": "Test XLSX",
            "content": [["A1", "B1"], ["A2", "B2"]]
        }
        res = handle_action(args)
        if "Error: openpyxl not installed" not in res:
            self.assertIn("DELIVERABLE_CREATED", res)

    def test_create_pptx_mock(self):
        path = os.path.join(self.temp_dir.name, "test.pptx")
        args = {
            "action": "create_pptx",
            "file_path": path,
            "title": "Test PPTX",
            "content": [{"title": "Slide 1", "layout": "content", "content": ["Bullet 1", "Bullet 2"]}]
        }
        res = handle_action(args)
        if "Error: python-pptx not installed" not in res:
            self.assertIn("DELIVERABLE_CREATED", res)

    def test_pptx_visual_qa(self):
        path = os.path.join(self.temp_dir.name, "test_qa.pptx")
        args = {
            "action": "create_pptx",
            "file_path": path,
            "title": "Test PPTX QA",
            "content": [{"title": "Too Many Bullets", "layout": "content", "content": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]}]
        }
        res = handle_action(args)
        # Should reject because of > 6 bullets
        self.assertIn("Visual QA Failed", res)
        self.assertIn("Too Many Bullets", res)

if __name__ == '__main__':
    unittest.main()
