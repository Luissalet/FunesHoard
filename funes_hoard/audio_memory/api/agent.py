"""/api/agent/* — the bridge used by mcp_server.py (Bearer token from <DATA_DIR>/mcp-token). The routes come from the shared agent kit."""

from __future__ import annotations

from fastapi import Request
from pydantic import ValidationError

from funes_hoard.hoard_link.agentkit import AppError, UnknownTool, make_agent_router

from ..agent_tools import AGENT_INSTRUCTIONS, call_tool, tool_catalog
from ..stt_jobs import NoModel
from .deps import services


def _call(name: str, arguments: dict, request: Request):
    """Run a tool and give its failures the codes the family contract names: a missing thing is 404, a service that cannot run is 503."""
    try:
        return call_tool(services(request), name, arguments)
    except (UnknownTool, ValidationError):
        raise  # the router answers 404 unknown_tool and 400 invalid_arguments itself
    except NoModel as error:
        raise AppError("no_model", str(error), status=503) from error
    except LookupError as error:
        raise AppError("not_found", str(error.args[0]) if error.args else "Not found.") from error
    except RuntimeError as error:
        raise AppError("unavailable", str(error)) from error


router = make_agent_router(
    tools_fn=tool_catalog,
    call_fn=_call,
    token_fn=lambda request: services(request).token,
    instructions=AGENT_INSTRUCTIONS,
    app_name="funes-audio",
)
