"""FastAPI application entrypoint for SyncPulse API & Monitoring Server (Process A)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api import events, health, transfers
from backend.config import Settings, get_settings
from backend.database import Database
from backend.services.monitoring_service import MonitoringService
from backend.services.storage_service import StorageService
from backend.services.transfer_service import TransferService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("syncpulse.api")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.ensure_directories()
        db = Database(cfg.db_path)
        db.init()

        storage = StorageService(
            staging_dir=cfg.staging_dir,
            backups_dir=cfg.backups_dir,
            max_file_size_bytes=cfg.max_file_size_bytes,
        )
        monitoring = MonitoringService(settings=cfg, db=db)
        transfer_svc = TransferService(settings=cfg, db=db, storage=storage)

        app.state.settings = cfg
        app.state.db = db
        app.state.storage_service = storage
        app.state.monitoring_service = monitoring
        app.state.transfer_service = transfer_svc

        await monitoring.start()
        await transfer_svc.start()
        logger.info(
            "SyncPulse API started (HTTP %s:%d, TCP target %s:%d, UDP listen %s:%d)",
            cfg.api_host,
            cfg.api_port,
            cfg.tcp_host,
            cfg.tcp_port,
            cfg.udp_host,
            monitoring.bound_udp_port,
        )
        try:
            yield
        finally:
            await transfer_svc.stop()
            await monitoring.stop()
            logger.info("SyncPulse API stopped")

    app = FastAPI(
        title="SyncPulse API",
        description="Self-Hosted File Backup & Infrastructure Monitoring System",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(transfers.router)
    app.include_router(events.router)

    # Serve built Vite production assets when available so only 2 processes are needed for demos
    if FRONTEND_DIST_DIR.exists() and (FRONTEND_DIST_DIR / "index.html").exists():
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            return FileResponse(FRONTEND_DIST_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            candidate = FRONTEND_DIST_DIR / full_path
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST_DIR / "index.html")

    return app


app = create_app()
