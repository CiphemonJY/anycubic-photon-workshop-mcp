"""Exploration CLI: python pwcli.py <cmd> [args]   cmds: dump | shot PATH | click NAME | clickxy X Y | keys K"""
import sys
import pwlib

cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "dump":
    print(pwlib.dump(named_only=not args))
elif cmd == "shot":
    print(pwlib.screenshot(args[0]))
elif cmd == "click":
    print(pwlib.click(args[0]))
elif cmd == "clickxy":
    pwlib.click_xy(args[0], args[1])
elif cmd == "keys":
    from pywinauto import keyboard
    pwlib.window().set_focus()
    keyboard.send_keys(args[0])
