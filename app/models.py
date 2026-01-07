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
