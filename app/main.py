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
    drain_pending_messages,
    start_message_polling,
    start_periodic_sync,
    stop_message_polling,
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
        logger.info("Connected to radio")
        if radio_manager.meshcore:
            register_event_handlers(radio_manager.meshcore)

            # Sync contacts/channels from radio to DB and clear radio
            logger.info("Syncing and offloading radio data...")
            result = await sync_and_offload_all()
            logger.info("Sync complete: %s", result)

            # Start periodic sync
            start_periodic_sync()

            # Send advertisement to announce our presence
            logger.info("Sending startup advertisement...")
            advert_result = await radio_manager.meshcore.commands.send_advert(flood=True)
            logger.info("Advertisement sent: %s", advert_result.type)

            await radio_manager.meshcore.start_auto_message_fetching()
            logger.info("Auto message fetching started")

            # Drain any messages that were queued before we connected
            drained = await drain_pending_messages()
            if drained > 0:
                logger.info("Drained %d pending message(s)", drained)

            # Start periodic message polling as fallback for unreliable push events
            start_message_polling()
    except Exception as e:
        logger.warning("Failed to connect to radio on startup: %s", e)

    # Always start connection monitor (even if initial connection failed)
    await radio_manager.start_connection_monitor()

    yield

    logger.info("Shutting down")
    await radio_manager.stop_connection_monitor()
    stop_message_polling()
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
