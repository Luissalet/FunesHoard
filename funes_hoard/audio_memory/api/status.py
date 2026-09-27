"""Health, status, devices, settings and model download."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import SERVICE, __version__
from ..settings import SettingsPatch
from .deps import services
from funes_hoard.hoard_link import family

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    transcriber_info = services(request).transcriber.info()
    return {
        "service": SERVICE,
        "version": __version__,
        "dataDirConfigured": request.app.state.config.data_dir_configured,
        "hoard_link": family.health_block(),
        "gpu_lease": transcriber_info.get("gpu_lease", "disabled"),
    }


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
