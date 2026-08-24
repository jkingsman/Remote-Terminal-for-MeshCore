"""FanoutManager: owns all active fanout modules and dispatches events."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)
_DISPATCH_TIMEOUT_SECONDS = 30.0

# Type string -> module class mapping
_MODULE_TYPES: dict[str, type] = {}


def _format_error_detail(exc: Exception) -> str:
    """Return a short operator-facing error string."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _register_module_types() -> None:
    """Lazily populate the type registry to avoid circular imports."""
    if _MODULE_TYPES:
        return
    from app.fanout.apprise_mod import AppriseModule
    from app.fanout.bot import BotModule
    from app.fanout.discord_bridge import DiscordBridgeModule
    from app.fanout.map_upload import MapUploadModule
    from app.fanout.mqtt_community import MqttCommunityModule
    from app.fanout.mqtt_ha import MqttHaModule
    from app.fanout.mqtt_private import MqttPrivateModule
    from app.fanout.sqs import SqsModule
    from app.fanout.telegram_bridge import TelegramBridgeModule
    from app.fanout.webhook import WebhookModule

    _MODULE_TYPES["mqtt_private"] = MqttPrivateModule
    _MODULE_TYPES["mqtt_community"] = MqttCommunityModule
    _MODULE_TYPES["mqtt_ha"] = MqttHaModule
    # "bot" stays registered so a DB that missed migration 064 still runs its
    # legacy rows; creating new ones via the API is no longer possible.
    _MODULE_TYPES["bot"] = BotModule
    _MODULE_TYPES["webhook"] = WebhookModule
    _MODULE_TYPES["apprise"] = AppriseModule
    _MODULE_TYPES["sqs"] = SqsModule
    _MODULE_TYPES["map_upload"] = MapUploadModule
    _MODULE_TYPES["discord_bridge"] = DiscordBridgeModule
    _MODULE_TYPES["telegram_bridge"] = TelegramBridgeModule


def _matches_filter(filter_value: Any, key: str) -> bool:
    """Check a single filter value (channels or contacts) against a key.

    Supported shapes:
      "all"                        -> True
      "none"                       -> False
      ["key1", "key2"]             -> key in list  (only listed)
      {"except": ["key1", "key2"]} -> key not in list  (all except listed)
    """
    if filter_value == "all":
        return True
    if filter_value == "none":
        return False
    if isinstance(filter_value, list):
        return key in filter_value
    if isinstance(filter_value, dict) and "except" in filter_value:
        return key not in filter_value["except"]
    return False


def _scope_matches_message(scope: dict, data: dict) -> bool:
    """Check whether a message event matches the given scope."""
    messages = scope.get("messages", "none")
    if messages == "all":
        return True
    if messages == "none":
        return False
    if isinstance(messages, dict):
        msg_type = data.get("type", "")
        conversation_key = data.get("conversation_key", "")
        if msg_type == "CHAN":
            return _matches_filter(messages.get("channels", "none"), conversation_key)
        elif msg_type == "PRIV":
            return _matches_filter(messages.get("contacts", "none"), conversation_key)
    return False


def _scope_matches_raw(scope: dict, _data: dict) -> bool:
    """Check whether a raw packet event matches the given scope."""
    return scope.get("raw_packets", "none") == "all"


def _always_match(_scope: dict, _data: dict) -> bool:
    """Match all modules unconditionally (filtering is module-internal)."""
    return True


class FanoutManager:
    """Owns all active fanout modules and dispatches events."""

    def __init__(self) -> None:
        self._modules: dict[str, tuple[FanoutModule, dict]] = {}  # id -> (module, scope)
        self._restart_locks: dict[str, asyncio.Lock] = {}
        self._bots_disabled_until_restart = False
        self._module_errors: dict[str, str] = {}

    def _broadcast_health_update(self) -> None:
        from app.services.radio_runtime import radio_runtime as radio_manager
        from app.websocket import broadcast_health

        broadcast_health(radio_manager.is_connected, radio_manager.connection_info)

    def _set_module_error(self, config_id: str, error: str) -> None:
        if self._module_errors.get(config_id) == error:
            return
        self._module_errors[config_id] = error
        self._broadcast_health_update()

    def _clear_module_error(self, config_id: str) -> None:
        if self._module_errors.pop(config_id, None) is not None:
            self._broadcast_health_update()

    def get_bots_disabled_source(self) -> str | None:
        """Return why bot modules are unavailable, if at all."""
        from app.config import settings as server_settings

        if server_settings.disable_bots:
            return "env"
        if self._bots_disabled_until_restart:
            return "until_restart"
        return None

    def bots_disabled_effective(self) -> bool:
        """Return True when bot modules should be treated as unavailable."""
        return self.get_bots_disabled_source() is not None

    async def load_from_db(self) -> None:
        """Read enabled fanout_configs and instantiate modules."""
        _register_module_types()
        from app.repository.fanout import FanoutConfigRepository

        configs = await FanoutConfigRepository.get_enabled()
        for cfg in configs:
            await self._start_module(cfg)

    async def _start_module(self, cfg: dict[str, Any]) -> None:
        """Instantiate and start a single module from a config dict."""
        config_id = cfg["id"]
        config_type = cfg["type"]
        config_blob = cfg["config"]
        scope = cfg["scope"]

        # Skip bot modules when bots are disabled server-wide or until restart.
        if config_type == "bot" and self.bots_disabled_effective():
            logger.info(
                "Skipping bot module %s (bots disabled: %s)",
                config_id,
                self.get_bots_disabled_source(),
            )
            return

        cls = _MODULE_TYPES.get(config_type)
        if cls is None:
            logger.warning("Unknown fanout type %r for config %s, skipping", config_type, config_id)
            return

        try:
            module = cls(config_id, config_blob, name=cfg.get("name", ""))
            await module.start()
            self._modules[config_id] = (module, scope)
            self._clear_module_error(config_id)
            logger.info(
                "Started fanout module %s (type=%s)", cfg.get("name", config_id), config_type
            )
        except Exception as exc:
            logger.exception("Failed to start fanout module %s", config_id)
            self._set_module_error(config_id, _format_error_detail(exc))

    async def reload_config(self, config_id: str) -> None:
        """Stop old module (if any) and start updated config."""
        lock = self._restart_locks.setdefault(config_id, asyncio.Lock())
        async with lock:
            await self.remove_config(config_id)

            from app.repository.fanout import FanoutConfigRepository

            cfg = await FanoutConfigRepository.get(config_id)
            if cfg is None or not cfg["enabled"]:
                return
            await self._start_module(cfg)

    async def remove_config(self, config_id: str) -> None:
        """Stop and remove a module."""
        entry = self._modules.pop(config_id, None)
        if entry is not None:
            module, _ = entry
            try:
                await module.stop()
            except Exception:
                logger.exception("Error stopping fanout module %s", config_id)
        self._clear_module_error(config_id)

    async def _dispatch_matching(
        self,
        data: dict,
        *,
        matcher: Any,
        handler_name: str,
        log_label: str,
    ) -> None:
        """Dispatch to all matching modules concurrently."""
        tasks = []
        for config_id, (module, scope) in list(self._modules.items()):
            if matcher(scope, data):
                tasks.append(self._run_handler(config_id, module, handler_name, data, log_label))
        if tasks:
            await asyncio.gather(*tasks)

    async def _run_handler(
        self,
        config_id: str,
        module: FanoutModule,
        handler_name: str,
        data: dict,
        log_label: str,
    ) -> None:
        """Run one module handler with per-module exception isolation."""
        try:
            handler = getattr(module, handler_name)
            await asyncio.wait_for(handler(data), timeout=_DISPATCH_TIMEOUT_SECONDS)
            self._clear_module_error(config_id)
        except TimeoutError:
            timeout_error = f"{handler_name} timed out after {_DISPATCH_TIMEOUT_SECONDS:.1f}s"
            self._set_module_error(config_id, timeout_error)
            logger.error(
                "Fanout %s %s timed out after %.1fs; restarting module",
                config_id,
                log_label,
                _DISPATCH_TIMEOUT_SECONDS,
            )
            await self._restart_module(config_id, module)
        except Exception as exc:
            self._set_module_error(config_id, _format_error_detail(exc))
            logger.exception("Fanout %s %s error", config_id, log_label)

    async def _restart_module(self, config_id: str, module: FanoutModule) -> None:
        """Restart a timed-out module if it is still the active instance."""
        lock = self._restart_locks.setdefault(config_id, asyncio.Lock())
        async with lock:
            entry = self._modules.get(config_id)
            if entry is None or entry[0] is not module:
                return
            try:
                await module.stop()
                await module.start()
            except Exception:
                logger.exception("Failed to restart timed-out fanout module %s", config_id)
                self._modules.pop(config_id, None)
                self._set_module_error(
                    config_id,
                    "Module restart failed after timeout",
                )

    async def broadcast_message(self, data: dict) -> None:
        """Dispatch a decoded message to modules whose scope matches."""
        await self._dispatch_matching(
            data,
            matcher=_scope_matches_message,
            handler_name="on_message",
            log_label="on_message",
        )

    async def broadcast_raw(self, data: dict) -> None:
        """Dispatch a raw packet to modules whose scope matches."""
        await self._dispatch_matching(
            data,
            matcher=_scope_matches_raw,
            handler_name="on_raw",
            log_label="on_raw",
        )

    async def broadcast_contact(self, data: dict) -> None:
        """Dispatch a contact upsert to all modules."""
        await self._dispatch_matching(
            data,
            matcher=_always_match,
            handler_name="on_contact",
            log_label="on_contact",
        )

    async def broadcast_telemetry(self, data: dict) -> None:
        """Dispatch a repeater telemetry snapshot to all modules."""
        await self._dispatch_matching(
            data,
            matcher=_always_match,
            handler_name="on_telemetry",
            log_label="on_telemetry",
        )

    async def broadcast_health_fanout(self, data: dict) -> None:
        """Dispatch a radio health snapshot to all modules."""
        await self._dispatch_matching(
            data,
            matcher=_always_match,
            handler_name="on_health",
            log_label="on_health",
        )

    async def stop_all(self) -> None:
        """Shutdown all modules."""
        for config_id, (module, _) in list(self._modules.items()):
            try:
                await module.stop()
            except Exception:
                logger.exception("Error stopping fanout module %s", config_id)
        self._modules.clear()
        self._restart_locks.clear()
        self._module_errors.clear()

    def get_statuses(self) -> dict[str, dict[str, str | None]]:
        """Return status info for each active module."""
        from app.repository.fanout import _configs_cache

        result: dict[str, dict[str, str | None]] = {}
        all_ids = set(_configs_cache) | set(self._modules) | set(self._module_errors)
        for config_id in all_ids:
            info = _configs_cache.get(config_id, {})
            if info.get("enabled") is False:
                continue

            module_entry = self._modules.get(config_id)
            module = module_entry[0] if module_entry is not None else None
            last_error = module.last_error if module is not None else None
            status = module.status if module is not None else "error"

            manager_error = self._module_errors.get(config_id)
            if manager_error is not None:
                status = "error"
                last_error = manager_error
            elif last_error is not None and status != "error":
                status = "error"

            if module is None and last_error is None:
                continue

            result[config_id] = {
                "name": info.get("name", config_id),
                "type": info.get("type", "unknown"),
                "status": status,
                "last_error": last_error,
            }
        return result

    async def disable_bots_until_restart(self) -> str:
        """Stop active bot modules and prevent them from starting again until restart."""
        source = self.get_bots_disabled_source()
        if source == "env":
            return source

        self._bots_disabled_until_restart = True

        from app.repository.fanout import _configs_cache

        bot_ids = [
            config_id
            for config_id in list(self._modules)
            if _configs_cache.get(config_id, {}).get("type") == "bot"
        ]
        for config_id in bot_ids:
            await self.remove_config(config_id)

        return "until_restart"


# Module-level singleton
fanout_manager = FanoutManager()
