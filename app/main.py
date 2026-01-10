import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import setup_logging
from app.database import db
from app.event_handlers import register_event_handlers
from app.radio import radio_manager
from app.radio_sync import (
    start_periodic_sync,
    stop_periodic_sync,
    sync_and_offload_all,
)
from app.routers import channels, contacts, health, messages, packets, radio, settings, ws

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database and radio connection lifecycle."""
    await db.connect()
    logger.info("Database connected")

    try:
        await radio_manager.connect()
        logger.info("[STARTUP] Connected to radio at %s", radio_manager.port)
        if radio_manager.meshcore:
            logger.info("[STARTUP] Registering event handlers for message reception...")
            register_event_handlers(radio_manager.meshcore)

            # Sync contacts/channels from radio to DB and clear radio
            logger.info("[STARTUP] Syncing and offloading radio data...")
            result = await sync_and_offload_all()
            logger.info("[STARTUP] Sync complete: %s", result)

            # Start periodic sync
            start_periodic_sync()

            # Send advertisement to announce our presence
            logger.info("[STARTUP] Sending startup advertisement...")
            advert_result = await radio_manager.meshcore.commands.send_advert(flood=True)
            logger.info("[STARTUP] Advertisement sent: %s", advert_result.type)

            logger.info("[STARTUP] Starting auto message fetching...")
            await radio_manager.meshcore.start_auto_message_fetching()
            logger.info("[STARTUP] Auto message fetching started - ready to receive messages")
    except Exception as e:
        logger.warning("Failed to connect to radio on startup: %s", e)

    # Always start connection monitor (even if initial connection failed)
    await radio_manager.start_connection_monitor()

    yield

    logger.info("Shutting down")
    await radio_manager.stop_connection_monitor()
    stop_periodic_sync()
    if radio_manager.meshcore:
        await radio_manager.meshcore.stop_auto_message_fetching()
    await radio_manager.disconnect()
    await db.disconnect()


app = FastAPI(
    title="RemoteTerm for MeshCore API",
    description="API for interacting with MeshCore mesh radio networks",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes - all prefixed with /api for production compatibility
app.include_router(health.router, prefix="/api")
app.include_router(radio.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(packets.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(ws.router, prefix="/api")

# Serve frontend static files in production
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    # Serve static assets (JS, CSS, etc.)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    # Serve other static files from frontend/dist (like wordlist)
    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve frontend files, falling back to index.html for SPA routing."""
        file_path = FRONTEND_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Fall back to index.html for SPA routing
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/")
    async def serve_index():
        """Serve the frontend index.html."""
        return FileResponse(FRONTEND_DIR / "index.html")
