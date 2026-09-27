"""API routers."""

from .agent import router as agent_router
from .pwa import router as pwa_router
from .search import router as search_router
from .sessions import import_router, router as sessions_router
from .status import router as status_router

ROUTERS = [status_router, sessions_router, import_router, search_router, pwa_router, agent_router]
