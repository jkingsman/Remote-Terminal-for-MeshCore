from dataclasses import dataclass
from typing import Optional

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.mcmp.codec import McmpAppCodec
from app.repository import ContactRepository


@dataclass
class McmpVerificationResult:
    """Result of verifying an inbound MCMP v3 channel signature."""

    status: str  # "valid", "invalid", "unverifiable", "unsigned"
    verified_sender_key_hex: Optional[str] = None
    name_collision: bool = False


def _normalize_name(name: str) -> str:
    return name.strip()


async def verify_channel_message(
    *,
    decoded_msg,
    sender_name: Optional[str],
    channel_key_bytes: bytes,
) -> McmpVerificationResult:
    """Verify an MCMP v3 channel message signature.

    The claimed identity is `sender_name` from the outer GROUP_TEXT envelope.
    If the MCMP container also embeds a sender name, it must match that
    displayed name. Candidate keys are the contacts bearing the sender name.
    Verification succeeds only if a candidate's public key verifies the
    signature against the canonical signing bytes.
    """
    signature = decoded_msg.signature
    if signature is None:
        return McmpVerificationResult(status="unsigned")

    if not sender_name:
        return McmpVerificationResult(status="unverifiable")

    # If the MCMP body embeds a sender name, it must match the displayed name.
    if decoded_msg.sender_name is not None:
        if _normalize_name(decoded_msg.sender_name) != _normalize_name(sender_name):
            return McmpVerificationResult(status="invalid")

    candidates = await ContactRepository.get_by_name(sender_name)
    if not candidates:
        return McmpVerificationResult(status="unverifiable")

    collision = len(candidates) > 1

    flags = McmpAppCodec.pack_flags(
        has_reply=decoded_msg.is_reply,
        is_signed=True,
        has_sender_name=decoded_msg.sender_name is not None,
    )
    binding = McmpAppCodec.channel_signing_binding(channel_key_bytes)

    canonical = McmpAppCodec.canonical_signing_bytes(
        context_id=0x01,  # channel context
        binding=binding,
        sender_name=sender_name,
        timestamp=decoded_msg.timestamp,
        flags=flags,
        text=decoded_msg.text,
        reply_author_name=decoded_msg.reply_author_name,
        reply_timestamp=decoded_msg.reply_timestamp,
    )

    for contact in candidates:
        if len(contact.public_key) != 64:
            continue
        try:
            public_key_bytes = bytes.fromhex(contact.public_key)
            VerifyKey(public_key_bytes).verify(canonical, signature)
            return McmpVerificationResult(
                status="valid",
                verified_sender_key_hex=contact.public_key,
                name_collision=collision,
            )
        except (BadSignatureError, ValueError):
            continue

    return McmpVerificationResult(status="invalid", name_collision=collision)