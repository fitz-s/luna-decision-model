"""Credential-free Claude Code MCP adapter for the local decide-d service."""
import json
import os
import math
from typing import Annotated
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from .client import request, report_diagnostics, DecisionError
from .local import loopback_url

URL = loopback_url(os.environ.get("DECIDE_D_URL", "http://127.0.0.1:20141/v1/systemone"), paths=('/v1/systemone',))
TIMEOUT = float(os.environ.get("DECIDE_D_CLIENT_TIMEOUT", "30"))
if not math.isfinite(TIMEOUT) or TIMEOUT <= 0:
    raise ValueError('DECIDE_D_CLIENT_TIMEOUT must be finite and positive')
mcp = FastMCP("Local Luna Decisions")


@mcp.tool(description="Ask subscription-backed Luna typed noul, choice or score questions. This local compatibility service returns model-estimated probabilities; inspect its backend and confidence before acting.",
          annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True))
async def jev_decide(
    state: Annotated[str | dict | list, Field(description='Evidence passed unchanged to ordinary Luna.')],
    questions: Annotated[dict, Field(description='Map question IDs to Jev objects: type noul with optional instructions/true criterion; choice with criteria mapping outcome labels to descriptions; score with criteria list. Original labels and order are preserved.')],
) -> dict:
    if not questions: raise ValueError("questions must be nonempty")
    try:
        result = await request({"model": "gpt-6-luna", "state": state, "questions": questions},
                               url=URL.removesuffix("/v1/systemone").rstrip("/"), timeout=TIMEOUT)
    except DecisionError as error:
        raise ValueError(json.dumps({"error": error.error, **error.accounting, "headers": error.headers})) from None
    report_diagnostics(result)
    return result


if __name__ == "__main__": mcp.run()
