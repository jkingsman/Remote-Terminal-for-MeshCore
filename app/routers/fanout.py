"""REST API for fanout config CRUD."""

import ast
import inspect
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.fanout.bot_exec import _analyze_bot_signature
from app.fanout.manager import fanout_manager
from app.repository.fanout import FanoutConfigRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fanout", tags=["fanout"])

_VALID_TYPES = {
    "mqtt_private",
    "mqtt_community",
    "mqtt_ha",
    "bot",
    "webhook",
    "apprise",
    "sqs",
    "map_upload",
}

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/packets"
_DEFAULT_COMMUNITY_NEIGHBOR_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/neighbors"
_DEFAULT_COMMUNITY_MQTT_BROKER_HOST = "mqtt-us-v1.letsmesh.net"
_DEFAULT_COMMUNITY_MQTT_BROKER_PORT = 443
_DEFAULT_COMMUNITY_MQTT_TRANSPORT = "websockets"
_DEFAULT_COMMUNITY_MQTT_AUTH_MODE = "token"
_ALLOWED_COMMUNITY_MQTT_TRANSPORTS = {"tcp", "websockets"}
_ALLOWED_COMMUNITY_MQTT_AUTH_MODES = {"token", "password", "none"}


def _normalize_community_topic_template(topic_template: str) -> str:
    """Validate packet topic templates through the runtime renderer contract."""
    from app.fanout.mqtt_community import _normalize_topic_template

    try:
        return _normalize_topic_template(topic_template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid topic_template: {exc}") from None


def _normalize_community_neighbor_topic_template(topic_template: str) -> str:
    """Validate neighbor topic templates through the runtime renderer contract."""
    from app.fanout.mqtt_community import _normalize_neighbor_topic_template

    try:
        return _normalize_neighbor_topic_template(topic_template)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid neighbor_topic_template: {exc}"
        ) from None


class FanoutConfigCreate(BaseModel):
    type: str = Field(description="Integration type: 'mqtt_private' or 'mqtt_community'")
    name: str = Field(min_length=1, description="User-assigned label")
    config: dict = Field(default_factory=dict, description="Type-specific config blob")
    scope: dict = Field(default_factory=dict, description="Scope controls")
    enabled: bool = Field(default=True, description="Whether enabled on creation")


class FanoutConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, description="Updated label")
    config: dict | None = Field(default=None, description="Updated config blob")
    scope: dict | None = Field(default=None, description="Updated scope controls")
    enabled: bool | None = Field(default=None, description="Enable/disable toggle")


def _validate_and_normalize_config(config_type: str, config: dict) -> dict:
    """Validate a config blob and return the canonical persisted form."""
    normalized = dict(config)

    if config_type == "mqtt_private":
        _validate_mqtt_private_config(normalized)
    elif config_type == "mqtt_community":
        _validate_mqtt_community_config(normalized)
    elif config_type == "bot":
        _validate_bot_config(normalized)
    elif config_type == "webhook":
        _validate_webhook_config(normalized)
    elif config_type == "apprise":
        _validate_apprise_config(normalized)
    elif config_type == "sqs":
        _validate_sqs_config(normalized)
    elif config_type == "map_upload":
        _validate_map_upload_config(normalized)
    elif config_type == "mqtt_ha":
        _validate_mqtt_ha_config(normalized)

    return normalized


def _validate_mqtt_private_config(config: dict) -> None:
    """Validate mqtt_private config blob."""
    if not config.get("broker_host"):
        raise HTTPException(status_code=400, detail="broker_host is required for mqtt_private")
    port = config.get("broker_port", 1883)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")


def _validate_mqtt_community_config(config: dict) -> None:
    """Validate mqtt_community config blob. Normalizes IATA to uppercase."""
    broker_host = str(config.get("broker_host", _DEFAULT_COMMUNITY_MQTT_BROKER_HOST)).strip()
    if not broker_host:
        broker_host = _DEFAULT_COMMUNITY_MQTT_BROKER_HOST
    config["broker_host"] = broker_host

    port = config.get("broker_port", _DEFAULT_COMMUNITY_MQTT_BROKER_PORT)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")
    config["broker_port"] = port

    transport = str(config.get("transport", _DEFAULT_COMMUNITY_MQTT_TRANSPORT)).strip().lower()
    if transport not in _ALLOWED_COMMUNITY_MQTT_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail="transport must be 'websockets' or 'tcp'",
        )
    config["transport"] = transport
    config["use_tls"] = bool(config.get("use_tls", True))
    config["tls_verify"] = bool(config.get("tls_verify", True))

    auth_mode = str(config.get("auth_mode", _DEFAULT_COMMUNITY_MQTT_AUTH_MODE)).strip().lower()
    if auth_mode not in _ALLOWED_COMMUNITY_MQTT_AUTH_MODES:
        raise HTTPException(
            status_code=400,
            detail="auth_mode must be 'token', 'password', or 'none'",
        )
    config["auth_mode"] = auth_mode
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if auth_mode == "password" and (not username or not password):
        raise HTTPException(
            status_code=400,
            detail="username and password are required when auth_mode is 'password'",
        )
    config["username"] = username
    config["password"] = password

    token_audience = str(config.get("token_audience", "")).strip()
    config["token_audience"] = token_audience

    raw_iata = config.get("iata", "")
    if not isinstance(raw_iata, str):
        raise HTTPException(status_code=400, detail="IATA code must be a string")
    iata = raw_iata.upper().strip()
    if not iata or not _IATA_RE.fullmatch(iata):
        raise HTTPException(
            status_code=400,
            detail="IATA code is required and must be exactly 3 uppercase alphabetic characters",
        )
    config["iata"] = iata

    topic_template = str(
        config.get("topic_template", _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE)
    ).strip()
    if not topic_template:
        topic_template = _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE

    config["topic_template"] = _normalize_community_topic_template(topic_template)

    # Neighbor reports share the same radio-side coordinator across every
    # Community MQTT broker.  The configuration is stored per broker because
    # topic/retain policy is slot-specific; the coordinator freezes one
    # snapshot and publishes that exact document to all participating slots.
    reporting_enabled = config.get("neighbor_reporting_enabled", False)
    if not isinstance(reporting_enabled, bool):
        raise HTTPException(status_code=400, detail="neighbor_reporting_enabled must be a boolean")
    config["neighbor_reporting_enabled"] = reporting_enabled

    interval_hours = config.get("neighbor_reporting_interval_hours", 24)
    if isinstance(interval_hours, bool) or not isinstance(interval_hours, int):
        raise HTTPException(
            status_code=400,
            detail="neighbor_reporting_interval_hours must be an integer between 12 and 336",
        )
    if not 12 <= interval_hours <= 336:
        raise HTTPException(
            status_code=400,
            detail="neighbor_reporting_interval_hours must be between 12 and 336",
        )
    config["neighbor_reporting_interval_hours"] = interval_hours

    origin = config.get("neighbor_origin", "")
    if not isinstance(origin, str):
        raise HTTPException(status_code=400, detail="neighbor_origin must be a string")
    config["neighbor_origin"] = origin.strip()

    try:
        from app.fanout.community_neighbors import normalize_self_scopes

        config["neighbor_self_scopes"] = normalize_self_scopes(
            config.get("neighbor_self_scopes", "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    neighbor_topic_template = config.get(
        "neighbor_topic_template", _DEFAULT_COMMUNITY_NEIGHBOR_TOPIC_TEMPLATE
    )
    if not isinstance(neighbor_topic_template, str):
        raise HTTPException(status_code=400, detail="neighbor_topic_template must be a string")
    config["neighbor_topic_template"] = _normalize_community_neighbor_topic_template(
        neighbor_topic_template
    )

    retain = config.get("neighbor_retain", False)
    if not isinstance(retain, bool):
        raise HTTPException(status_code=400, detail="neighbor_retain must be a boolean")
    config["neighbor_retain"] = retain


def _validate_bot_config(config: dict) -> None:
    """Validate bot config blob (syntax-check the code and supported signature)."""
    code = config.get("code", "")
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="Bot code cannot be empty")
    try:
        tree = ast.parse(code, filename="<bot_code>", mode="exec")
    except SyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Bot code has syntax error at line {e.lineno}: {e.msg}",
        ) from None

    bot_def = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "bot"
        ),
        None,
    )
    if bot_def is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Bot code must define a callable bot() function. "
                "Use the default bot template as a reference."
            ),
        )

    try:
        parameters: list[inspect.Parameter] = []
        positional_args = [
            *((arg, inspect.Parameter.POSITIONAL_ONLY) for arg in bot_def.args.posonlyargs),
            *((arg, inspect.Parameter.POSITIONAL_OR_KEYWORD) for arg in bot_def.args.args),
        ]
        positional_defaults_start = len(positional_args) - len(bot_def.args.defaults)
        sentinel_default = object()

        for index, (arg, kind) in enumerate(positional_args):
            has_default = index >= positional_defaults_start
            parameters.append(
                inspect.Parameter(
                    arg.arg,
                    kind=kind,
                    default=sentinel_default if has_default else inspect.Parameter.empty,
                )
            )
        if bot_def.args.vararg is not None:
            parameters.append(
                inspect.Parameter(bot_def.args.vararg.arg, kind=inspect.Parameter.VAR_POSITIONAL)
            )
        for kwonly_arg, kw_default in zip(
            bot_def.args.kwonlyargs, bot_def.args.kw_defaults, strict=True
        ):
            parameters.append(
                inspect.Parameter(
                    kwonly_arg.arg,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=(
                        sentinel_default if kw_default is not None else inspect.Parameter.empty
                    ),
                )
            )
        if bot_def.args.kwarg is not None:
            parameters.append(
                inspect.Parameter(bot_def.args.kwarg.arg, kind=inspect.Parameter.VAR_KEYWORD)
            )

        _analyze_bot_signature(inspect.Signature(parameters))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _validate_apprise_config(config: dict) -> None:
    """Validate apprise config blob."""
    urls = config.get("urls", "")
    if not urls or not urls.strip():
        raise HTTPException(status_code=400, detail="At least one Apprise URL is required")

    from app.fanout.apprise_mod import FORMAT_VARIABLES, _apply_format

    dummy_vars: dict[str, str] = dict.fromkeys(FORMAT_VARIABLES, "test")
    for field in ("body_format_dm", "body_format_channel"):
        value = config.get(field)
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{field} must be a string")
        if isinstance(value, str) and value.strip():
            try:
                _apply_format(value, dummy_vars)
            except Exception:
                raise HTTPException(
                    status_code=400, detail=f"Invalid format string in {field}"
                ) from None

    config["markdown_format"] = bool(config.get("markdown_format", True))
    config["include_outgoing"] = bool(config.get("include_outgoing", False))


def _validate_webhook_config(config: dict) -> None:
    """Validate webhook config blob."""
    url = config.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required for webhook")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    method = config.get("method", "POST").upper()
    if method not in ("POST", "PUT", "PATCH"):
        raise HTTPException(status_code=400, detail="method must be POST, PUT, or PATCH")
    headers = config.get("headers", {})
    if not isinstance(headers, dict):
        raise HTTPException(status_code=400, detail="headers must be a JSON object")


def _validate_sqs_config(config: dict) -> None:
    """Validate sqs config blob."""
    queue_url = str(config.get("queue_url", "")).strip()
    if not queue_url:
        raise HTTPException(status_code=400, detail="queue_url is required for sqs")
    if not queue_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="queue_url must start with http:// or https://")

    endpoint_url = str(config.get("endpoint_url", "")).strip()
    if endpoint_url and not endpoint_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=400,
            detail="endpoint_url must start with http:// or https://",
        )

    access_key_id = str(config.get("access_key_id", "")).strip()
    secret_access_key = str(config.get("secret_access_key", "")).strip()
    session_token = str(config.get("session_token", "")).strip()
    has_static_keypair = bool(access_key_id) and bool(secret_access_key)
    has_partial_keypair = bool(access_key_id) != bool(secret_access_key)

    if has_partial_keypair:
        raise HTTPException(
            status_code=400,
            detail="access_key_id and secret_access_key must be set together for sqs",
        )
    if session_token and not has_static_keypair:
        raise HTTPException(
            status_code=400,
            detail="session_token requires access_key_id and secret_access_key for sqs",
        )


def _validate_map_upload_config(config: dict) -> None:
    """Validate and normalize map_upload config blob."""
    api_url = str(config.get("api_url", "")).strip()
    if api_url and not api_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="api_url must start with http:// or https://",
        )
    # Persist the cleaned value (empty string means use the module default)
    config["api_url"] = api_url
    config["dry_run"] = bool(config.get("dry_run", True))
    config["geofence_enabled"] = bool(config.get("geofence_enabled", False))
    try:
        radius = float(config.get("geofence_radius_km", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="geofence_radius_km must be a number") from None
    if radius < 0:
        raise HTTPException(status_code=400, detail="geofence_radius_km must be >= 0")
    config["geofence_radius_km"] = radius


def _validate_mqtt_ha_config(config: dict) -> None:
    """Validate mqtt_ha config blob."""
    if not config.get("broker_host"):
        raise HTTPException(status_code=400, detail="broker_host is required for mqtt_ha")
    port = config.get("broker_port", 1883)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")
    for field in ("tracked_contacts", "tracked_repeaters"):
        value = config.get(field)
        if value is not None and not isinstance(value, list):
            raise HTTPException(status_code=400, detail=f"{field} must be a list of public keys")


def _enforce_scope(config_type: str, scope: dict) -> dict:
    """Enforce type-specific scope constraints. Returns normalized scope."""
    if config_type == "mqtt_community":
        return {"messages": "none", "raw_packets": "all"}
    if config_type == "map_upload":
        return {"messages": "none", "raw_packets": "all"}
    if config_type == "bot":
        return {"messages": "all", "raw_packets": "none"}
    if config_type in ("webhook", "apprise", "mqtt_ha"):
        messages = scope.get("messages", "all")
        if messages not in ("all", "none") and not isinstance(messages, dict):
            raise HTTPException(
                status_code=400,
                detail="scope.messages must be 'all', 'none', or a filter object",
            )
        return {"messages": messages, "raw_packets": "none"}
    # For mqtt_private and sqs, validate scope values
    messages = scope.get("messages", "all")
    if messages not in ("all", "none") and not isinstance(messages, dict):
        raise HTTPException(
            status_code=400,
            detail="scope.messages must be 'all', 'none', or a filter object",
        )
    raw_packets = scope.get("raw_packets", "all")
    if raw_packets not in ("all", "none"):
        raise HTTPException(
            status_code=400,
            detail="scope.raw_packets must be 'all' or 'none'",
        )
    return {"messages": messages, "raw_packets": raw_packets}


def _bot_system_disabled_detail() -> str | None:
    source = fanout_manager.get_bots_disabled_source()
    if source == "env":
        return "Bot system disabled by server configuration (MESHCORE_DISABLE_BOTS)"
    if source == "until_restart":
        return "Bot system disabled until the server restarts"
    return None


@router.get("")
async def list_fanout_configs() -> list[dict]:
    """List all fanout configs."""
    return await FanoutConfigRepository.get_all()


@router.post("")
async def create_fanout_config(body: FanoutConfigCreate) -> dict:
    """Create a new fanout config."""
    if body.type not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{body.type}'. Must be one of: {', '.join(sorted(_VALID_TYPES))}",
        )

    if body.type == "bot":
        disabled_detail = _bot_system_disabled_detail()
        if disabled_detail:
            raise HTTPException(status_code=403, detail=disabled_detail)

    normalized_config = _validate_and_normalize_config(body.type, body.config)
    scope = _enforce_scope(body.type, body.scope)

    cfg = await FanoutConfigRepository.create(
        config_type=body.type,
        name=body.name,
        config=normalized_config,
        scope=scope,
        enabled=body.enabled,
    )

    # Start the module if enabled
    if cfg["enabled"]:
        await fanout_manager.reload_config(cfg["id"])

    logger.info("Created fanout config %s (type=%s, name=%s)", cfg["id"], body.type, body.name)
    return cfg


@router.patch("/{config_id}")
async def update_fanout_config(config_id: str, body: FanoutConfigUpdate) -> dict:
    """Update a fanout config. Triggers module reload."""
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    if existing["type"] == "bot":
        disabled_detail = _bot_system_disabled_detail()
        if disabled_detail:
            raise HTTPException(status_code=403, detail=disabled_detail)

    kwargs = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if body.scope is not None:
        kwargs["scope"] = _enforce_scope(existing["type"], body.scope)

    config_to_validate = body.config if body.config is not None else existing["config"]
    kwargs["config"] = _validate_and_normalize_config(existing["type"], config_to_validate)

    updated = await FanoutConfigRepository.update(config_id, **kwargs)
    if updated is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Reload the module to pick up changes
    await fanout_manager.reload_config(config_id)

    logger.info("Updated fanout config %s", config_id)
    return updated


async def _require_community_neighbor_config(config_id: str) -> dict:
    """Validate that a live config can use the shared Community MQTT reporter."""
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")
    if existing["type"] != "mqtt_community":
        raise HTTPException(
            status_code=400, detail="Neighbor reporting requires a Community MQTT config"
        )
    if not existing["enabled"]:
        raise HTTPException(status_code=409, detail="Community MQTT integration is disabled")
    return existing


@router.get("/{config_id}/community-neighbors/status")
async def get_community_neighbor_status(config_id: str) -> dict:
    """Return cache and shared-workflow state for a Community MQTT slot."""
    await _require_community_neighbor_config(config_id)
    from app.fanout.community_neighbors import community_neighbor_reporter

    return community_neighbor_reporter.status_for(config_id)


@router.post("/{config_id}/community-neighbors/discover")
async def discover_community_neighbors(config_id: str) -> dict:
    """Start or join a non-publishing 60-second zero-hop neighbor refresh."""
    await _require_community_neighbor_config(config_id)
    from app.fanout.community_neighbors import NeighborReporterError, community_neighbor_reporter

    try:
        return await community_neighbor_reporter.start_manual_discovery(config_id)
    except NeighborReporterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/{config_id}/community-neighbors/snapshot")
async def publish_community_neighbor_snapshot(config_id: str) -> dict:
    """Query cached direct repeaters and publish one completion snapshot."""
    await _require_community_neighbor_config(config_id)
    from app.fanout.community_neighbors import NeighborReporterError, community_neighbor_reporter

    try:
        return await community_neighbor_reporter.start_manual_snapshot(config_id)
    except NeighborReporterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.delete("/{config_id}/community-neighbors/{public_key}")
async def remove_community_neighbor(config_id: str, public_key: str) -> dict:
    """Explicitly remove one cached direct repeater neighbour by public key."""
    await _require_community_neighbor_config(config_id)
    from app.fanout.community_neighbors import community_neighbor_reporter

    removed = await community_neighbor_reporter.remove_neighbor(public_key)
    return {"removed": removed, "public_key": public_key.lower()}


@router.delete("/{config_id}/community-neighbors")
async def clear_community_neighbors(config_id: str) -> dict:
    """Remove all cached direct repeater neighbours."""
    await _require_community_neighbor_config(config_id)
    from app.fanout.community_neighbors import community_neighbor_reporter

    count = await community_neighbor_reporter.clear_neighbors()
    return {"cleared": count}


@router.delete("/{config_id}")
async def delete_fanout_config(config_id: str) -> dict:
    """Delete a fanout config."""
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Stop the module first
    await fanout_manager.remove_config(config_id)
    await FanoutConfigRepository.delete(config_id)

    logger.info("Deleted fanout config %s", config_id)
    return {"deleted": True}


@router.post("/bots/disable-until-restart")
async def disable_bots_until_restart() -> dict:
    """Stop active bot modules and prevent them from running again until restart."""
    source = await fanout_manager.disable_bots_until_restart()

    from app.services.radio_runtime import radio_runtime as radio_manager
    from app.websocket import broadcast_health

    broadcast_health(radio_manager.is_connected, radio_manager.connection_info)
    return {
        "status": "ok",
        "bots_disabled": True,
        "bots_disabled_source": source,
    }
