import asyncio
import glob
import logging
import platform
from pathlib import Path

from meshcore import MeshCore

from app.config import settings

logger = logging.getLogger(__name__)


def detect_serial_devices() -> list[str]:
    """Detect available serial devices based on platform."""
    devices: list[str] = []
    system = platform.system()

    if system == "Darwin":
        # macOS: Use /dev/cu.* devices (callout devices, preferred over tty.*)
        patterns = [
            "/dev/cu.usb*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.SLAB_USBtoUART*",
        ]
        for pattern in patterns:
            devices.extend(glob.glob(pattern))
        devices.sort()
    else:
        # Linux: Prefer /dev/serial/by-id/ for persistent naming
        by_id_path = Path("/dev/serial/by-id")
        if by_id_path.is_dir():
            devices.extend(str(p) for p in by_id_path.iterdir())

        # Also check /dev/ttyACM* and /dev/ttyUSB* as fallback
        resolved_paths = set()
        for dev in devices:
            try:
                resolved_paths.add(str(Path(dev).resolve()))
            except OSError:
                pass

        for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*"]:
            for dev in glob.glob(pattern):
                try:
                    if str(Path(dev).resolve()) not in resolved_paths:
                        devices.append(dev)
                except OSError:
                    devices.append(dev)

        devices.sort()

    return devices


async def test_serial_device(port: str, baudrate: int, timeout: float = 3.0) -> bool:
    """Test if a MeshCore radio responds on the given serial port."""
    try:
        logger.debug("Testing serial device %s", port)
        mc = await asyncio.wait_for(
            MeshCore.create_serial(port=port, baudrate=baudrate),
            timeout=timeout,
        )

        # Check if we got valid self_info (indicates successful communication)
        if mc.is_connected and mc.self_info:
            logger.debug("Device %s responded with valid self_info", port)
            await mc.disconnect()
            return True

        await mc.disconnect()
        return False
    except asyncio.TimeoutError:
        logger.debug("Device %s timed out", port)
        return False
    except Exception as e:
        logger.debug("Device %s failed: %s", port, e)
        return False


async def find_radio_port(baudrate: int) -> str | None:
    """Find the first serial port with a responding MeshCore radio."""
    devices = detect_serial_devices()

    if not devices:
        logger.warning("No serial devices found")
        return None

    logger.info("Found %d serial device(s), testing for MeshCore radio...", len(devices))

    for device in devices:
        if await test_serial_device(device, baudrate):
            logger.info("Found MeshCore radio at %s", device)
            return device

    logger.warning("No MeshCore radio found on any serial device")
    return None


class RadioManager:
    """Manages the MeshCore radio connection."""

    def __init__(self):
        self._meshcore: MeshCore | None = None
        self._port: str | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: bool = False
        self._reconnecting: bool = False

    @property
    def meshcore(self) -> MeshCore | None:
        return self._meshcore

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def is_connected(self) -> bool:
        return self._meshcore is not None and self._meshcore.is_connected

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnecting

    async def connect(self) -> None:
        """Connect to the radio over serial."""
        if self._meshcore is not None:
            await self.disconnect()

        port = settings.serial_port

        # Auto-detect if no port specified
        if not port:
            logger.info("No serial port specified, auto-detecting...")
            port = await find_radio_port(settings.serial_baudrate)
            if not port:
                raise RuntimeError("No MeshCore radio found. Please specify MESHCORE_SERIAL_PORT.")

        logger.debug(
            "Connecting to radio at %s (baud %d)",
            port,
            settings.serial_baudrate,
        )
        self._meshcore = await MeshCore.create_serial(
            port=port,
            baudrate=settings.serial_baudrate,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        self._port = port
        self._last_connected = True
        logger.debug("Serial connection established")

    async def disconnect(self) -> None:
        """Disconnect from the radio."""
        if self._meshcore is not None:
            logger.debug("Disconnecting from radio")
            await self._meshcore.disconnect()
            self._meshcore = None
            logger.debug("Radio disconnected")

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the radio.

        Returns True if reconnection was successful, False otherwise.
        """
        from app.websocket import broadcast_error, broadcast_health

        if self._reconnecting:
            logger.debug("Reconnection already in progress")
            return False

        self._reconnecting = True
        logger.info("Attempting to reconnect to radio...")

        try:
            # Disconnect if we have a stale connection
            if self._meshcore is not None:
                try:
                    await self._meshcore.disconnect()
                except Exception:
                    pass
                self._meshcore = None

            # Try to connect (will auto-detect if no port specified)
            await self.connect()

            if self.is_connected:
                logger.info("Radio reconnected successfully at %s", self._port)
                broadcast_health(True, self._port)
                return True
            else:
                logger.warning("Reconnection failed: not connected after connect()")
                return False

        except Exception as e:
            logger.warning("Reconnection failed: %s", e)
            broadcast_error("Reconnection failed", str(e))
            return False
        finally:
            self._reconnecting = False

    async def start_connection_monitor(self) -> None:
        """Start background task to monitor connection and auto-reconnect."""
        if self._reconnect_task is not None:
            return

        async def monitor_loop():
            from app.websocket import broadcast_health

            while True:
                await asyncio.sleep(5)  # Check every 5 seconds

                current_connected = self.is_connected

                # Detect status change
                if self._last_connected and not current_connected:
                    # Connection lost
                    logger.warning("Radio connection lost, broadcasting status change")
                    broadcast_health(False, self._port)
                    self._last_connected = False

                    # Attempt reconnection
                    await asyncio.sleep(3)  # Wait a bit before trying
                    await self.reconnect()

                elif not self._last_connected and current_connected:
                    # Connection restored (might have reconnected automatically)
                    logger.info("Radio connection restored")
                    broadcast_health(True, self._port)
                    self._last_connected = True

        self._reconnect_task = asyncio.create_task(monitor_loop())
        logger.info("Radio connection monitor started")

    async def stop_connection_monitor(self) -> None:
        """Stop the connection monitor task."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
            logger.info("Radio connection monitor stopped")


radio_manager = RadioManager()
