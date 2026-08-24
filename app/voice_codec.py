"""Small ctypes boundary around the system Codec2 library."""

from __future__ import annotations

import ctypes
import ctypes.util
from array import array

from app.voice_protocol import VoiceMode

_CODEC2_MODES = {
    VoiceMode.MODE_3200: 0,
    VoiceMode.MODE_2400: 1,
    VoiceMode.MODE_1600: 2,
    VoiceMode.MODE_1400: 3,
    VoiceMode.MODE_1300: 4,
    VoiceMode.MODE_1200: 5,
    VoiceMode.MODE_700C: 8,
}


class Codec2Unavailable(RuntimeError):
    pass


class Codec2:
    def __init__(self, mode: VoiceMode) -> None:
        library = ctypes.util.find_library("codec2")
        if not library:
            raise Codec2Unavailable("Codec2 is not installed on this server")
        self._lib = ctypes.CDLL(library)
        self._lib.codec2_create.argtypes = [ctypes.c_int]
        self._lib.codec2_create.restype = ctypes.c_void_p
        self._lib.codec2_destroy.argtypes = [ctypes.c_void_p]
        self._lib.codec2_samples_per_frame.argtypes = [ctypes.c_void_p]
        self._lib.codec2_samples_per_frame.restype = ctypes.c_int
        self._lib.codec2_bits_per_frame.argtypes = [ctypes.c_void_p]
        self._lib.codec2_bits_per_frame.restype = ctypes.c_int
        self._lib.codec2_encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self._lib.codec2_decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self._handle = self._lib.codec2_create(_CODEC2_MODES[mode])
        if not self._handle:
            raise Codec2Unavailable(f"Codec2 mode {mode.name} is unavailable")
        self.samples_per_frame = self._lib.codec2_samples_per_frame(self._handle)
        self.bytes_per_frame = (self._lib.codec2_bits_per_frame(self._handle) + 7) // 8

    def close(self) -> None:
        if self._handle:
            self._lib.codec2_destroy(self._handle)
            self._handle = None

    def __enter__(self) -> Codec2:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def encode_pcm16le(self, pcm: bytes) -> bytes:
        frame_bytes = self.samples_per_frame * 2
        if len(pcm) % 2:
            raise ValueError("PCM16 input must contain complete samples")
        padding = (-len(pcm)) % frame_bytes
        pcm += bytes(padding)
        encoded = bytearray()
        for offset in range(0, len(pcm), frame_bytes):
            speech = (ctypes.c_int16 * self.samples_per_frame).from_buffer_copy(
                pcm[offset : offset + frame_bytes]
            )
            bits = (ctypes.c_ubyte * self.bytes_per_frame)()
            self._lib.codec2_encode(self._handle, bits, speech)
            encoded.extend(bits)
        return bytes(encoded)

    def decode_pcm16le(self, encoded: bytes) -> bytes:
        if not encoded or len(encoded) % self.bytes_per_frame:
            raise ValueError("Codec2 data does not contain complete frames")
        samples = array("h")
        for offset in range(0, len(encoded), self.bytes_per_frame):
            bits = (ctypes.c_ubyte * self.bytes_per_frame).from_buffer_copy(
                encoded[offset : offset + self.bytes_per_frame]
            )
            speech = (ctypes.c_int16 * self.samples_per_frame)()
            self._lib.codec2_decode(self._handle, speech, bits)
            samples.extend(speech)
        if samples.itemsize != 2:
            raise RuntimeError("unsupported platform PCM sample width")
        return samples.tobytes()


def codec2_available() -> bool:
    try:
        with Codec2(VoiceMode.MODE_1300):
            return True
    except (Codec2Unavailable, OSError):
        return False
