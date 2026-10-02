"""FastAPI application factory: request guard, API routers, PWA files, static SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .api import ROUTERS
from .config import Config
from .services import Services
from funes_hoard.hoard_link.guard import install_guard
from funes_hoard.hoard_link.service import install_error_handlers, install_spa

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: Config | None = None, link_provider=None, emit=None) -> FastAPI:
    """`link_provider` returns the host app's Hoard Link, used to write meeting minutes; `emit` overrides family.emit (tests)."""
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = Services(config, link_provider=link_provider, emit=emit)
        app.state.services = services
        services.start()
        logging.getLogger("funes_hoard.audio_memory").info("Funes audio %s — data in %s", __version__, config.data_dir)
        try:
            yield
        finally:
            services.stop()

    app = FastAPI(title="Funes Audio", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config
    # This module runs inside Funes and shares its family bus identity.

    install_guard(app, port_getter=lambda: config.port, allowed_env="SCRIBE_ALLOWED_HOSTS", allowed_hosts=config.allowed_hosts)
    install_error_handlers(app)

    for router in ROUTERS:
        app.include_router(router)

    install_spa(app, STATIC_DIR)

    return app
