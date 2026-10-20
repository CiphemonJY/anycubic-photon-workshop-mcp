"""Start server.py over stdio as an MCP client would, list its tools, call pw_status."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


async def main():
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "server.py")], cwd=HERE)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("pw_status", {})
            print(res.content[0].text[:400])
            if len(sys.argv) > 1:                       # optional: a sliced file to check
                res = await s.call_tool("pw_check_slice", {"path": sys.argv[1]})
                print(res.content[0].text[:1500])


asyncio.run(main())
