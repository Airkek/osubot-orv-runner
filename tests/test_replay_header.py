import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from replay_header import ReplayHashes, read_replay_hashes


def osr_string(value):
    data = value.encode("utf-8")
    length = len(data)
    encoded_length = bytearray()
    while length >= 128:
        encoded_length.append((length & 0x7F) | 0x80)
        length >>= 7
    encoded_length.append(length)
    return b"\x0b" + bytes(encoded_length) + data


PREFIX = b"\x00" + (20241001).to_bytes(4, "little")
BEATMAP_HASH = "a" * 32
REPLAY_HASH = "b" * 32


class ReplayHeaderTests(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.osr"
            path.write_bytes(data)
            return read_replay_hashes(path)

    def test_reads_hashes_without_frames_or_replay_id(self):
        header = PREFIX + b"".join(map(osr_string, (BEATMAP_HASH, "player", REPLAY_HASH)))
        self.assertEqual(self.read(header), ReplayHashes(BEATMAP_HASH, REPLAY_HASH))

    def test_ignores_replay_body(self):
        header = PREFIX + b"".join(map(osr_string, (BEATMAP_HASH, "player", REPLAY_HASH)))
        self.assertEqual(self.read(header + b"invalid compressed data"), ReplayHashes(BEATMAP_HASH, REPLAY_HASH))

    def test_skips_utf8_username_with_multibyte_length(self):
        header = PREFIX + b"".join(map(osr_string, (BEATMAP_HASH, "Игрок" * 30, REPLAY_HASH)))
        self.assertEqual(self.read(header), ReplayHashes(BEATMAP_HASH, REPLAY_HASH))

    def test_accepts_both_empty_string_encodings(self):
        for empty in (b"\x00", b"\x0b\x00"):
            with self.subTest(empty=empty):
                self.assertEqual(self.read(PREFIX + empty * 3), ReplayHashes("", ""))

    def test_rejects_header_truncated_at_any_position(self):
        header = PREFIX + b"".join(map(osr_string, (BEATMAP_HASH, "player", REPLAY_HASH)))
        for end in range(len(header)):
            with self.subTest(end=end), self.assertRaisesRegex(ValueError, "Truncated .osr header"):
                self.read(header[:end])

    def test_rejects_invalid_string_marker(self):
        with self.assertRaisesRegex(ValueError, "string marker for beatmap hash"):
            self.read(PREFIX + b"\x01")

    def test_rejects_overflowing_or_unterminated_length(self):
        for length in (b"\xff\xff\xff\xff\x10", b"\x80" * 5):
            with self.subTest(length=length), self.assertRaisesRegex(ValueError, "string length"):
                self.read(PREFIX + b"\x0b" + length)

    def test_rejects_invalid_utf8(self):
        with self.assertRaisesRegex(ValueError, "Invalid UTF-8"):
            self.read(PREFIX + b"\x0b\x01\xff")


if __name__ == "__main__":
    unittest.main()
