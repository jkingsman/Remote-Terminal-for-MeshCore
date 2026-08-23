"""MCMP text compression for MeshCore messages.

See :mod:`app.compression.mcmp` for the codec. The common entry points:

    from app.compression import get_compressor, try_decode_incoming

``try_decode_incoming(text)`` decodes an inbound ``mcmp2:``/``mcmp3:`` body to
plain text (or returns ``None`` for non-MCMP text). ``get_compressor()`` returns
the process-wide compressor for encoding outbound text.
"""

from .mcmp import (
    DecodedIncoming,
    DecodedV3Message,
    MeshCompressor,
    MeshCompressorError,
    encode_outbound,
    encode_v3_text,
    get_compressor,
    is_v3_text_payload,
    outbound_wire_bytes,
    try_decode_incoming,
    try_decode_v3_text,
)

__all__ = [
    "DecodedIncoming",
    "DecodedV3Message",
    "MeshCompressor",
    "MeshCompressorError",
    "encode_outbound",
    "encode_v3_text",
    "get_compressor",
    "is_v3_text_payload",
    "outbound_wire_bytes",
    "try_decode_incoming",
    "try_decode_v3_text",
]
