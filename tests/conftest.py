"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture
def sample_channel_key():
    """A sample 16-byte channel key for testing."""
    return bytes.fromhex("0123456789abcdef0123456789abcdef")


@pytest.fixture
def sample_hashtag_key():
    """A channel key derived from hashtag name '#test'."""
    import hashlib
    return hashlib.sha256(b"#test").digest()[:16]
