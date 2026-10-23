from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReplayHashes:
    beatmap_hash: str
    replay_hash: str


def read_replay_hashes(path: str | Path) -> ReplayHashes:
    data = Path(path).read_bytes()
    offset = 5  # Game mode (1 byte) and game version (4 bytes)
    if len(data) < offset:
        raise ValueError("Truncated .osr header: missing game mode or version")

    def read_string(field: str) -> str:
        nonlocal offset

        def read_byte() -> int:
            nonlocal offset
            if offset >= len(data):
                raise ValueError(f"Truncated .osr header: missing {field}")
            value = data[offset]
            offset += 1
            return value

        marker = read_byte()
        if marker == 0:
            return ""
        if marker != 0x0B:
            raise ValueError(f"Invalid .osr string marker for {field}: {marker:#x}")

        length = 0
        for shift in range(0, 35, 7):
            byte = read_byte()
            if shift == 28 and byte > 0x0F:
                raise ValueError(f"Invalid .osr string length for {field}")
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break

        end = offset + length
        if end > len(data):
            raise ValueError(f"Truncated .osr header: incomplete {field}")
        try:
            value = data[offset:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"Invalid UTF-8 in .osr {field}") from error
        offset = end
        return value

    beatmap_hash = read_string("beatmap hash")
    read_string("username")
    replay_hash = read_string("replay hash")
    return ReplayHashes(beatmap_hash, replay_hash)
