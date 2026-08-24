from types import SimpleNamespace

import pytest

from app.image_protocol import (
    MAX_ENCODED_IMAGE_BYTES,
    ImageEnvelope,
    ImageFetchRequest,
    ImageFormat,
    ImagePacket,
    fragment_image,
    reassemble_image,
)
from app.routers.images import _image_envelope_body


def test_meshcore_sar_ie4_compatibility_vector():
    parsed = ImageEnvelope.parse("IE4:a:0:e:74:4r:1mc")
    assert parsed == ImageEnvelope("0000000a", ImageFormat.AVIF, 14, 256, 171, 2100)
    assert parsed.encode() == "IE4:a:0:e:74:4r:1mc"


@pytest.mark.parametrize(
    "invalid",
    [
        "IE3:a:0:e:74:4r:1mc",
        "IE4:!:0:e:74:4r:1mc",
        "IE4:a:2:e:74:4r:1mc",
        "IE4:a:0:0:74:4r:1mc",
        "IE4:a:0:e:75:4r:1mc",
        "IE4:a:0:e:74:4r:1",
    ],
)
def test_ie4_rejects_malformed_or_impossible_metadata(invalid):
    assert ImageEnvelope.parse(invalid) is None


def test_image_packet_binary_compatibility_vector():
    packet = ImagePacket("00112233", 7, bytes.fromhex("aabbcc"))
    wire = bytes.fromhex("490011223307aabbcc")
    assert packet.encode() == wire
    assert ImagePacket.parse(wire) == packet
    assert ImagePacket.parse(wire[:6]) is None
    assert ImagePacket.parse(b"X" + wire[1:]) is None
    assert ImagePacket.parse(wire[:6] + bytes(153)) is None


def test_fetch_request_binary_compatibility_vector():
    request = ImageFetchRequest("00112233", "aabbccddeeff", (1, 3, 4))
    wire = bytes.fromhex("690011223301aabbccddeeff03010304")
    assert request.encode() == wire
    assert ImageFetchRequest.parse(wire) == request
    assert ImageFetchRequest.parse(wire[:-1]) is None
    assert ImageFetchRequest.parse(wire[:-3] + bytes([2, 1, 1])) is None


def test_fragmentation_reassembly_and_missing_fragment_handling():
    encoded = bytes(index % 251 for index in range(400))
    packets = fragment_image("00112233", encoded)
    assert [len(packet.data) for packet in packets] == [152, 152, 96]
    assert reassemble_image(packets, 3) == encoded
    assert reassemble_image([packets[0], packets[2]], 3) is None
    assert reassemble_image([packets[0], packets[0], packets[2]], 3) is None


def test_fragmentation_rejects_empty_and_oversized_images():
    with pytest.raises(ValueError):
        fragment_image("00112233", b"")
    with pytest.raises(ValueError):
        fragment_image("00112233", bytes(MAX_ENCODED_IMAGE_BYTES + 1))


def test_private_image_body_is_passed_to_parser_unchanged():
    message = SimpleNamespace(type="PRIV", text="IE4:a:0:e:74:4r:1mc", sender_name=None)

    body = _image_envelope_body(message)

    assert body == "IE4:a:0:e:74:4r:1mc"
    assert ImageEnvelope.parse(body) is not None


def test_channel_image_body_excludes_exact_sender_presentation_prefix():
    message = SimpleNamespace(
        type="CHAN", text="Alice: IE4:a:0:e:74:4r:1mc", sender_name="Alice"
    )

    body = _image_envelope_body(message)

    assert body == "IE4:a:0:e:74:4r:1mc"
    assert ImageEnvelope.parse(body) is not None
    # Parsing the raw stored text (with the presentation prefix) must not work.
    assert ImageEnvelope.parse(message.text) is None


@pytest.mark.parametrize(
    ("text", "sender_name"),
    [
        ("Alice: ordinary channel text", "Alice"),
        ("Alice: IE4:!:0:e:74:4r:1mc", "Alice"),
        ("Mallory: IE4:a:0:e:74:4r:1mc", "Alice"),
    ],
)
def test_channel_body_extraction_does_not_turn_non_image_or_mismatched_text_into_image(
    text, sender_name
):
    message = SimpleNamespace(type="CHAN", text=text, sender_name=sender_name)

    assert ImageEnvelope.parse(_image_envelope_body(message)) is None
