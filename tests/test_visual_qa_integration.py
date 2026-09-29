import unittest
import os
import tempfile
import json
from actions.document_maker import handle_action as make_doc
from actions.visual_qa import handle_action as run_qa, detect_libreoffice

class TestVisualQAIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        
    def tearDown(self):
        self.temp_dir.cleanup()
        
    def test_libreoffice_detect(self):
        info = detect_libreoffice()
        self.assertIn("available", info)
        
    def test_full_pipeline_pptx(self):
        info = detect_libreoffice()
        if not info.get("available"):
            self.skipTest("LibreOffice not available, skipping integration test.")
            
        pptx_path = os.path.join(self.temp_dir.name, "test_integration.pptx")
        
        # 1. Make Document
        doc_args = {
            "action": "create_pptx",
            "file_path": pptx_path,
            "title": "Integration Test",
            "content": [{"title": "Slide 1", "layout": "content", "content": ["Bullet 1", "Bullet 2"]}]
        }
        res_doc = make_doc(doc_args)
        self.assertIn("DELIVERABLE_CREATED", res_doc)
        self.assertTrue(os.path.exists(pptx_path))
        
        # 2. Run Visual QA
        qa_args = {
            "action": "render",
            "file_path": pptx_path
        }
        res_qa = run_qa(qa_args)
        
        # It should return VISUAL_QA_READY with images
        self.assertIn("VISUAL_QA_READY", res_qa)
        qa_data = json.loads(res_qa)
        
        images = qa_data.get("images", [])
        self.assertTrue(len(images) > 0)
        self.assertTrue(os.path.exists(images[0]))

if __name__ == '__main__':
    unittest.main()
