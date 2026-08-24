from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.path_utils import normalize_contact_route, normalize_route_override

# Valid MeshCore contact types: 0=unknown, 1=client, 2=repeater, 3=room, 4=sensor.
# Corrupted radio data can produce values outside this range.
_VALID_CONTACT_TYPES = frozenset({0, 1, 2, 3, 4})


class ContactRoute(BaseModel):
    """A normalized contact route."""

    path: str = Field(description="Hex-encoded path bytes (empty string for direct/flood)")
    path_len: int = Field(description="Hop count (-1=flood, 0=direct, >0=explicit route)")
    path_hash_mode: int = Field(
        description="Path hash mode (-1=flood, 0=1-byte, 1=2-byte, 2=3-byte hop identifiers)"
    )


class ContactUpsert(BaseModel):
    """Typed write contract for contacts persisted to SQLite."""

    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0
    flags: int = 0
    direct_path: str | None = None
    direct_path_len: int | None = None
    direct_path_hash_mode: int | None = None
    direct_path_updated_at: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool | None = None
    last_contacted: int | None = None
    first_seen: int | None = None
    mcmp_enabled: bool = False  # <-- added

    @classmethod
    def from_contact(cls, contact: Contact, **changes) -> ContactUpsert:
        return cls.model_validate(
            {
                **contact.model_dump(exclude={"last_read_at"}),
                **changes,
            }
        )

    @classmethod
    def from_radio_dict(
        cls, public_key: str, radio_data: dict, on_radio: bool = False
    ) -> ContactUpsert:
        """Convert radio contact data to the contact-row write shape."""
        direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
            radio_data.get("out_path"),
            radio_data.get("out_path_len", -1),
            radio_data.get(
                "out_path_hash_mode",
                -1 if radio_data.get("out_path_len", -1) == -1 else 0,
            ),
        )
        # Clamp invalid contact types to 0 (unknown) — corrupted radio data
        # can produce values like 111 or 240 that break downstream branching.
        raw_type = radio_data.get("type", 0)
        contact_type = raw_type if raw_type in _VALID_CONTACT_TYPES else 0

        # Null out impossible coordinates — the contact is still ingested,
        # but garbage lat/lon (e.g. 1953.7) is discarded rather than stored.
        lat = radio_data.get("adv_lat")
        lon = radio_data.get("adv_lon")
        if lat is not None and not (-90 <= lat <= 90):
            lat = None
        if lon is not None and not (-180 <= lon <= 180):
            lon = None

        return cls(
            public_key=public_key,
            name=radio_data.get("adv_name"),
            type=contact_type,
            flags=radio_data.get("flags", 0),
            direct_path=direct_path,
            direct_path_len=direct_path_len,
            direct_path_hash_mode=direct_path_hash_mode,
            lat=lat,
            lon=lon,
            last_advert=radio_data.get("last_advert"),
            on_radio=on_radio,
        )


class Contact(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0  # 0=unknown, 1=client, 2=repeater, 3=room, 4=sensor
    flags: int = 0
    direct_path: str | None = None
    direct_path_len: int = -1
    direct_path_hash_mode: int = -1
    direct_path_updated_at: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool = False
    favorite: bool = False
    last_contacted: int | None = None  # Last time we sent/received a message
    last_read_at: int | None = None  # Server-side read state tracking
    first_seen: int | None = None
    mcmp_enabled: bool = False  # <-- added
    effective_route: ContactRoute | None = None
    effective_route_source: Literal["override", "direct", "flood"] = "flood"
    direct_route: ContactRoute | None = None
    route_override: ContactRoute | None = None

    def model_post_init(self, __context) -> None:
        direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
            self.direct_path,
            self.direct_path_len,
            self.direct_path_hash_mode,
        )
        self.direct_path = direct_path or None
        self.direct_path_len = direct_path_len
        self.direct_path_hash_mode = direct_path_hash_mode

        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                self.route_override_path,
                self.route_override_len,
                self.route_override_hash_mode,
            )
        )
        self.route_override_path = route_override_path or None
        self.route_override_len = route_override_len
        self.route_override_hash_mode = route_override_hash_mode
        if (
            route_override_path is not None
            and route_override_len is not None
            and route_override_hash_mode is not None
        ):
            self.route_override = ContactRoute(
                path=route_override_path,
                path_len=route_override_len,
                path_hash_mode=route_override_hash_mode,
            )
        else:
            self.route_override = None

        if direct_path_len >= 0:
            self.direct_route = ContactRoute(
                path=direct_path,
                path_len=direct_path_len,
                path_hash_mode=direct_path_hash_mode,
            )
        else:
            self.direct_route = None

        path, path_len, path_hash_mode = self.effective_route_tuple()
        if self.has_route_override():
            self.effective_route_source = "override"
        elif self.direct_route is not None:
            self.effective_route_source = "direct"
        else:
            self.effective_route_source = "flood"
        self.effective_route = ContactRoute(
            path=path,
            path_len=path_len,
            path_hash_mode=path_hash_mode,
        )

    def has_route_override(self) -> bool:
        return self.route_override_len is not None

    def effective_route_tuple(self) -> tuple[str, int, int]:
        if self.has_route_override():
            return normalize_contact_route(
                self.route_override_path,
                self.route_override_len,
                self.route_override_hash_mode,
            )
        if self.direct_path_len >= 0:
            return normalize_contact_route(
                self.direct_path,
                self.direct_path_len,
                self.direct_path_hash_mode,
            )
        return "", -1, -1

    def to_radio_dict(self) -> dict:
        """Convert to the dict format expected by meshcore radio commands.

        The radio API uses different field names (adv_name, out_path, etc.)
        than our database schema (name, direct_path, etc.).
        """
        effective_path, effective_path_len, effective_path_hash_mode = self.effective_route_tuple()
        return {
            "public_key": self.public_key,
            "adv_name": self.name or "",
            "type": self.type,
            "flags": self.flags,
            "out_path": effective_path,
            "out_path_len": effective_path_len,
            "out_path_hash_mode": effective_path_hash_mode,
            "adv_lat": self.lat if self.lat is not None else 0.0,
            "adv_lon": self.lon if self.lon is not None else 0.0,
            "last_advert": self.last_advert if self.last_advert is not None else 0,
        }

    def to_upsert(self, **changes) -> ContactUpsert:
        """Convert the stored contact to the repository's write contract."""
        return ContactUpsert.from_contact(self, **changes)


class CreateContactRequest(BaseModel):
    """Request to create a new contact."""

    public_key: str = Field(min_length=64, max_length=64, description="Public key (64-char hex)")
    name: str | None = Field(default=None, description="Display name for the contact")
    type: int = Field(
        default=0, ge=0, le=3, description="Contact type (0=unknown, 1=client, 2=repeater, 3=room)"
    )
    try_historical: bool = Field(
        default=False,
        description="Attempt to decrypt historical DM packets for this contact",
    )


class ContactRoutingOverrideRequest(BaseModel):
    """Request to set, force, or clear a contact routing override."""

    route: str = Field(
        description=(
            "Blank clears the override, "
            '"-1" forces flood, "0" forces direct, and explicit routes are '
            "comma-separated 1/2/3-byte hop hex values"
        )
    )


# Contact type constants
CONTACT_TYPE_REPEATER = 2
CONTACT_TYPE_ROOM = 3


class ContactAdvertPath(BaseModel):
    """A unique advert path observed for a contact."""

    path: str = Field(description="Hex-encoded routing path (empty string for direct)")
    path_len: int = Field(description="Number of hops in the path")
    next_hop: str | None = Field(
        default=None,
        description="First hop toward us as a full hop identifier, or null for direct",
    )
    first_seen: int = Field(description="Unix timestamp of first observation")
    last_seen: int = Field(description="Unix timestamp of most recent observation")
    heard_count: int = Field(description="Number of times this unique path was heard")


class ContactAdvertPathSummary(BaseModel):
    """Recent unique advertisement paths for a single contact."""

    public_key: str = Field(description="Contact public key (64-char hex)")
    paths: list[ContactAdvertPath] = Field(
        default_factory=list, description="Most recent unique advert paths"
    )


class ContactNameHistory(BaseModel):
    """A historical name used by a contact."""

    name: str
    first_seen: int
    last_seen: int


class ContactActiveRoom(BaseModel):
    """A channel where a contact has been active."""

    channel_key: str
    channel_name: str
    message_count: int


class NearestRepeater(BaseModel):
    """A repeater that has relayed a contact's advertisements."""

    public_key: str
    name: str | None = None
    path_len: int
    last_seen: int
    heard_count: int


class ContactAnalyticsHourlyBucket(BaseModel):
    """A single hourly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the hour bucket")
    last_24h_count: int = 0
    last_week_average: float = 0
    all_time_average: float = 0


class ContactAnalyticsWeeklyBucket(BaseModel):
    """A single weekly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the 7-day bucket")
    message_count: int = 0


class ContactAnalytics(BaseModel):
    """Unified contact analytics payload for keyed and name-only lookups."""

    lookup_type: Literal["contact", "name"]
    name: str
    contact: Contact | None = None
    name_first_seen_at: int | None = None
    name_history: list[ContactNameHistory] = Field(default_factory=list)
    dm_message_count: int = 0
    channel_message_count: int = 0
    includes_direct_messages: bool = False
    most_active_rooms: list[ContactActiveRoom] = Field(default_factory=list)
    advert_paths: list[ContactAdvertPath] = Field(default_factory=list)
    advert_frequency: float | None = Field(
        default=None,
        description="Advert observations per hour (includes multi-path arrivals of same advert)",
    )
    nearest_repeaters: list[NearestRepeater] = Field(default_factory=list)
    hourly_activity: list[ContactAnalyticsHourlyBucket] = Field(default_factory=list)
    weekly_activity: list[ContactAnalyticsWeeklyBucket] = Field(default_factory=list)


class Channel(BaseModel):
    key: str = Field(description="Channel key (32-char hex)")
    name: str
    is_hashtag: bool = False
    on_radio: bool = False
    flood_scope_override: str | None = Field(
        default=None,
        description=(
            "Per-channel outbound flood scope override, tri-state: null = inherit the "
            "global app setting; '*' (UNSCOPED_OVERRIDE_MARKER) = force unscoped/plain "
            "flood even over a scoped global; a region name (e.g. '#Esperance') = scope "
            "this channel."
        ),
    )
    path_hash_mode_override: int | None = Field(
        default=None,
        description="Per-channel path hash mode override (0=1-byte, 1=2-byte, 2=3-byte, null = use radio default)",
    )
    last_read_at: int | None = None  # Server-side read state tracking
    favorite: bool = False
    muted: bool = False
    mcmp_enabled: bool = False             # <-- added
    mcmp_sign_enabled: bool = False        # <-- added


class ChannelMessageCounts(BaseModel):
    """Time-windowed message counts for a channel."""

    last_1h: int = 0
    last_24h: int = 0
    last_48h: int = 0
    last_7d: int = 0
    all_time: int = 0


class ChannelTopSender(BaseModel):
    """A top sender in a channel over the last 24 hours."""

    sender_name: str
    sender_key: str | None = None
    message_count: int


class PathHashWidthStats(BaseModel):
    """Hop byte width distribution for parsed raw packets."""

    total_packets: int = 0
    single_byte: int = 0
    double_byte: int = 0
    triple_byte: int = 0
    single_byte_pct: float = 0.0
    double_byte_pct: float = 0.0
    triple_byte_pct: float = 0.0


class ChannelDetail(BaseModel):
    """Comprehensive channel profile data."""

    channel: Channel
    message_counts: ChannelMessageCounts = Field(default_factory=ChannelMessageCounts)
    first_message_at: int | None = None
    unique_sender_count: int = 0
    top_senders_24h: list[ChannelTopSender] = Field(default_factory=list)
    path_hash_width_24h: PathHashWidthStats = Field(default_factory=PathHashWidthStats)


class MessagePath(BaseModel):
    """A single path that a message took to reach us."""

    path: str = Field(description="Hex-encoded routing path")
    received_at: int = Field(description="Unix timestamp when this path was received")
    path_len: int | None = Field(
        default=None,
        description="Hop count. None = legacy (infer as len(path)//2, i.e. 1-byte hops)",
    )
    rssi: int | None = Field(default=None, description="Last-hop RSSI in dBm")
    snr: float | None = Field(default=None, description="Last-hop SNR in dB")


class Message(BaseModel):
    id: int
    type: str = Field(description="PRIV or CHAN")
    conversation_key: str = Field(description="User pubkey for PRIV, channel key for CHAN")
    text: str
    sender_timestamp: int | None = None
    received_at: int
    paths: list[MessagePath] | None = Field(
        default=None, description="List of routing paths this message arrived via"
    )
    txt_type: int = 0
    signature: str | None = None
    sender_key: str | None = None
    outgoing: bool = False
    acked: int = 0
    sender_name: str | None = None
    channel_name: str | None = None
    packet_id: int | None = Field(
        default=None,
        description="Representative raw packet row ID when archival raw bytes exist",
    )
    transport_code: int | None = Field(
        default=None,
        description=(
            "Region scope transport code (uint16) when the message arrived via a "
            "TransportFlood/TransportDirect packet; None for unscoped (plain flood) messages"
        ),
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )


class MessagesAroundResponse(BaseModel):
    messages: list[Message]
    has_older: bool
    has_newer: bool


class ResendChannelMessageResponse(BaseModel):
    status: str
    message_id: int
    message: Message | None = None


class RawPacketDecryptedInfo(BaseModel):
    """Decryption info for a raw packet (when successfully decrypted)."""

    channel_name: str | None = None
    sender: str | None = None
    channel_key: str | None = None
    contact_key: str | None = None
    sender_timestamp: int | None = None
    message: str | None = None


class RawPacketBroadcast(BaseModel):
    """Raw packet payload broadcast via WebSocket.

    This extends the database model with runtime-computed fields
    like payload_type, snr, rssi, and decryption info.
    """

    id: int
    observation_id: int = Field(
        description=(
            "Monotonic per-process ID for this RF observation (distinct from the DB packet row ID)"
        )
    )
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g., GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB")
    rssi: int | None = Field(default=None, description="Received signal strength in dBm")
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None
    transport_code: int | None = Field(
        default=None,
        description="Region scope transport code (uint16) for TransportFlood/TransportDirect packets",
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )


class RawPacketDetail(BaseModel):
    """Stored raw-packet detail returned by the packet API."""

    id: int
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g. GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB if available")
    rssi: int | None = Field(
        default=None, description="Received signal strength in dBm if available"
    )
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None
    transport_code: int | None = Field(
        default=None,
        description="Region scope transport code (uint16) for TransportFlood/TransportDirect packets",
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class SendDirectMessageRequest(SendMessageRequest):
    destination: str = Field(
        description="Recipient public key (64-char hex preferred; prefix must resolve uniquely)"
    )


class SendChannelMessageRequest(SendMessageRequest):
    channel_key: str = Field(description="Channel key (32-char hex)")
    flood_scope_override: str | None = Field(
        default=None,
        description=(
            "Per-send regional flood-scope override. None = use the channel's persisted "
            "override (or none); empty string = force unscoped/plain flood; a region name "
            "scopes this single send to that region. Takes precedence over the channel's "
            "persisted flood_scope_override for this send only."
        ),
    )


class RepeaterLoginRequest(BaseModel):
    """Request to log in to a repeater."""

    password: str = Field(
        default="", description="Repeater password (empty string for guest login)"
    )


class RepeaterLoginResponse(BaseModel):
    """Response from repeater login."""

    status: str = Field(description="Login result status")
    authenticated: bool = Field(description="Whether repeater authentication was confirmed")
    message: str | None = Field(
        default=None,
        description="Optional warning or error message when authentication was not confirmed",
    )


class RepeaterStatusResponse(BaseModel):
    """Status telemetry from a repeater (single attempt, no retries)."""

    battery_volts: float = Field(description="Battery voltage in volts")
    tx_queue_len: int = Field(description="Transmit queue length")
    noise_floor_dbm: int = Field(description="Noise floor in dBm")
    last_rssi_dbm: int = Field(description="Last RSSI in dBm")
    last_snr_db: float = Field(description="Last SNR in dB")
    packets_received: int = Field(description="Total packets received")
    packets_sent: int = Field(description="Total packets sent")
    airtime_seconds: int = Field(description="TX airtime in seconds")
    rx_airtime_seconds: int = Field(description="RX airtime in seconds")
    uptime_seconds: int = Field(description="Uptime in seconds")
    sent_flood: int = Field(description="Flood packets sent")
    sent_direct: int = Field(description="Direct packets sent")
    recv_flood: int = Field(description="Flood packets received")
    recv_direct: int = Field(description="Direct packets received")
    flood_dups: int = Field(description="Duplicate flood packets")
    direct_dups: int = Field(description="Duplicate direct packets")
    full_events: int = Field(description="Full event queue count")
    recv_errors: int | None = Field(default=None, description="Radio-level RX packet errors")
    telemetry_history: list[TelemetryHistoryEntry] = Field(
        default_factory=list, description="Recent telemetry history snapshots"
    )


class RepeaterNodeInfoResponse(BaseModel):
    """Identity/location info from a repeater (small CLI batch)."""

    name: str | None = Field(default=None, description="Repeater name")
    lat: str | None = Field(default=None, description="Latitude")
    lon: str | None = Field(default=None, description="Longitude")
    clock_utc: str | None = Field(default=None, description="Repeater clock in UTC")


class RepeaterRadioSettingsResponse(BaseModel):
    """Radio settings from a repeater (radio/config CLI batch)."""

    firmware_version: str | None = Field(default=None, description="Firmware version string")
    radio: str | None = Field(default=None, description="Radio settings (freq,bw,sf,cr)")
    tx_power: str | None = Field(default=None, description="TX power in dBm")
    airtime_factor: str | None = Field(default=None, description="Airtime factor")
    duty_cycle_limit: str | None = Field(
        default=None,
        description=(
            "Configured duty-cycle limit as a percentage string (e.g. '25.0%'), derived "
            "by firmware from airtime_factor (100/(af+1)). This is the configured ceiling, "
            "not the current measured duty cycle. Only available on firmware >= 1.15; None "
            "on older nodes that don't support 'get dutycycle'."
        ),
    )
    repeat_enabled: str | None = Field(default=None, description="Repeat mode enabled")
    flood_max: str | None = Field(default=None, description="Max flood hops")


class RepeaterAdvertIntervalsResponse(BaseModel):
    """Advertisement intervals from a repeater."""

    advert_interval: str | None = Field(default=None, description="Local advert interval")
    flood_advert_interval: str | None = Field(default=None, description="Flood advert interval")


class RepeaterOwnerInfoResponse(BaseModel):
    """Owner info, firmware, and guest password from a repeater.

    ``owner_info``, ``firmware_version``, and ``name`` come from the
    guest-accessible binary owner-info request (REQ_TYPE_GET_OWNER_INFO / 0x07).
    ``guest_password`` is admin-only and still comes from the CLI, so guests see
    ``None`` for it.
    """

    owner_info: str | None = Field(default=None, description="Owner info string")
    firmware_version: str | None = Field(
        default=None, description="Firmware version string (from binary owner-info request)"
    )
    name: str | None = Field(
        default=None, description="Repeater name (from binary owner-info request)"
    )
    guest_password: str | None = Field(default=None, description="Guest password (admin only)")


class RepeaterRegionEntry(BaseModel):
    """One region from a repeater's region hierarchy dump."""

    name: str = Field(description="Region name ('*' is the wildcard/global root)")
    depth: int = Field(description="Indentation depth in the hierarchy (0 = root)")
    flood_allowed: bool = Field(description="True if flood is allowed for this region")
    is_home: bool = Field(description="True if this is the repeater's home region")


class RepeaterRegionsResponse(BaseModel):
    """Region hierarchy and flood permissions from a repeater.

    Primary source is the admin `region` CLI dump — an indented tree capped at
    ~160 chars, so large region sets can be truncated (``truncated`` flags this).
    When the CLI is unavailable (e.g. guest access), ``source`` is ``"anon"`` and
    ``regions`` is the guest-accessible anon request's flat list of flood-allowed
    region names only — no hierarchy, no blocked regions, no home marker. See
    issue #309.
    """

    regions: list[RepeaterRegionEntry] = Field(default_factory=list)
    raw: str | None = Field(default=None, description="Raw CLI dump text as received")
    truncated: bool = Field(default=False, description="True if the dump was likely truncated")
    source: Literal["cli", "anon"] | None = Field(
        default=None,
        description="'cli' = full admin hierarchy; 'anon' = guest flood-allowed names only",
    )


class LppSensor(BaseModel):
    """A single CayenneLPP sensor reading from req_telemetry_sync."""

    channel: int = Field(description="LPP channel number")
    type_name: str = Field(description="Sensor type name (e.g. temperature, humidity)")
    value: float | dict = Field(
        description="Scalar value or dict for multi-value sensors (GPS, accel)"
    )


class RepeaterLppTelemetryResponse(BaseModel):
    """CayenneLPP sensor telemetry from a repeater."""

    sensors: list[LppSensor] = Field(default_factory=list, description="List of sensor readings")


class ContactTelemetryResponse(BaseModel):
    """On-demand CayenneLPP telemetry snapshot from any contact."""

    sensors: list[LppSensor] = Field(default_factory=list, description="List of sensor readings")
    fetched_at: int = Field(description="Unix timestamp when this telemetry was fetched")
    telemetry_history: list[TelemetryHistoryEntry] = Field(
        default_factory=list, description="Recent telemetry history entries"
    )


class NeighborInfo(BaseModel):
    """Information about a neighbor seen by a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (4-12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    snr: float = Field(description="Signal-to-noise ratio in dB")
    last_heard_seconds: int = Field(description="Seconds since last heard")


class AclEntry(BaseModel):
    """Access control list entry for a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    permission: int = Field(
        description="Permission level: 0=Guest, 1=Read-only, 2=Read-write, 3=Admin"
    )
    permission_name: str = Field(description="Human-readable permission name")


class RepeaterNeighborsResponse(BaseModel):
    """Neighbors list from a repeater."""

    neighbors: list[NeighborInfo] = Field(
        default_factory=list, description="List of neighbors seen by repeater"
    )
    reported_count: int | None = Field(
        default=None,
        description=(
            "Total neighbor count reported by the repeater firmware, independent of "
            "how many entries were actually returned. May exceed len(neighbors) when a "
            "multi-chunk fetch is incomplete (dropped follow-up query / duty-cycle throttle)."
        ),
    )


class RepeaterAclResponse(BaseModel):
    """ACL list from a repeater."""

    acl: list[AclEntry] = Field(default_factory=list, description="Access control list")


class TraceResponse(BaseModel):
    """Result of a direct (zero-hop) trace to a contact."""

    remote_snr: float | None = Field(
        default=None, description="SNR at which the target heard us (dB)"
    )
    local_snr: float | None = Field(
        default=None, description="SNR at which we heard the target on the bounce-back (dB)"
    )
    path_len: int = Field(description="Number of hops in the trace path")


class RadioTraceHopRequest(BaseModel):
    """One requested hop in a radio trace path."""

    public_key: str | None = Field(
        default=None,
        description="Full repeater public key when this hop maps to a known repeater",
    )
    hop_hex: str | None = Field(
        default=None,
        description="Raw hop hash hex when using a custom repeater prefix",
    )


class RadioTraceRequest(BaseModel):
    """Ordered trace path for a radio trace loop."""

    hop_hash_bytes: Literal[1, 2, 4] = Field(
        default=4,
        description="Hash width in bytes for every hop in this trace path",
    )
    hops: list[RadioTraceHopRequest] = Field(
        min_length=1,
        description="Ordered repeater hops, using either known repeater keys or custom hop hex",
    )


class RadioTraceNode(BaseModel):
    """One resolved node in a radio trace result."""

    role: Literal["repeater", "custom", "local"] = Field(description="Node role in the trace")
    public_key: str | None = Field(
        default=None,
        description="Resolved full public key for this node when known",
    )
    name: str | None = Field(default=None, description="Display name for this node when known")
    observed_hash: str | None = Field(
        default=None,
        description="Observed 4-byte trace hash for this node as hex",
    )
    snr: float | None = Field(default=None, description="Reported SNR for this node in dB")


class RadioTraceResponse(BaseModel):
    """Resolved multi-hop radio trace result."""

    path_len: int = Field(description="Number of hashed nodes returned by the trace response")
    timeout_seconds: float = Field(description="Timeout window used while waiting for the trace")
    nodes: list[RadioTraceNode] = Field(
        default_factory=list,
        description="Ordered trace nodes: repeater hops followed by the terminal local radio",
    )


class PathDiscoveryRoute(BaseModel):
    """One resolved route returned by contact path discovery."""

    path: str = Field(description="Hex-encoded path bytes")
    path_len: int = Field(description="Hop count for this route")
    path_hash_mode: int = Field(
        description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte hop identifiers)"
    )


class PathDiscoveryResponse(BaseModel):
    """Round-trip routing data for a contact path discovery request."""

    contact: Contact = Field(
        description="Updated contact row after saving the learned forward path"
    )
    forward_path: PathDiscoveryRoute = Field(
        description="Route used from the local radio to the target contact"
    )
    return_path: PathDiscoveryRoute = Field(
        description="Route used from the target contact back to the local radio"
    )


class CommandRequest(BaseModel):
    """Request to send a CLI command to a repeater."""

    command: str = Field(min_length=1, description="CLI command to send")


class CommandResponse(BaseModel):
    """Response from a repeater CLI command."""

    command: str = Field(description="The command that was sent")
    response: str = Field(description="Response from the repeater")
    sender_timestamp: int | None = Field(
        default=None, description="Timestamp from the repeater's response"
    )


class RadioDiscoveryRequest(BaseModel):
    """Request to discover nearby mesh nodes from the local radio."""

    target: Literal["repeaters", "sensors", "all"] = Field(
        default="all",
        description="Which node classes to discover over the mesh",
    )


class RadioDiscoveryResult(BaseModel):
    """One mesh node heard during a discovery sweep."""

    public_key: str = Field(description="Discovered node public key as hex")
    name: str | None = Field(
        default=None,
        description="Known name for this node from contacts DB, if any",
    )
    node_type: Literal["repeater", "sensor"] = Field(description="Discovered node class")
    heard_count: int = Field(default=1, description="How many responses were heard from this node")
    local_snr: float | None = Field(
        default=None,
        description="SNR at which the local radio heard the response (dB)",
    )
    local_rssi: int | None = Field(
        default=None,
        description="RSSI at which the local radio heard the response (dBm)",
    )
    remote_snr: float | None = Field(
        default=None,
        description="SNR reported by the remote node while hearing our discovery request (dB)",
    )


class RadioDiscoveryResponse(BaseModel):
    """Response payload for a mesh discovery sweep."""

    target: Literal["repeaters", "sensors", "all"] = Field(
        description="Which node classes were requested"
    )
    duration_seconds: float = Field(description="How long the sweep listened for responses")
    results: list[RadioDiscoveryResult] = Field(
        default_factory=list,
        description="Deduplicated discovery responses heard during the sweep",
    )


class RadioRegionDiscoveryRequest(BaseModel):
    """Request to sweep nearby repeaters for their flood-allowed region names.

    Uses the guest-accessible anon regions request (direct-routed, so only
    repeaters in range answer). When ``public_keys`` is omitted, the sweep
    targets the most recently seen repeater contacts.
    """

    public_keys: list[str] | None = Field(
        default=None,
        description="Specific repeater public keys to query; None = most recent repeater contacts",
    )
    max_repeaters: int = Field(
        default=8,
        ge=1,
        le=40,
        description="Maximum number of repeaters to query in one sweep",
    )


class RadioRegionDiscoveryRepeater(BaseModel):
    """One repeater's result from a region discovery sweep."""

    public_key: str = Field(description="Repeater public key")
    name: str | None = Field(default=None, description="Known contact name, if any")
    answered: bool = Field(description="True if the repeater answered the anon regions request")
    regions: list[str] = Field(
        default_factory=list,
        description="Flood-allowed region names reported by this repeater (wildcard excluded)",
    )


class RadioRegionDiscoveryResponse(BaseModel):
    """Aggregated result of a region discovery sweep across nearby repeaters.

    ``regions`` is the deduplicated union of every repeater's flood-allowed
    region names — the list an operator can merge into ``known_regions``. The
    anon request only reports flood-allowed names, so blocked regions and the
    hierarchy are not visible here (use the per-repeater admin regions pane for
    the full picture). See issue #309.
    """

    repeaters_queried: int = Field(description="How many repeaters were contacted")
    repeaters_answered: int = Field(description="How many repeaters answered the request")
    regions: list[str] = Field(
        default_factory=list,
        description="Deduplicated union of flood-allowed region names across all repeaters",
    )
    results: list[RadioRegionDiscoveryRepeater] = Field(
        default_factory=list,
        description="Per-repeater region results",
    )


class UnreadCounts(BaseModel):
    """Aggregated unread counts, mention flags, and last message times for all conversations."""

    counts: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> unread count"
    )
    mentions: dict[str, bool] = Field(
        default_factory=dict, description="Map of stateKey -> has mention"
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> last message timestamp"
    )
    first_unread_ids: dict[str, int | None] = Field(
        default_factory=dict,
        description=(
            "Map of stateKey -> id of the oldest unread message. Lets the client place "
            "the unread divider (and jump to it) without paging back through history."
        ),
    )
    last_read_ats: dict[str, int | None] = Field(
        default_factory=dict, description="Map of stateKey -> server-side last_read_at boundary"
    )


class AppSettings(BaseModel):
    """Application settings stored in the database."""

    max_radio_contacts: int = Field(
        default=200,
        description=(
            "Configured radio contact capacity used for maintenance thresholds; "
            "favorites reload first, then background fill targets about 80% of this value"
        ),
    )
    auto_decrypt_dm_on_advert: bool = Field(
        default=True,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict,
        description="Map of conversation state keys to last message timestamps",
    )
    advert_interval: int = Field(
        default=0,
        description="Periodic advertisement interval in seconds (0 = disabled)",
    )
    last_advert_time: int = Field(
        default=0,
        description="Unix timestamp of last advertisement sent (0 = never)",
    )
    flood_scope: str = Field(
        default="",
        description="Outbound flood scope / region name (empty = disabled, no tagging)",
    )
    known_regions: list[str] = Field(
        default_factory=list,
        description=(
            "Region scope names used to resolve incoming TransportFlood/TransportDirect "
            "packets back to a readable region label (packet inspector + channel decoration)"
        ),
    )
    blocked_keys: list[str] = Field(
        default_factory=list,
        description="Public keys whose messages are hidden from the UI",
    )
    blocked_names: list[str] = Field(
        default_factory=list,
        description="Display names whose messages are hidden from the UI",
    )
    discovery_blocked_types: list[int] = Field(
        default_factory=list,
        description=(
            "Contact type codes (1=Client, 2=Repeater, 3=Room, 4=Sensor) whose "
            "advertisements should not create new contacts; existing contacts are still updated"
        ),
    )
    tracked_telemetry_repeaters: list[str] = Field(
        default_factory=list,
        description="Public keys of repeaters opted into periodic telemetry collection (max 8)",
    )
    tracked_telemetry_contacts: list[str] = Field(
        default_factory=list,
        description="Public keys of contacts opted into periodic LPP telemetry collection (max 8)",
    )
    telemetry_interval_hours: int = Field(
        default=8,
        description=(
            "User-preferred telemetry collection interval in hours. The backend "
            "clamps this up to the shortest legal interval given the number of "
            "tracked repeaters and contacts so daily checks stay under a 24/day ceiling."
        ),
    )
    telemetry_routed_hourly: bool = Field(
        default=False,
        description=(
            "When enabled, tracked repeaters/contacts with a direct or routed (non-flood) "
            "path are polled every hour instead of on the normal scheduled interval."
        ),
    )
    auto_resend_channel: bool = Field(
        default=False,
        description=(
            "When enabled, outgoing channel messages that receive no echo within 2 seconds "
            "are automatically byte-perfect resent once (within the 30-second dedup window)"
        ),
    )


class BusyChannel(BaseModel):
    channel_key: str
    channel_name: str
    message_count: int


class ContactActivityCounts(BaseModel):
    last_hour: int
    last_24_hours: int
    last_week: int


class NoiseFloorSample(BaseModel):
    timestamp: int = Field(description="Unix timestamp of the sampled reading")
    noise_floor_dbm: int = Field(description="Noise floor in dBm")


class NoiseFloorHistoryStats(BaseModel):
    sample_interval_seconds: int = Field(description="Expected spacing between samples")
    coverage_seconds: int = Field(description="How much of the last 24 hours is represented")
    latest_noise_floor_dbm: int | None = Field(
        default=None, description="Most recent sampled noise floor in dBm"
    )
    latest_timestamp: int | None = Field(
        default=None, description="Unix timestamp of the most recent sample"
    )
    samples: list[NoiseFloorSample] = Field(default_factory=list)


class PacketsPerHourBucket(BaseModel):
    timestamp: int = Field(description="Unix timestamp at the start of the hour")
    count: int = Field(description="Number of packets received in that hour")


class RegionScopeStats(BaseModel):
    """Regional flood-scope adoption over the last 24 hours.

    Two independent views, deliberately not merged — they have different
    denominators and will not agree:

    - Traffic (``total_messages``/``scoped_messages``) counts flood-routed
      channel-message packets across all channels, including ones we cannot
      decrypt. Broad coverage, but corrupt RF captures contribute false
      positives, hence ``false_positive_floor``.
    - Senders (``total_senders``/``scoped_senders``) counts distinct message
      senders, which requires decryption and so only covers channels we hold
      keys for. Narrower, but noise-free and immune to one chatty node skewing
      the result.
    """

    total_messages: int = Field(
        description="Flood-routed channel-message packets heard in the last 24h (unique payloads)"
    )
    scoped_messages: int = Field(description="Of those, how many carried a regional transport code")
    scoped_pct: float
    false_positive_floor: float = Field(
        description=(
            "Estimated false positives in scoped_messages, measured from transport-routed "
            "packets claiming a payload type the protocol does not define. A scoped_messages "
            "value at or below this is not evidence of regional adoption."
        )
    )
    total_senders: int = Field(
        description="Distinct channel-message senders in the last 24h (decryptable channels only)"
    )
    scoped_senders: int = Field(description="Of those, how many sent at least one scoped message")
    scoped_senders_pct: float


class StatisticsResponse(BaseModel):
    busiest_channels_24h: list[BusyChannel]
    contact_count: int
    repeater_count: int
    channel_count: int
    total_packets: int
    decrypted_packets: int
    undecrypted_packets: int
    total_dms: int
    total_channel_messages: int
    total_outgoing: int
    contacts_heard: ContactActivityCounts
    repeaters_heard: ContactActivityCounts
    known_channels_active: ContactActivityCounts
    path_hash_width_24h: PathHashWidthStats
    region_scope_24h: RegionScopeStats
    packets_per_hour_72h: list[PacketsPerHourBucket]
    noise_floor_24h: NoiseFloorHistoryStats


class TelemetryHistoryEntry(BaseModel):
    timestamp: int
    data: dict