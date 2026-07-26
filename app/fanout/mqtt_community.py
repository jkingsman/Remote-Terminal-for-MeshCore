"""Fanout module wrapping the community MQTT publisher."""

from __future__ import annotations

import logging
import re
import string
from types import SimpleNamespace
from typing import Any

from app.fanout.base import FanoutModule
from app.fanout.community_mqtt import CommunityMqttPublisher, _format_raw_packet
from app.fanout.community_neighbors import community_neighbor_reporter

logger = logging.getLogger(__name__)

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DEFAULT_PACKET_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/packets"
_DEFAULT_NEIGHBOR_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/neighbors"
_TOPIC_TEMPLATE_FIELD_CANONICAL = {
    "iata": "IATA",
    "public_key": "PUBLIC_KEY",
}
_NEIGHBOR_TOPIC_TEMPLATE_FIELD_CANONICAL = {
    **_TOPIC_TEMPLATE_FIELD_CANONICAL,
    # ``device`` and ``type`` are the common names used by generic community
    # MQTT template systems.  Keep PUBLIC_KEY as the established RemoteTerm
    # spelling and make both forms render the same observer identity.
    "device": "PUBLIC_KEY",
    "type": "TYPE",
}


def _normalize_topic_template(topic_template: str) -> str:
    """Normalize packet topic template fields to canonical uppercase placeholders."""
    template = topic_template.strip() or _DEFAULT_PACKET_TOPIC_TEMPLATE
    parts: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for literal_text, field_name, format_spec, conversion in parsed:
            parts.append(literal_text)
            if field_name is None:
                continue
            normalized_field = _TOPIC_TEMPLATE_FIELD_CANONICAL.get(field_name.lower())
            if normalized_field is None:
                raise ValueError(f"Unsupported topic template field(s): {field_name}")
            replacement = ["{", normalized_field]
            if conversion:
                replacement.extend(["!", conversion])
            if format_spec:
                replacement.extend([":", format_spec])
            replacement.append("}")
            parts.append("".join(replacement))
    except ValueError:
        raise

    return "".join(parts)


def _normalize_neighbor_topic_template(topic_template: str) -> str:
    """Normalize neighbor topic fields, including the ``{TYPE}`` suffix hook."""
    template = topic_template.strip() or _DEFAULT_NEIGHBOR_TOPIC_TEMPLATE
    parts: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for literal_text, field_name, format_spec, conversion in parsed:
            parts.append(literal_text)
            if field_name is None:
                continue
            normalized_field = _NEIGHBOR_TOPIC_TEMPLATE_FIELD_CANONICAL.get(field_name.lower())
            if normalized_field is None:
                raise ValueError(f"Unsupported neighbor topic template field(s): {field_name}")
            replacement = ["{", normalized_field]
            if conversion:
                replacement.extend(["!", conversion])
            if format_spec:
                replacement.extend([":", format_spec])
            replacement.append("}")
            parts.append("".join(replacement))
    except ValueError:
        raise

    normalized = "".join(parts)
    if not (normalized.endswith("/neighbors") or normalized.endswith("/{TYPE}")):
        raise ValueError("Neighbor topic templates must end with /neighbors or /{TYPE}")
    return normalized


def _config_to_settings(config: dict) -> SimpleNamespace:
    """Map a fanout config blob to a settings namespace for the CommunityMqttPublisher."""
    return SimpleNamespace(
        community_mqtt_enabled=True,
        community_mqtt_broker_host=config.get("broker_host", "mqtt-us-v1.letsmesh.net"),
        community_mqtt_broker_port=config.get("broker_port", 443),
        community_mqtt_transport=config.get("transport", "websockets"),
        community_mqtt_use_tls=config.get("use_tls", True),
        community_mqtt_tls_verify=config.get("tls_verify", True),
        community_mqtt_auth_mode=config.get("auth_mode", "token"),
        community_mqtt_username=config.get("username", ""),
        community_mqtt_password=config.get("password", ""),
        community_mqtt_iata=config.get("iata", ""),
        community_mqtt_email=config.get("email", ""),
        community_mqtt_token_audience=config.get("token_audience", ""),
        community_mqtt_websocket_path=config.get("websocket_path", "/"),
    )


def _render_packet_topic(topic_template: str, *, iata: str, public_key: str) -> str:
    """Render the configured raw-packet publish topic."""
    template = _normalize_topic_template(topic_template)
    return template.format(IATA=iata, PUBLIC_KEY=public_key)


def _render_neighbor_topic(topic_template: str, *, iata: str, public_key: str) -> str:
    """Render the configured MQTT topic for one canonical neighbor snapshot."""
    template = _normalize_neighbor_topic_template(topic_template)
    return template.format(IATA=iata, PUBLIC_KEY=public_key, TYPE="neighbors")


class MqttCommunityModule(FanoutModule):
    """Wraps a CommunityMqttPublisher for community packet sharing."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._publisher = CommunityMqttPublisher()
        self._publisher.set_integration_name(name or config_id)

    async def start(self) -> None:
        settings = _config_to_settings(self.config)
        await self._publisher.start(settings)
        await community_neighbor_reporter.register_module(self)

    async def stop(self) -> None:
        await community_neighbor_reporter.unregister_module(self.config_id)
        await self._publisher.stop()

    async def on_message(self, data: dict) -> None:
        # Community MQTT only publishes raw packets, not decoded messages.
        pass

    async def on_raw(self, data: dict) -> None:
        # Neighbor cache observations are radio facts, not MQTT delivery
        # attempts.  Keep recording them while this broker reconnects.
        await community_neighbor_reporter.observe_raw_packet(data)
        if not self._publisher.connected or self._publisher._settings is None:
            return
        await _publish_community_packet(self._publisher, self.config, data)

    @property
    def neighbor_publisher_connected(self) -> bool:
        """Whether this slot can accept a completed neighbor snapshot now."""
        return self._publisher.connected and self._publisher._settings is not None

    async def publish_neighbor_snapshot(self, serialized_snapshot: str) -> bool:
        """Publish an already-frozen canonical neighbor JSON document at QoS 1."""
        if not self.neighbor_publisher_connected:
            return False

        try:
            from app.keystore import get_public_key

            public_key = get_public_key()
            if public_key is None or len(public_key) != 32:
                return False

            iata = str(self.config.get("iata", "")).upper().strip()
            if not _IATA_RE.fullmatch(iata):
                logger.debug(
                    "Community MQTT: skipping neighbor snapshot — no valid IATA code configured"
                )
                return False

            topic = _render_neighbor_topic(
                str(self.config.get("neighbor_topic_template", _DEFAULT_NEIGHBOR_TOPIC_TEMPLATE)),
                iata=iata,
                public_key=public_key.hex().upper(),
            )
            return await self._publisher.publish(
                topic,
                serialized_snapshot,
                retain=bool(self.config.get("neighbor_retain", False)),
                qos=1,
            )
        except Exception:
            logger.warning("Community MQTT neighbor snapshot publish error", exc_info=True)
            return False

    @property
    def status(self) -> str:
        if self.last_error:
            return "error"
        if self._publisher._is_configured():
            return "connected" if self._publisher.connected else "disconnected"
        return "disconnected"

    @property
    def last_error(self) -> str | None:
        if self._publisher.last_error:
            return self._publisher.last_error
        return self._key_unavailable_reason()

    def _key_unavailable_reason(self) -> str | None:
        """Explain a silent "disconnected" caused by a missing radio key.

        Community MQTT can only authenticate (and derive its topic/identity)
        with the radio's own key, which is exported from the radio on connect.
        When the radio is fully connected and set up but the keystore is still
        empty, the key export was refused or never answered -- typically
        firmware without ENABLE_PRIVATE_KEY_EXPORT, or a proxy transport
        (e.g. Meshmonitor) that does not forward the key-export command.

        Surfacing this as the module's ``last_error`` promotes the fanout status
        to "error" and lights up the existing per-card error detail, instead of
        leaving the operator staring at a bare "Disconnected". See issue #321.

        Gated on ``is_setup_complete`` so we do not falsely accuse the radio
        during the normal connect/key-export window.
        """
        from app.keystore import get_public_key
        from app.services.radio_runtime import radio_runtime as radio_manager

        if get_public_key() is not None:
            return None
        if not (radio_manager.is_connected and radio_manager.is_setup_complete):
            return None
        return (
            "Community MQTT needs the radio's private key to authenticate, but it "
            "isn't available. Your radio firmware may not support key export "
            "(ENABLE_PRIVATE_KEY_EXPORT=1), or you're connecting through a proxy "
            "that doesn't forward the key-export command."
        )


async def _publish_community_packet(
    publisher: CommunityMqttPublisher,
    config: dict,
    data: dict[str, Any],
) -> None:
    """Format and publish a raw packet to the community broker."""
    try:
        from app.keystore import get_public_key
        from app.services.radio_runtime import radio_runtime as radio_manager

        public_key = get_public_key()
        if public_key is None:
            return

        pubkey_hex = public_key.hex().upper()

        device_name = ""
        if radio_manager.meshcore and radio_manager.meshcore.self_info:
            device_name = radio_manager.meshcore.self_info.get("name", "")

        packet = _format_raw_packet(data, device_name, pubkey_hex)
        if packet is None:
            return
        iata = config.get("iata", "").upper().strip()
        if not _IATA_RE.fullmatch(iata):
            logger.debug("Community MQTT: skipping publish — no valid IATA code configured")
            return
        topic = _render_packet_topic(
            str(config.get("topic_template", _DEFAULT_PACKET_TOPIC_TEMPLATE)),
            iata=iata,
            public_key=pubkey_hex,
        )

        await publisher.publish(topic, packet)

    except Exception as e:
        logger.warning("Community MQTT broadcast error: %s", e, exc_info=True)
