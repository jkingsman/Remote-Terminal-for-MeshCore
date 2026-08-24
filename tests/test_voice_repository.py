import app.repository.voice as voice_module
from app.repository.voice import VoiceRepository


async def test_voice_session_persists_fragments_and_rejects_duplicates(test_db):
    original = voice_module.db
    voice_module.db = test_db
    try:
        await VoiceRepository.upsert_session(
            session_id="00112233",
            message_id=None,
            direction="incoming",
            conversation_type="PRIV",
            conversation_key="aa" * 32,
            peer_public_key="aa" * 32,
            mode=3,
            duration_ms=1000,
            packet_count=2,
            state="available",
        )
        assert await VoiceRepository.add_fragment("00112233", 0, b"first") is True
        assert await VoiceRepository.add_fragment("00112233", 0, b"duplicate") is False
        assert await VoiceRepository.add_fragment("00112233", 1, b"second") is True
        session = await VoiceRepository.get("00112233")
        assert session is not None
        assert session["state"] == "complete"
        assert session["fragments"] == [(0, b"first"), (1, b"second")]
    finally:
        voice_module.db = original
