import logging
import logging.config
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MESHCORE_")

    serial_port: str = ""  # Empty string triggers auto-detection
    serial_baudrate: int = 115200
    tcp_host: str = ""
    tcp_port: int = 4000
    ble_address: str = ""
    ble_pin: str = ""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_path: str = "data/meshcore.db"
    disable_bots: bool = False
    enable_message_poll_fallback: bool = False
    force_channel_slot_reconfigure: bool = False
    basic_auth_username: str = ""
    basic_auth_password: str = ""

    @model_validator(mode="after")
    def validate_transport_exclusivity(self) -> "Settings":
        transports_set = sum(
            [
                bool(self.serial_port),
                bool(self.tcp_host),
                bool(self.ble_address),
            ]
        )
        if transports_set > 1:
            raise ValueError(
                "Only one transport may be configured at a time. "
                "Set exactly one of MESHCORE_SERIAL_PORT, MESHCORE_TCP_HOST, or MESHCORE_BLE_ADDRESS."
            )
        if self.ble_address and not self.ble_pin:
            raise ValueError("MESHCORE_BLE_PIN is required when MESHCORE_BLE_ADDRESS is set.")
        if self.basic_auth_partially_configured:
            raise ValueError(
                "MESHCORE_BASIC_AUTH_USERNAME and MESHCORE_BASIC_AUTH_PASSWORD "
                "must be set together."
            )
        return self

    @property
    def connection_type(self) -> Literal["serial", "tcp", "ble"]:
        if self.tcp_host:
            return "tcp"
        if self.ble_address:
            return "ble"
        return "serial"

    @property
    def basic_auth_enabled(self) -> bool:
        return bool(self.basic_auth_username and self.basic_auth_password)

    @property
    def basic_auth_partially_configured(self) -> bool:
        any_credentials_set = bool(self.basic_auth_username or self.basic_auth_password)
        return any_credentials_set and not self.basic_auth_enabled


settings = Settings()


class _RepeatSquelch(logging.Filter):
    """Suppress rapid-fire identical messages and emit a summary instead.

    Attached to the ``meshcore`` library logger to catch its repeated
    "Serial Connection started" lines that flood the log when another
    process holds the serial port.
    """

    def __init__(self, threshold: int = 3) -> None:
        super().__init__()
        self._last_msg: str | None = None
        self._repeat_count: int = 0
        self._threshold = threshold

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if msg == self._last_msg:
            self._repeat_count += 1
            if self._repeat_count == self._threshold:
                record.msg = (
                    "%s (repeated %d times — possible serial port contention from another process)"
                )
                record.args = (msg, self._repeat_count)
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
                return True
            # Suppress further repeats beyond the threshold
            return self._repeat_count < self._threshold
        else:
            self._last_msg = msg
            self._repeat_count = 1
            return True


def setup_logging() -> None:
    """Configure logging for the application."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "uvicorn_access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": '%(asctime)s - %(name)s - %(levelname)s - %(client_addr)s - "%(request_line)s" %(status_code)s',
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                    "use_colors": None,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                "uvicorn_access": {
                    "class": "logging.StreamHandler",
                    "formatter": "uvicorn_access",
                },
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {
                    "level": settings.log_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": settings.log_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": settings.log_level,
                    "handlers": ["uvicorn_access"],
                    "propagate": False,
                },
            },
        }
    )
    # Squelch repeated messages from the meshcore library (e.g. rapid-fire
    # "Serial Connection started" when the port is contended).
    logging.getLogger("meshcore").addFilter(_RepeatSquelch())
