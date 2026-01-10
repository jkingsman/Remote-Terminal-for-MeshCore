from pydantic import BaseModel, Field


class Contact(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0  # 0=unknown, 1=client, 2=repeater, 3=room
    flags: int = 0
    last_path: str | None = None
    last_path_len: int = -1
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool = False
    last_contacted: int | None = None  # Last time we sent/received a message

    def to_radio_dict(self) -> dict:
        """Convert to the dict format expected by meshcore radio commands.

        The radio API uses different field names (adv_name, out_path, etc.)
        than our database schema (name, last_path, etc.).
        """
        return {
            "public_key": self.public_key,
            "adv_name": self.name or "",
            "type": self.type,
            "flags": self.flags,
            "out_path": self.last_path or "",
            "out_path_len": self.last_path_len,
            "adv_lat": self.lat or 0.0,
            "adv_lon": self.lon or 0.0,
            "last_advert": self.last_advert or 0,
        }

    @staticmethod
    def from_radio_dict(public_key: str, radio_data: dict, on_radio: bool = False) -> dict:
        """Convert radio contact data to database format dict.

        This is the inverse of to_radio_dict(), used when syncing contacts
        from radio to database.
        """
        return {
            "public_key": public_key,
            "name": radio_data.get("adv_name"),
            "type": radio_data.get("type", 0),
            "flags": radio_data.get("flags", 0),
            "last_path": radio_data.get("out_path"),
            "last_path_len": radio_data.get("out_path_len", -1),
            "lat": radio_data.get("adv_lat"),
            "lon": radio_data.get("adv_lon"),
            "last_advert": radio_data.get("last_advert"),
            "on_radio": on_radio,
        }


# Contact type constants
CONTACT_TYPE_REPEATER = 2


class Channel(BaseModel):
    key: str = Field(description="Channel key (32-char hex)")
    name: str
    is_hashtag: bool = False
    on_radio: bool = False


class Message(BaseModel):
    id: int
    type: str = Field(description="PRIV or CHAN")
    conversation_key: str = Field(description="User pubkey for PRIV, channel key for CHAN")
    text: str
    sender_timestamp: int | None = None
    received_at: int
    path_len: int | None = None
    txt_type: int = 0
    signature: str | None = None
    outgoing: bool = False
    acked: bool = False


class RawPacket(BaseModel):
    """Raw packet as stored in the database."""
    id: int
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    decrypted: bool = False
    message_id: int | None = None
    decrypt_attempts: int = 0
    last_attempt: int | None = None


class RawPacketDecryptedInfo(BaseModel):
    """Decryption info for a raw packet (when successfully decrypted)."""
    channel_name: str | None = None
    sender: str | None = None


class RawPacketBroadcast(BaseModel):
    """Raw packet payload broadcast via WebSocket.

    This extends the database model with runtime-computed fields
    like payload_type, snr, rssi, and decryption info.
    """
    id: int
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g., GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB")
    rssi: int | None = Field(default=None, description="Received signal strength in dBm")
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class SendDirectMessageRequest(SendMessageRequest):
    destination: str = Field(description="Public key or prefix of recipient")


class SendChannelMessageRequest(SendMessageRequest):
    channel_key: str = Field(description="Channel key (32-char hex)")


class TelemetryRequest(BaseModel):
    password: str = Field(default="", description="Repeater password (empty string for no password)")


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
    permission: int = Field(description="Permission level: 0=Guest, 1=Read-only, 2=Read-write, 3=Admin")
    permission_name: str = Field(description="Human-readable permission name")


class TelemetryResponse(BaseModel):
    """Telemetry data from a repeater, formatted for human readability."""
    pubkey_prefix: str = Field(description="12-char public key prefix")
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
    neighbors: list[NeighborInfo] = Field(default_factory=list, description="List of neighbors seen by repeater")
    acl: list[AclEntry] = Field(default_factory=list, description="Access control list")


class CommandRequest(BaseModel):
    """Request to send a CLI command to a repeater."""
    command: str = Field(min_length=1, description="CLI command to send")


class CommandResponse(BaseModel):
    """Response from a repeater CLI command."""
    command: str = Field(description="The command that was sent")
    response: str = Field(description="Response from the repeater")
    sender_timestamp: int | None = Field(default=None, description="Timestamp from the repeater's response")
