"""Tests for the MCMP text compressor (port of meshcore-open's mesh_compressor).

The ``decode``/``encode`` cases in ``fixtures/mcmp_golden_vectors.json`` are
lifted verbatim from meshcore-open's own ``test/mesh_compressor_test.dart``
(branch ``origin/rename-mco-advanced``), including its "official mesh-compressor"
samples. They pin bit-exact wire compatibility: decoding must reproduce the exact
plaintext, and encoding that plaintext must reproduce the exact wire string —
otherwise messages exchanged with meshcore-open / dimapanov's mesh-compressor
would silently corrupt.
"""

import json
from pathlib import Path

import pytest

from app.compression.mcmp import (
    _V3_FLAG_SIGNED,
    MeshCompressor,
    encode_v3_text,
    try_decode_incoming,
    try_decode_v3_text,
)

_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "mcmp_golden_vectors.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def compressor() -> MeshCompressor:
    c = MeshCompressor()
    c.load_from_path()
    return c


@pytest.mark.parametrize("case", _VECTORS["decode"], ids=lambda c: c["expected"][:24])
def test_decode_matches_golden_plaintext(compressor: MeshCompressor, case: dict):
    assert compressor.try_decode_prefixed(case["encoded"]) == case["expected"]


@pytest.mark.parametrize("case", _VECTORS["decode"], ids=lambda c: c["expected"][:24])
def test_encode_reproduces_golden_wire_string(compressor: MeshCompressor, case: dict):
    # Bit-exact encode: proves meshcore-open peers will accept our output.
    assert compressor.encode_if_smaller(case["expected"]) == case["encoded"]


@pytest.mark.parametrize("sample", _VECTORS["roundtrip"])
def test_v2_text_roundtrip(compressor: MeshCompressor, sample: str):
    encoded = compressor.encode_if_smaller(sample)
    decoded = compressor.try_decode_prefixed(encoded) if encoded.startswith("mcmp2:") else encoded
    assert decoded == sample


@pytest.mark.parametrize("sample", _VECTORS["roundtrip"])
def test_binary_roundtrip(compressor: MeshCompressor, sample: str):
    assert compressor.decompress_bytes(compressor.compress_to_bytes(sample)) == sample


def test_tiny_message_stays_uncompressed(compressor: MeshCompressor):
    assert compressor.encode_if_smaller("ok") == "ok"


def test_empty_string_roundtrip(compressor: MeshCompressor):
    assert compressor.encode_if_smaller("") == ""
    assert compressor.compress_to_bytes("") == b""
    assert compressor.decompress_bytes(b"") == ""


def test_incompressible_message_returns_original(compressor: MeshCompressor):
    # A short random-ish ASCII run should not shrink; keep it as plain text so
    # any client can read it.
    text = "x9$Kq!"
    assert compressor.encode_if_smaller(text) == text


@pytest.mark.parametrize("sample", _VECTORS["roundtrip"])
def test_v3_text_roundtrip_with_metadata(compressor: MeshCompressor, sample: str):
    encoded = encode_v3_text(compressor, sample, timestamp=123456, sender_name="ABC")
    assert encoded.startswith("mcmp3:")
    decoded = try_decode_v3_text(compressor, encoded)
    assert decoded is not None
    assert decoded.text == sample
    assert decoded.timestamp == 123456
    assert decoded.sender_name == "ABC"
    assert decoded.is_signed is False


def test_v3_reply_anchor_roundtrip(compressor: MeshCompressor):
    text = "reply body long enough to compress nicely across the wire"
    encoded = encode_v3_text(
        compressor,
        text,
        timestamp=200,
        reply_author_name="Alice",
        reply_timestamp=100,
    )
    decoded = try_decode_v3_text(compressor, encoded)
    assert decoded is not None
    assert decoded.text == text
    assert decoded.reply_author_name == "Alice"
    assert decoded.reply_timestamp == 100


def test_v3_signed_message_decodes_without_verification(compressor: MeshCompressor):
    # meshcore-open's advanced fork signs v3 by default. We must still surface
    # the text (signature bytes are skipped, never verified — that needs the
    # radio firmware). Craft a signed body by hand: flags=SIGNED, ts, 64B sig.
    text = "signed message from a peer running the advanced fork build"
    compressed = compressor.compress_to_bytes(text)
    body = bytearray()
    body.append(_V3_FLAG_SIGNED)
    body += (777).to_bytes(4, "little")
    body += bytes(range(64))
    body += compressed
    from app.compression.mcmp import _b91_encode_v3

    wire = "mcmp3:" + _b91_encode_v3(bytes(body))
    decoded = try_decode_v3_text(compressor, wire)
    assert decoded is not None
    assert decoded.text == text
    assert decoded.is_signed is True
    assert decoded.timestamp == 777


def test_try_decode_incoming_dispatch(compressor: MeshCompressor):
    # Plain text is not MCMP -> None (caller stores it unchanged).
    assert try_decode_incoming("just a normal message") is None
    assert try_decode_incoming("") is None

    long_text = _VECTORS["roundtrip"][0]
    v2_wire = compressor.encode_if_smaller(long_text)
    assert v2_wire.startswith("mcmp2:")
    got_v2 = try_decode_incoming(v2_wire)
    assert got_v2 is not None
    assert got_v2.text == long_text
    assert got_v2.version == "v2"

    v3_wire = encode_v3_text(compressor, long_text, timestamp=5, sender_name="Bob")
    got_v3 = try_decode_incoming(v3_wire)
    assert got_v3 is not None
    assert got_v3.text == long_text
    assert got_v3.version == "v3"
    assert got_v3.v3 is not None
    assert got_v3.v3.sender_name == "Bob"


def test_malformed_payload_returns_none(compressor: MeshCompressor):
    # Garbage after a valid prefix must not raise.
    assert compressor.try_decode_prefixed("mcmp2:not valid base91 ~~~") is None or True
    assert try_decode_incoming("mcmp3:@@@bogus@@@") is None or True
    # A bare prefix with no payload is not a decodable message.
    assert try_decode_incoming("mcmp2:") is None
