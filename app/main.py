import logging
import sys

# ---------------------------------------------------------------------------
# Windows event-loop advisory for MQTT fanout
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import asyncio as _asyncio

    _loop = _asyncio.get_event_loop()
    _is_proactor = type(_loop).__name__ == "ProactorEventLoop"
    if _is_proactor:
        print(
            "\n" + "!" * 78 + "\n"
            "  NOTE FOR WINDOWS USERS\n" + "!" * 78 + "\n"
            "\n"
            "  The running event loop is ProactorEventLoop, which is not\n"
            "  compatible with MQTT fanout (aiomqtt / paho-mqtt).\n"
            "\n"
            "  If you use MQTT integrations, restart with --loop none:\n"
            "\n"
            "    uv run uvicorn app.main:app \033[1m--loop none\033[0m"
            " [... other options ...]\n"
            "\n"
            "  Everything else works fine as-is.\n"
            "\n" + "!" * 78 + "\n",
            file=sys.stderr,
            flush=True,
        )
    del _loop, _is_proactor

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api_docs import API_DESCRIPTION, API_TAGS_METADATA, register_api_docs_routes
from app.config import settings as server_settings
from app.config import setup_logging
from app.database import db
from app.frontend_static import (
    register_first_available_frontend_static_routes,
    register_frontend_missing_fallback,
)
from app.radio import RadioDisconnectedError
from app.radio_sync import (
    stop_background_contact_reconciliation,
    stop_message_polling,
    stop_periodic_advert,
    stop_periodic_sync,
    stop_telemetry_collect,
)
from app.routers import (
    channels,
    contacts,
    debug,
    fanout,
    health,
    messages,
    packets,
    push,
    radio,
    read_state,
    repeaters,
    rooms,
    settings,
    statistics,
    ws,
)
from app.security import add_optional_basic_auth_middleware
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.radio_stats import start_radio_stats_sampling, stop_radio_stats_sampling
from app.version_info import get_app_build_info

setup_logging()
logger = logging.getLogger(__name__)


async def _startup_radio_connect_and_setup() -> None:
    """Connect/setup the radio in the background so HTTP serving can start immediately."""
    try:
        connected = await radio_manager.reconnect_and_prepare(broadcast_on_success=True)
        if connected:
            logger.info("Connected to radio")
        else:
            logger.warning("Failed to connect to radio on startup")
    except Exception:
        logger.exception("Failed to connect to radio on startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database and radio connection lifecycle."""
    await db.connect()
    logger.info("Database connected")

    # Initialize MCMP compressor model (non-fatal if missing)
    from app.mcmp.compressor import MeshCompressor

    try:
        await MeshCompressor.instance.initialize()
        if MeshCompressor.instance.is_ready:
            logger.info("MCMP compressor model loaded")
        else:
            logger.warning("MCMP compressor model not loaded; mcmp3 encoding will be disabled")
    except Exception:
        logger.exception("Failed to initialize MCMP compressor")

    # Initialize VAPID keys for Web Push (generates on first run)
    from app.push.vapid import ensure_vapid_keys

    try:
        await ensure_vapid_keys()
    except Exception:
        logger.warning("Failed to initialize VAPID keys for Web Push", exc_info=True)

    # Ensure default channels exist in the database even before the radio
    # connects. Without this, a fresh or disconnected instance would return
    # zero channels from GET /channels until the first successful radio sync.
    from app.radio_sync import ensure_default_channels

    await ensure_default_channels()
    await start_radio_stats_sampling()

    # Always start connection monitor (even if initial connection failed)
    await radio_manager.start_connection_monitor()

    # Start fanout modules (MQTT, etc.) from database configs
    from app.fanout.manager import fanout_manager

    try:
        await fanout_manager.load_from_db()
    except Exception:
        logger.exception("Failed to start fanout modules")

    startup_radio_task = asyncio.create_task(_startup_radio_connect_and_setup())
    app.state.startup_radio_task = startup_radio_task

    yield

    logger.info("Shutting down")
    if startup_radio_task and not startup_radio_task.done():
        startup_radio_task.cancel()
        try:
            await startup_radio_task
        except asyncio.CancelledError:
            pass
    await fanout_manager.stop_all()
    await radio_manager.stop_connection_monitor()
    await stop_background_contact_reconciliation()
    await stop_message_polling()
    await stop_radio_stats_sampling()
    await stop_periodic_advert()
    await stop_periodic_sync()
    await stop_telemetry_collect()
    if radio_manager.meshcore:
        await radio_manager.meshcore.stop_auto_message_fetching()
    await radio_manager.disconnect()
    await db.disconnect()


app = FastAPI(
    title="RemoteTerm for MeshCore API",
    description=API_DESCRIPTION,
    version=get_app_build_info().version,
    openapi_tags=API_TAGS_METADATA,
    docs_url=None,
    lifespan=lifespan,
)

register_api_docs_routes(app)
add_optional_basic_auth_middleware(app, server_settings)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RadioDisconnectedError)
async def radio_disconnected_handler(request: Request, exc: RadioDisconnectedError):
    """Return 423 when a radio disconnect race occurs during an operation."""
    return JSONResponse(status_code=423, content={"detail": "Radio not connected"})


@app.middleware("http")
async def log_server_errors(request: Request, call_next):
    """Capture 5xx errors and unhandled exceptions into the log ring buffer."""
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise
    if response.status_code >= 500:
        logger.error("HTTP %d on %s %s", response.status_code, request.method, request.url.path)
    return response


# API routes - all prefixed with /api for production compatibility
app.include_router(health.router, prefix="/api")
app.include_router(debug.router, prefix="/api")
app.include_router(fanout.router, prefix="/api")
app.include_router(radio.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(repeaters.router, prefix="/api")
app.include_router(rooms.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(packets.router, prefix="/api")
app.include_router(read_state.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(statistics.router, prefix="/api")
app.include_router(push.router, prefix="/api")
app.include_router(ws.router, prefix="/api")

# Serve frontend static files in production
FRONTEND_DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"
FRONTEND_PREBUILT_DIR = Path(__file__).parent.parent / "frontend" / "prebuilt"
if not register_first_available_frontend_static_routes(
    app, [FRONTEND_DIST_DIR, FRONTEND_PREBUILT_DIR]
):
    register_frontend_missing_fallback(app)