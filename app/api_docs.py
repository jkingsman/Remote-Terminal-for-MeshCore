"""Custom OpenAPI documentation page for the RemoteTerm API."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

API_DESCRIPTION = (
    "RemoteTerm exposes the MeshCore companion radio as a local REST and WebSocket API.\n\n"
    "REST endpoints are mounted below `/api`. The live WebSocket stream is available at "
    "`/api/ws` for health, message, raw-packet, contact, and telemetry events.\n\n"
    "**Trusted network note:** RemoteTerm is designed for trusted local networks. Optional "
    "HTTP Basic auth can be enabled for the whole app, but operators should pair it with "
    "HTTPS when credentials cross the network."
)

API_TAGS_METADATA: list[dict[str, Any]] = [
    {
        "name": "health",
        "description": "Connection state, build info, database size, radio stats, and fanout health.",
    },
    {
        "name": "debug",
        "description": "Support snapshots for logs, live radio probes, drift audits, and version data.",
    },
    {
        "name": "radio",
        "description": "Radio configuration, connection lifecycle, discovery, trace, and advert commands.",
    },
    {
        "name": "contacts",
        "description": "Mesh contacts, analytics, read state, route overrides, and path discovery.",
    },
    {
        "name": "repeaters",
        "description": "Repeater login, telemetry, ACL, owner info, radio settings, and CLI commands.",
    },
    {
        "name": "rooms",
        "description": "Room-server login, status, telemetry, and ACL operations.",
    },
    {
        "name": "channels",
        "description": "Channel creation, metadata, read state, flood scope, and path-hash overrides.",
    },
    {
        "name": "messages",
        "description": "Message history, direct sends, channel sends, and channel resend workflows.",
    },
    {
        "name": "packets",
        "description": "Raw packet inspection, historical decryption, undecrypted counts, and maintenance.",
    },
    {
        "name": "read-state",
        "description": "Server-side unread counters, mention flags, and mark-all-read operations.",
    },
    {
        "name": "settings",
        "description": "App settings, favorites, muted channels, block lists, and telemetry tracking.",
    },
    {
        "name": "push",
        "description": "Browser Web Push subscriptions, per-device preferences, tests, and conversations.",
    },
    {
        "name": "fanout",
        "description": "MQTT, webhooks, Apprise, SQS, Home Assistant, map upload, and Discord/Telegram bridge integrations.",
    },
    {
        "name": "bots",
        "description": "The Bots workspace: DB-stored Python bots with keyword/cron/event/webhook triggers, scheduled messages, feeds, engine settings, logs, and run statistics.",
    },
    {
        "name": "statistics",
        "description": "Aggregated mesh, message, packet, channel, and contact statistics.",
    },
]

SWAGGER_UI_CSS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
SWAGGER_UI_BUNDLE_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
SWAGGER_UI_PRESET_URL = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"
)

COMMON_ERROR_RESPONSES: dict[str, str] = {
    "400": "Bad request",
    "401": "Authentication required",
    "403": "Forbidden",
    "404": "Not found",
    "408": "Request timed out",
    "409": "Conflict",
    "422": "Validation or command error",
    "423": "Radio unavailable or locked",
    "500": "Server error",
}

ERROR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "detail": {
            "description": "Human-readable error detail or structured validation detail.",
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {}},
                {"type": "object", "additionalProperties": True},
            ],
        }
    },
}


def _relative_openapi_url(app: FastAPI) -> str:
    """Keep docs usable behind reverse-proxy path prefixes."""
    openapi_url = app.openapi_url or "/openapi.json"
    return openapi_url.lstrip("/")


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": ERROR_RESPONSE_SCHEMA,
            }
        },
    }


def _add_common_error_responses(openapi_schema: dict[str, Any]) -> None:
    paths = openapi_schema.get("paths")
    if not isinstance(paths, dict):
        return

    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            if not isinstance(responses, dict):
                continue
            for status_code, description in COMMON_ERROR_RESPONSES.items():
                responses.setdefault(status_code, _error_response(description))


def install_custom_openapi(app: FastAPI) -> None:
    """Install OpenAPI metadata polishing used by the custom docs page."""
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
        )
        _add_common_error_responses(openapi_schema)
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    # Keep a reference for debuggability and for tests that may introspect it.
    app.state.default_openapi = original_openapi
    app.openapi = custom_openapi  # type: ignore[method-assign]


def _build_swagger_docs_html(app: FastAPI) -> str:
    title = escape(app.title)
    version = escape(app.version)
    openapi_url = json.dumps(_relative_openapi_url(app))

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{title}</title>
    <link rel="stylesheet" href="{SWAGGER_UI_CSS_URL}">
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23111419'/%3E%3Cpath d='M16 42h32M18 42l8-20 6 14 6-14 8 20' fill='none' stroke='%2374d99f' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='26' cy='22' r='4' fill='%23f5c15c'/%3E%3Ccircle cx='38' cy='22' r='4' fill='%2386b7ff'/%3E%3C/svg%3E">
    <style>
      :root {{
        --docs-ink: #111419;
        --docs-muted: #5b6674;
        --docs-panel: #ffffff;
        --docs-border: #d8dee7;
        --docs-page: #f4f7f6;
        --docs-green: #2f9d69;
        --docs-amber: #b7791f;
        --docs-blue: #286fc2;
        --docs-red: #c2413b;
      }}

      * {{
        box-sizing: border-box;
      }}

      body {{
        margin: 0;
        background: var(--docs-page);
        color: var(--docs-ink);
        font-family:
          Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
          sans-serif;
      }}

      .docs-hero {{
        color: #ffffff;
        background: #141a1d;
        border-bottom: 1px solid rgba(255, 255, 255, 0.14);
      }}

      .docs-hero-inner {{
        width: min(1280px, calc(100% - 40px));
        margin: 0 auto;
        padding: 32px 0 28px;
      }}

      .docs-kicker {{
        margin: 0 0 10px;
        color: #74d99f;
        font-size: 0.78rem;
        font-weight: 800;
        letter-spacing: 0;
        text-transform: uppercase;
      }}

      .docs-hero h1 {{
        margin: 0;
        font-size: 3.2rem;
        line-height: 1.04;
        letter-spacing: 0;
      }}

      .docs-lede {{
        max-width: 780px;
        margin: 14px 0 0;
        color: rgba(255, 255, 255, 0.82);
        font-size: 1.04rem;
        line-height: 1.55;
      }}

      .docs-actions {{
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 22px;
      }}

      .docs-action {{
        display: inline-flex;
        align-items: center;
        min-height: 38px;
        padding: 0 13px;
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 8px;
        color: #ffffff;
        background: rgba(255, 255, 255, 0.08);
        font-size: 0.9rem;
        font-weight: 700;
        text-decoration: none;
      }}

      .docs-action:hover {{
        background: rgba(255, 255, 255, 0.15);
      }}

      .docs-action strong {{
        color: #f5c15c;
        font-weight: 800;
      }}

      .docs-meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 22px;
      }}

      .docs-pill {{
        display: inline-flex;
        align-items: center;
        min-height: 28px;
        padding: 0 10px;
        border: 1px solid rgba(255, 255, 255, 0.2);
        border-radius: 999px;
        color: rgba(255, 255, 255, 0.84);
        background: rgba(255, 255, 255, 0.08);
        font-size: 0.78rem;
        font-weight: 700;
        white-space: nowrap;
      }}

      .docs-pill span {{
        margin-left: 6px;
        color: #ffffff;
        font-weight: 800;
      }}

      .swagger-ui {{
        width: min(1280px, calc(100% - 40px));
        margin: 24px auto 56px;
      }}

      .swagger-ui .topbar {{
        display: none;
      }}

      .swagger-ui .information-container.wrapper,
      .swagger-ui .scheme-container,
      .swagger-ui .opblock-tag-section,
      .swagger-ui .models {{
        max-width: none;
      }}

      .swagger-ui .info {{
        margin: 0 0 20px;
        padding: 22px 24px;
        border: 1px solid var(--docs-border);
        border-radius: 8px;
        background: var(--docs-panel);
        box-shadow: 0 10px 32px rgba(17, 20, 25, 0.08);
      }}

      .swagger-ui .info .title {{
        color: var(--docs-ink);
        font-size: 1.55rem;
      }}

      .swagger-ui .info p,
      .swagger-ui .info li,
      .swagger-ui .markdown p {{
        color: var(--docs-muted);
        line-height: 1.55;
      }}

      .swagger-ui .scheme-container {{
        margin: 0 0 20px;
        padding: 16px 18px;
        border: 1px solid var(--docs-border);
        border-radius: 8px;
        background: var(--docs-panel);
        box-shadow: 0 10px 32px rgba(17, 20, 25, 0.06);
      }}

      .swagger-ui .opblock-tag {{
        margin: 18px 0 10px;
        padding: 14px 18px;
        border: 1px solid var(--docs-border);
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.92);
        color: var(--docs-ink);
        box-shadow: 0 8px 22px rgba(17, 20, 25, 0.05);
      }}

      .swagger-ui .opblock {{
        overflow: hidden;
        border-radius: 8px;
        box-shadow: 0 8px 22px rgba(17, 20, 25, 0.05);
      }}

      .swagger-ui .opblock.opblock-get {{
        border-color: rgba(40, 111, 194, 0.42);
        background: rgba(40, 111, 194, 0.06);
      }}

      .swagger-ui .opblock.opblock-post {{
        border-color: rgba(47, 157, 105, 0.42);
        background: rgba(47, 157, 105, 0.06);
      }}

      .swagger-ui .opblock.opblock-put,
      .swagger-ui .opblock.opblock-patch {{
        border-color: rgba(183, 121, 31, 0.44);
        background: rgba(183, 121, 31, 0.07);
      }}

      .swagger-ui .opblock.opblock-delete {{
        border-color: rgba(194, 65, 59, 0.4);
        background: rgba(194, 65, 59, 0.06);
      }}

      .swagger-ui .opblock .opblock-summary-method {{
        border-radius: 6px;
        min-width: 78px;
        text-shadow: none;
      }}

      .swagger-ui .opblock .opblock-summary-path,
      .swagger-ui .opblock .opblock-summary-path__deprecated {{
        color: var(--docs-ink);
        font-weight: 800;
      }}

      .swagger-ui .btn,
      .swagger-ui select {{
        border-radius: 7px;
      }}

      .swagger-ui input[type="text"],
      .swagger-ui textarea {{
        border-radius: 7px;
      }}

      .swagger-ui .models {{
        border: 1px solid var(--docs-border);
        border-radius: 8px;
        background: var(--docs-panel);
      }}

      @media (max-width: 720px) {{
        .docs-hero-inner,
        .swagger-ui {{
          width: min(100% - 24px, 1280px);
        }}

        .docs-hero-inner {{
          padding: 24px 0 22px;
        }}

        .docs-hero h1 {{
          font-size: 2.05rem;
        }}

        .docs-actions {{
          display: grid;
          grid-template-columns: 1fr;
        }}

        .docs-action {{
          justify-content: center;
          width: 100%;
        }}

        .swagger-ui {{
          margin-top: 16px;
        }}

        .swagger-ui .info,
        .swagger-ui .scheme-container {{
          padding: 16px;
        }}
      }}
    </style>
  </head>
  <body>
    <header class="docs-hero">
      <div class="docs-hero-inner">
        <p class="docs-kicker">RemoteTerm API</p>
        <h1>{title}</h1>
        <p class="docs-lede">
          Explore radio control, messaging, packet inspection, push notifications,
          and fanout integration endpoints from one interactive console.
        </p>
        <div class="docs-actions" aria-label="API shortcuts">
          <a class="docs-action" href="api/health"><strong>GET</strong>&nbsp;/api/health</a>
          <a class="docs-action" href="openapi.json">OpenAPI JSON</a>
          <span class="docs-action">WebSocket /api/ws</span>
        </div>
        <div class="docs-meta" aria-label="API metadata">
          <span class="docs-pill">Version <span>{version}</span></span>
          <span class="docs-pill">REST base <span>/api</span></span>
          <span class="docs-pill">Auth <span>optional Basic</span></span>
        </div>
      </div>
    </header>
    <main id="swagger-ui" aria-label="Swagger UI"></main>
    <script src="{SWAGGER_UI_BUNDLE_URL}" crossorigin></script>
    <script src="{SWAGGER_UI_PRESET_URL}" crossorigin></script>
    <script>
      window.ui = SwaggerUIBundle({{
        url: {openapi_url},
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayRequestDuration: true,
        docExpansion: "none",
        filter: true,
        persistAuthorization: true,
        showExtensions: true,
        showCommonExtensions: true,
        tryItOutEnabled: false,
        withCredentials: true,
        defaultModelsExpandDepth: 1,
        defaultModelExpandDepth: 1,
        syntaxHighlight: {{
          activate: true,
          theme: "nord"
        }},
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        layout: "BaseLayout"
      }});
    </script>
  </body>
</html>
"""


def register_api_docs_routes(app: FastAPI) -> None:
    """Register the custom Swagger UI route."""
    install_custom_openapi(app)

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui_html() -> HTMLResponse:
        return HTMLResponse(_build_swagger_docs_html(app))
