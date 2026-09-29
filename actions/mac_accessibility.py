import subprocess
import json
import traceback



def get_ax_tree(app_name: str) -> str:
    script = f'''
    tell application "System Events"
        if not (exists process "{app_name}") then return "Error: App not running."
        tell process "{app_name}"
            if (count of windows) = 0 then return "Error: No windows open."
            set w to front window
            set output to ""
            try
                set buttons to (name of every button of w)
                set output to output & "Buttons: " & (buttons as string) & " | "
            end try
            try
                set fields to (name of every text field of w)
                set output to output & "Text Fields: " & (fields as string)
            end try
            return output
        end tell
    end tell
    '''
    res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if res.returncode != 0:
        return f"Error reading AX Tree: {res.stderr.strip()}"
    return res.stdout.strip()

def click_ax_element(app_name: str, element_name: str, element_role: str = "button") -> str:
    # A generic script to click a named element. 
    # AppleScript's "click button X" works if it's a direct child, but for deep trees we might need "entire contents", which is very slow.
    # We will try a targeted approach.
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            set target to null
            try
                if "{element_role}" = "button" then
                    click button "{element_name}" of front window
                    return "Clicked button {element_name}"
                else if "{element_role}" = "menu item" then
                    click menu item "{element_name}" of menu 1 of menu bar item 1 of menu bar 1
                    return "Clicked menu item {element_name}"
                end if
            on error
                return "Error: Element not found or not clickable."
            end try
        end tell
    end tell
    '''
    res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else f"Error: {res.stderr.strip()}"

def handle_action(args: dict, context: dict = None) -> str:
    action = args.get("action")
    app_name = args.get("app_name")
    
    if action == "get_tree":
        return get_ax_tree(app_name)
    elif action == "click_element":
        return click_ax_element(app_name, args.get("element_name", ""), args.get("element_role", "button"))
    else:
        return "Error: Invalid action."


TOOL = {
    "name": "mac_accessibility",
    "description": (
        "Reads the macOS Accessibility Tree to find or click UI elements (buttons, fields, menus). "
        "Use this for robust Computer Use before falling back to blind visual coordinates. "
        "Actions: 'get_tree' (returns structured UI elements), 'click_element' (clicks by role/name)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'get_tree' or 'click_element'"
            },
            "app_name": {
                "type": "STRING",
                "description": "The name of the application (e.g., 'Safari', 'System Settings')."
            },
            "element_name": {
                "type": "STRING",
                "description": "Name/label of the element to click (only for 'click_element')."
            },
            "element_role": {
                "type": "STRING",
                "description": "Role of the element to click (e.g., 'button', 'menu item'). Optional."
            }
        },
        "required": ["action", "app_name"]
    },
    "handler": handle_action
}
