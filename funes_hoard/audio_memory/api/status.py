"""Health, status, devices, settings and model download."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import SERVICE, __version__
from ..settings import SettingsPatch
from .deps import services
from funes_hoard.hoard_link.service import health_router

router = APIRouter(prefix="/api")


def _health_extra(request: Request) -> dict:
    return {"dataDirConfigured": request.app.state.config.data_dir_configured,
            "gpu_lease": services(request).transcriber.info().get("gpu_lease", "disabled")}


health = health_router(SERVICE, __version__, extra=_health_extra)


@router.get("/status")
def status(request: Request):
    return services(request).status()


@router.get("/devices")
def devices(request: Request):
    svc = services(request)
    return {"backend": svc.backend.name, **svc.backend.devices().as_dict()}


@router.get("/settings")
def get_settings(request: Request):
    return services(request).settings.get()


@router.put("/settings")
def put_settings(request: Request, patch: SettingsPatch):
    return services(request).update_settings(patch)


@router.post("/models/download")
def download_model(request: Request):
    svc = services(request)
    svc.preload_model()
    return {"ok": True, "transcriber": svc.transcriber.info(), "queue_depth": svc.worker.depth}
