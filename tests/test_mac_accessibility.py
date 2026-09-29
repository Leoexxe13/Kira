import unittest
from actions.mac_accessibility import handle_action

class TestMacAccessibility(unittest.TestCase):
    def test_get_tree(self):
        # We can't guarantee a specific app is running, so we test the failure or a generic one like Finder
        args = {"action": "get_tree", "app_name": "Finder"}
        res = handle_action(args)
        # Should return something, or "Error" if Finder has no windows
        self.assertTrue(isinstance(res, str))

    def test_click_invalid(self):
        args = {"action": "click_element", "app_name": "Finder", "element_name": "NonExistent"}
        res = handle_action(args)
        self.assertIn("Error", res)

if __name__ == '__main__':
    unittest.main()
