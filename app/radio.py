import asyncio
import glob
import logging
import platform
from collections import OrderedDict
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

from meshcore import MeshCore

from app.config import settings

logger = logging.getLogger(__name__)


class RadioOperationError(RuntimeError):
    """Base class for shared radio operation lock errors."""


class RadioOperationBusyError(RadioOperationError):
    """Raised when a non-blocking radio operation cannot acquire the lock."""


class RadioDisconnectedError(RadioOperationError):
    """Raised when the radio disconnects between pre-check and lock acquisition."""


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
    mc = None
    try:
        logger.debug("Testing serial device %s", port)
        mc = await asyncio.wait_for(
            MeshCore.create_serial(port=port, baudrate=baudrate),
            timeout=timeout,
        )

        # Check if we got valid self_info (indicates successful communication)
        if mc.is_connected and mc.self_info:
            logger.debug("Device %s responded with valid self_info", port)
            return True

        return False
    except asyncio.TimeoutError:
        logger.debug("Device %s timed out", port)
        return False
    except Exception as e:
        logger.debug("Device %s failed: %s", port, e)
        return False
    finally:
        if mc is not None:
            try:
                await mc.disconnect()
            except Exception:
                pass


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
        self._connection_info: str | None = None
        self._connection_desired: bool = True
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: bool = False
        self._reconnect_lock: asyncio.Lock | None = None
        self._operation_lock: asyncio.Lock | None = None
        self._setup_lock: asyncio.Lock | None = None
        self._setup_in_progress: bool = False
        self._setup_complete: bool = False
        self.max_channels: int = 40
        self.path_hash_mode: int = 0
        self.path_hash_mode_supported: bool = False
        self._channel_slot_by_key: OrderedDict[str, int] = OrderedDict()
        self._channel_key_by_slot: dict[int, str] = {}

    async def _acquire_operation_lock(
        self,
        name: str,
        *,
        blocking: bool,
    ) -> None:
        """Acquire the shared radio operation lock."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()

        if not blocking:
            if self._operation_lock.locked():
                raise RadioOperationBusyError(f"Radio is busy (operation: {name})")
            await self._operation_lock.acquire()
        else:
            await self._operation_lock.acquire()

        logger.debug("Acquired radio operation lock (%s)", name)

    def _release_operation_lock(self, name: str) -> None:
        """Release the shared radio operation lock."""
        if self._operation_lock and self._operation_lock.locked():
            self._operation_lock.release()
            logger.debug("Released radio operation lock (%s)", name)
        else:
            logger.error("Attempted to release unlocked radio operation lock (%s)", name)

    @asynccontextmanager
    async def radio_operation(
        self,
        name: str,
        *,
        pause_polling: bool = False,
        suspend_auto_fetch: bool = False,
        blocking: bool = True,
    ):
        """Acquire shared radio lock and optionally pause polling / auto-fetch.

        After acquiring the lock, resolves the current MeshCore instance and
        yields it.  Callers get a fresh reference via ``async with ... as mc:``,
        avoiding stale-reference bugs when a reconnect swaps ``_meshcore``
        between the pre-check and the lock acquisition.

        Args:
            name: Human-readable operation name for logs/errors.
            pause_polling: Pause fallback message polling while held.
            suspend_auto_fetch: Stop MeshCore auto message fetching while held.
            blocking: If False, fail immediately when lock is held.

        Raises:
            RadioDisconnectedError: If the radio disconnected before the lock
                was acquired (``_meshcore`` is ``None``).
        """
        await self._acquire_operation_lock(name, blocking=blocking)

        mc = self._meshcore
        if mc is None:
            self._release_operation_lock(name)
            raise RadioDisconnectedError("Radio disconnected")

        poll_context = nullcontext()
        if pause_polling:
            from app.radio_sync import pause_polling as pause_polling_context

            poll_context = pause_polling_context()

        auto_fetch_paused = False

        try:
            async with poll_context:
                if suspend_auto_fetch:
                    await mc.stop_auto_message_fetching()
                    auto_fetch_paused = True
                yield mc
        finally:
            try:
                if auto_fetch_paused:
                    try:
                        await mc.start_auto_message_fetching()
                    except Exception as e:
                        logger.warning("Failed to restart auto message fetching (%s): %s", name, e)
            finally:
                self._release_operation_lock(name)

    async def post_connect_setup(self) -> None:
        """Run shared post-connection orchestration after transport setup succeeds."""
        from app.services.radio_lifecycle import run_post_connect_setup

        await run_post_connect_setup(self)

    def reset_channel_send_cache(self) -> None:
        """Forget any session-local channel-slot reuse state."""
        self._channel_slot_by_key.clear()
        self._channel_key_by_slot.clear()

    def channel_slot_reuse_enabled(self) -> bool:
        """Return whether this transport can safely reuse cached channel slots."""
        if settings.force_channel_slot_reconfigure:
            return False
        if self._connection_info:
            return not self._connection_info.startswith("TCP:")
        return settings.connection_type != "tcp"

    def get_channel_send_cache_capacity(self) -> int:
        """Return the app-managed channel cache capacity for the current session."""
        try:
            return max(1, int(self.max_channels))
        except (TypeError, ValueError):
            return 1

    def get_cached_channel_slot(self, channel_key: str) -> int | None:
        """Return the cached radio slot for a channel key, if present."""
        return self._channel_slot_by_key.get(channel_key.upper())

    def plan_channel_send_slot(
        self,
        channel_key: str,
        *,
        preferred_slot: int = 0,
    ) -> tuple[int, bool, str | None]:
        """Choose a radio slot for a channel send.

        Returns `(slot, needs_configure, evicted_channel_key)`.
        """
        if not self.channel_slot_reuse_enabled():
            return preferred_slot, True, None

        normalized_key = channel_key.upper()
        cached_slot = self._channel_slot_by_key.get(normalized_key)
        if cached_slot is not None:
            return cached_slot, False, None

        capacity = self.get_channel_send_cache_capacity()
        if len(self._channel_slot_by_key) < capacity:
            slot = self._find_first_free_channel_slot(capacity, preferred_slot)
            return slot, True, None

        evicted_key, slot = next(iter(self._channel_slot_by_key.items()))
        return slot, True, evicted_key

    def note_channel_slot_loaded(self, channel_key: str, slot: int) -> None:
        """Record that a channel is now resident in the given radio slot."""
        if not self.channel_slot_reuse_enabled():
            return

        normalized_key = channel_key.upper()
        previous_slot = self._channel_slot_by_key.pop(normalized_key, None)
        if previous_slot is not None and previous_slot != slot:
            self._channel_key_by_slot.pop(previous_slot, None)

        displaced_key = self._channel_key_by_slot.get(slot)
        if displaced_key is not None and displaced_key != normalized_key:
            self._channel_slot_by_key.pop(displaced_key, None)

        self._channel_key_by_slot[slot] = normalized_key
        self._channel_slot_by_key[normalized_key] = slot

    def note_channel_slot_used(self, channel_key: str) -> None:
        """Refresh LRU order for a previously loaded channel slot."""
        if not self.channel_slot_reuse_enabled():
            return

        normalized_key = channel_key.upper()
        slot = self._channel_slot_by_key.get(normalized_key)
        if slot is None:
            return
        self._channel_slot_by_key.move_to_end(normalized_key)
        self._channel_key_by_slot[slot] = normalized_key

    def invalidate_cached_channel_slot(self, channel_key: str) -> None:
        """Drop any cached slot assignment for a channel key."""
        normalized_key = channel_key.upper()
        slot = self._channel_slot_by_key.pop(normalized_key, None)
        if slot is None:
            return
        if self._channel_key_by_slot.get(slot) == normalized_key:
            self._channel_key_by_slot.pop(slot, None)

    def get_channel_send_cache_snapshot(self) -> list[tuple[str, int]]:
        """Return the current channel send cache contents in LRU order."""
        return list(self._channel_slot_by_key.items())

    def _find_first_free_channel_slot(self, capacity: int, preferred_slot: int) -> int:
        """Pick the first unclaimed app-managed slot, preferring the requested slot."""
        if preferred_slot < capacity and preferred_slot not in self._channel_key_by_slot:
            return preferred_slot

        for slot in range(capacity):
            if slot not in self._channel_key_by_slot:
                return slot

        return preferred_slot

    @property
    def meshcore(self) -> MeshCore | None:
        return self._meshcore

    @property
    def connection_info(self) -> str | None:
        return self._connection_info

    @property
    def is_connected(self) -> bool:
        return self._meshcore is not None and self._meshcore.is_connected

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnect_lock is not None and self._reconnect_lock.locked()

    @property
    def is_setup_in_progress(self) -> bool:
        return self._setup_in_progress

    @property
    def is_setup_complete(self) -> bool:
        return self._setup_complete

    @property
    def connection_desired(self) -> bool:
        return self._connection_desired

    def resume_connection(self) -> None:
        """Allow connection monitor and manual reconnects to establish transport again."""
        self._connection_desired = True

    async def pause_connection(self) -> None:
        """Stop automatic reconnect attempts and tear down any current transport."""
        self._connection_desired = False
        self._last_connected = False
        await self.disconnect()

    async def _disable_meshcore_auto_reconnect(self, mc: MeshCore) -> None:
        """Disable library-managed reconnects so manual teardown fully releases transport."""
        connection_manager = getattr(mc, "connection_manager", None)
        if connection_manager is None:
            return

        if hasattr(connection_manager, "auto_reconnect"):
            connection_manager.auto_reconnect = False

        reconnect_task = getattr(connection_manager, "_reconnect_task", None)
        if reconnect_task is None or not isinstance(reconnect_task, asyncio.Task | asyncio.Future):
            return

        reconnect_task.cancel()
        try:
            await reconnect_task
        except asyncio.CancelledError:
            pass
        finally:
            connection_manager._reconnect_task = None

    async def connect(self) -> None:
        """Connect to the radio using the configured transport."""
        if self._meshcore is not None:
            await self.disconnect()

        connection_type = settings.connection_type
        if connection_type == "tcp":
            await self._connect_tcp()
        elif connection_type == "ble":
            await self._connect_ble()
        else:
            await self._connect_serial()

    async def _connect_serial(self) -> None:
        """Connect to the radio over serial."""
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
        self._connection_info = f"Serial: {port}"
        self._last_connected = True
        self._setup_complete = False
        logger.debug("Serial connection established")

    async def _connect_tcp(self) -> None:
        """Connect to the radio over TCP."""
        host = settings.tcp_host
        port = settings.tcp_port

        logger.debug("Connecting to radio at %s:%d (TCP)", host, port)
        self._meshcore = await MeshCore.create_tcp(
            host=host,
            port=port,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        self._connection_info = f"TCP: {host}:{port}"
        self._last_connected = True
        self._setup_complete = False
        logger.debug("TCP connection established")

    async def _connect_ble(self) -> None:
        """Connect to the radio over BLE."""
        address = settings.ble_address
        pin = settings.ble_pin

        logger.debug("Connecting to radio at %s (BLE)", address)
        self._meshcore = await MeshCore.create_ble(
            address=address,
            pin=pin,
            auto_reconnect=True,
            max_reconnect_attempts=15,
        )
        self._connection_info = f"BLE: {address}"
        self._last_connected = True
        self._setup_complete = False
        logger.debug("BLE connection established")

    async def disconnect(self) -> None:
        """Disconnect from the radio."""
        if self._meshcore is not None:
            logger.debug("Disconnecting from radio")
            mc = self._meshcore
            await self._disable_meshcore_auto_reconnect(mc)
            await mc.disconnect()
            await self._disable_meshcore_auto_reconnect(mc)
            self._meshcore = None
            self._setup_complete = False
            self.max_channels = 40
            self.path_hash_mode = 0
            self.path_hash_mode_supported = False
            self.reset_channel_send_cache()
            logger.debug("Radio disconnected")

    async def reconnect(self, *, broadcast_on_success: bool = True) -> bool:
        """Attempt to reconnect to the radio.

        Returns True if reconnection was successful, False otherwise.
        Uses a lock to prevent concurrent reconnection attempts.
        """
        from app.websocket import broadcast_error, broadcast_health

        # Lazily initialize lock (can't create in __init__ before event loop exists)
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()

        async with self._reconnect_lock:
            if not self._connection_desired:
                logger.info("Reconnect skipped because connection is paused by operator")
                return False

            # If we became connected while waiting for the lock (another
            # reconnect succeeded ahead of us), skip the redundant attempt.
            if self.is_connected:
                logger.debug("Already connected after acquiring lock, skipping reconnect")
                return True

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

                if not self._connection_desired:
                    logger.info("Reconnect completed after pause request; disconnecting transport")
                    await self.disconnect()
                    return False

                if self.is_connected:
                    logger.info("Radio reconnected successfully at %s", self._connection_info)
                    if broadcast_on_success:
                        broadcast_health(True, self._connection_info)
                    return True
                else:
                    logger.warning("Reconnection failed: not connected after connect()")
                    return False

            except Exception as e:
                logger.warning("Reconnection failed: %s", e, exc_info=True)
                broadcast_error("Reconnection failed", str(e))
                return False

    async def start_connection_monitor(self) -> None:
        """Start background task to monitor connection and auto-reconnect."""
        from app.services.radio_lifecycle import connection_monitor_loop

        if self._reconnect_task is not None:
            return

        self._reconnect_task = asyncio.create_task(connection_monitor_loop(self))
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
