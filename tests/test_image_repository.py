
import pytest

import app.repository.image as image_module
from app.repository.image import ImageRepository


async def _create_session(**overrides):
    values = {
        "session_id": "00112233",
        "message_id": None,
        "direction": "incoming",
        "conversation_type": "PRIV",
        "conversation_key": "aa" * 32,
        "peer_public_key": "aa" * 32,
        "format_id": 1,
        "width": 32,
        "height": 24,
        "size_bytes": 200,
        "fragment_count": 2,
        "state": "available",
        "ttl_seconds": 3600,
    }
    values.update(overrides)
    await ImageRepository.create_session(**values)


async def test_image_session_reassembles_and_suppresses_identical_duplicates(test_db):
    original = image_module.db
    image_module.db = test_db
    try:
        await _create_session()
        assert await ImageRepository.add_fragment("00112233", 0, b"a" * 152) is True
        assert await ImageRepository.add_fragment("00112233", 0, b"a" * 152) is False
        with pytest.raises(ValueError, match="conflicting duplicate"):
            await ImageRepository.add_fragment("00112233", 0, b"b" * 152)
        assert await ImageRepository.add_fragment("00112233", 1, b"c" * 48) is True
        session = await ImageRepository.get("00112233")
        assert session is not None
        assert session["state"] == "complete"
        assert session["fragments"] == [(0, b"a" * 152), (1, b"c" * 48)]
    finally:
        image_module.db = original


async def test_image_session_rejects_bad_fragment_and_conflicting_metadata(test_db):
    original = image_module.db
    image_module.db = test_db
    try:
        await _create_session()
        with pytest.raises(ValueError, match="fragment size"):
            await ImageRepository.add_fragment("00112233", 0, b"short")
        with pytest.raises(ValueError, match="conflicts"):
            await _create_session(width=31)
    finally:
        image_module.db = original


async def test_image_session_expiry_cleanup(test_db):
    original = image_module.db
    image_module.db = test_db
    try:
        await _create_session(ttl_seconds=-1)
        assert await ImageRepository.enforce_cache_limit() == 0
        assert await ImageRepository.get("00112233") is None
    finally:
        image_module.db = original
