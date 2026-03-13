"""Fanout module for Amazon SQS delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from functools import partial
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)


def _build_payload(data: dict, *, event_type: str) -> str:
    """Serialize a fanout event into a stable JSON envelope."""
    return json.dumps(
        {
            "event_type": event_type,
            "data": data,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _infer_region_from_queue_url(queue_url: str) -> str | None:
    """Infer AWS region from a standard SQS queue URL host when possible."""
    host = urlparse(queue_url).hostname or ""
    if not host:
        return None

    parts = host.split(".")
    if len(parts) < 4 or parts[0] != "sqs":
        return None
    if parts[2] != "amazonaws":
        return None
    if parts[3] not in {"com", "com.cn"}:
        return None

    region = parts[1].strip()
    return region or None


def _is_fifo_queue(queue_url: str) -> bool:
    """Return True when the configured queue URL points at an SQS FIFO queue."""
    return queue_url.rstrip("/").endswith(".fifo")


def _build_message_group_id(data: dict, *, event_type: str) -> str:
    """Choose a stable FIFO group ID from the event identity."""
    if event_type == "message":
        conversation_key = str(data.get("conversation_key", "")).strip()
        if conversation_key:
            return f"message-{conversation_key}"
        return "message-default"
    return "raw-packets"


def _build_message_deduplication_id(data: dict, *, event_type: str, body: str) -> str:
    """Choose a deterministic deduplication ID for FIFO queues."""
    if event_type == "message":
        message_id = data.get("id")
        if isinstance(message_id, int):
            return f"message-{message_id}"
    else:
        observation_id = data.get("observation_id")
        if isinstance(observation_id, str) and observation_id.strip():
            return f"raw-{observation_id}"
        packet_id = data.get("id")
        if isinstance(packet_id, int):
            return f"raw-{packet_id}"
    return hashlib.sha256(body.encode()).hexdigest()


class SqsModule(FanoutModule):
    """Delivers message and raw-packet events to an Amazon SQS queue."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._client = None
        self._last_error: str | None = None

    async def start(self) -> None:
        kwargs: dict[str, str] = {}
        queue_url = str(self.config.get("queue_url", "")).strip()
        region_name = str(self.config.get("region_name", "")).strip()
        endpoint_url = str(self.config.get("endpoint_url", "")).strip()
        access_key_id = str(self.config.get("access_key_id", "")).strip()
        secret_access_key = str(self.config.get("secret_access_key", "")).strip()
        session_token = str(self.config.get("session_token", "")).strip()

        if not region_name:
            region_name = _infer_region_from_queue_url(queue_url) or ""
        if region_name:
            kwargs["region_name"] = region_name
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        if session_token:
            kwargs["aws_session_token"] = session_token

        self._client = boto3.client("sqs", **kwargs)
        self._last_error = None

    async def stop(self) -> None:
        self._client = None

    async def on_message(self, data: dict) -> None:
        await self._send(data, event_type="message")

    async def on_raw(self, data: dict) -> None:
        await self._send(data, event_type="raw_packet")

    async def _send(self, data: dict, *, event_type: str) -> None:
        if self._client is None:
            return

        queue_url = str(self.config.get("queue_url", "")).strip()
        if not queue_url:
            return

        body = _build_payload(data, event_type=event_type)
        request_kwargs: dict[str, object] = {
            "QueueUrl": queue_url,
            "MessageBody": body,
            "MessageAttributes": {
                "event_type": {
                    "DataType": "String",
                    "StringValue": event_type,
                }
            },
        }

        if _is_fifo_queue(queue_url):
            request_kwargs["MessageGroupId"] = _build_message_group_id(data, event_type=event_type)
            request_kwargs["MessageDeduplicationId"] = _build_message_deduplication_id(
                data, event_type=event_type, body=body
            )

        try:
            await asyncio.to_thread(partial(self._client.send_message, **request_kwargs))
            self._last_error = None
        except (ClientError, BotoCoreError) as exc:
            self._last_error = str(exc)
            logger.warning("SQS %s send error: %s", self.config_id, exc)
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Unexpected SQS send error for %s", self.config_id)

    @property
    def status(self) -> str:
        if not str(self.config.get("queue_url", "")).strip():
            return "disconnected"
        if self._last_error:
            return "error"
        return "connected"
