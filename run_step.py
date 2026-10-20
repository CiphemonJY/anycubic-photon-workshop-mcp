"""Call one server tool directly (no MCP transport): python run_step.py TOOL '{json kwargs}'"""
import json
import sys

import server

fn = getattr(server, sys.argv[1])
kw = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
out = fn(**kw)
print(out if isinstance(out, str) else repr(out))
