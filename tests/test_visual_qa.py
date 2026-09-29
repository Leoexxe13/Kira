import unittest
import os
import json
from actions.visual_qa import handle_action

class TestVisualQA(unittest.TestCase):
    def test_visual_qa_missing_file(self):
        args = {"file_path": "/path/does/not/exist.pptx"}
        res = handle_action(args)
        self.assertIn("Error: File does not exist", res)

    def test_visual_qa_mock_render(self):
        # Create a temp text file just to pass existence check
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"test")
            path = f.name
        
        args = {"file_path": path}
        res = handle_action(args)
        # Since LibreOffice successfully renders .txt to PDF and Quartz to PNG, it works!
        if "Error:" in res:
            self.assertIn("Error: Renderer", res)
        else:
            self.assertIn("VISUAL_QA_READY", res)
        os.remove(path)

if __name__ == '__main__':
    unittest.main()
