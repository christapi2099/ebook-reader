"""Minimal Ogg container code for single-stream Ogg Opus files.

Enough to split ffmpeg's output into packets, write packets back as one valid
stream laid out like ffmpeg's own muxer (two header pages, then pages of up to
50 x 20 ms packets with granule = packets * 960), and check a file's pages
(capture pattern, CRC, sequence, serial, granule order, EOS) without decoding it.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

G48_PER_PACKET = 960       # 20 ms packets in 48 kHz granule units
PAGE_PACKETS = 50          # ffmpeg's ogg muxer default (1 s pages)
_HEADER = struct.Struct("<4sBBqIIIB")  # capture, version, flags, granule, serial, seq, crc, nseg
FLAG_CONTINUED, FLAG_BOS, FLAG_EOS = 0x01, 0x02, 0x04

_REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


class OggError(ValueError):
    pass


def ogg_crc(page: bytes) -> int:
    """Ogg CRC-32 (poly 0x04C11DB7, MSB-first, init 0, no final xor).

    zlib's CRC-32 is the bit-reflected form of the same polynomial, so it runs on
    bit-reversed bytes and the result is reversed back."""
    raw = zlib.crc32(page.translate(_REV), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return int(f"{raw:032b}"[::-1], 2)


def _pages(read):
    """Yield (flags, granule, serial, seq, lacing, body) per page; checks pattern and CRC."""
    while True:
        hdr = read(27)
        if not hdr:
            return
        if len(hdr) < 27:
            raise OggError("truncated page header")
        capture, version, flags, granule, serial, seq, crc, nseg = _HEADER.unpack(hdr)
        if capture != b"OggS" or version != 0:
            raise OggError("bad capture pattern")
        lacing = read(nseg)
        if len(lacing) < nseg:
            raise OggError("truncated segment table")
        body = read(sum(lacing))
        if len(body) < sum(lacing):
            raise OggError("truncated page body")
        page = bytearray(hdr + lacing + body)
        page[22:26] = b"\0\0\0\0"
        if ogg_crc(bytes(page)) != crc:
            raise OggError(f"CRC mismatch on page {seq}")
        yield flags, granule, serial, seq, lacing, body


def read_packets(buf: bytes) -> tuple[int, list[bytes], int]:
    """(serial, packets, last page granule) of a single-stream Ogg buffer."""
    pos = 0

    def read(n):
        nonlocal pos
        out = buf[pos:pos + n]
        pos += len(out)
        return out

    packets: list[bytes] = []
    cur: list[bytes] = []
    serial = granule = None
    for _flags, g, s, _seq, lacing, body in _pages(read):
        serial = s if serial is None else serial
        if s != serial:
            raise OggError("more than one logical stream")
        granule = g
        p = 0
        for n in lacing:
            cur.append(body[p:p + n])
            p += n
            if n < 255:
                packets.append(b"".join(cur))
                cur = []
    if serial is None:
        raise OggError("empty Ogg stream")
    if cur:
        raise OggError("stream ends inside a packet")
    return serial, packets, granule


def pre_skip(opus_head: bytes) -> int:
    """OpusHead pre-skip in 48 kHz samples."""
    if not opus_head.startswith(b"OpusHead") or len(opus_head) < 19:
        raise OggError("not an OpusHead packet")
    return struct.unpack_from("<H", opus_head, 10)[0]


class OggOpusWriter:
    """Writes one Ogg Opus stream: OpusHead page, OpusTags page, audio pages.

    Audio pages hold up to PAGE_PACKETS packets (fewer if a page would need more
    than 255 lacing values); a non-final page's granule is packets-so-far * 960.
    The last page carries EOS and the encoder's final granule (pre-skip + length).
    """

    def __init__(self, f, serial: int, head: bytes, tags: bytes):
        self._f, self._serial, self._seq = f, serial, 0
        self._pending: list[bytes] = []
        self._count = 0  # audio packets already written
        self._page([head], 0, FLAG_BOS)
        self._page([tags], 0, 0)

    def _page(self, packets: list[bytes], granule: int, flags: int) -> None:
        lacing = bytearray()
        for p in packets:
            lacing += b"\xff" * (len(p) // 255) + bytes([len(p) % 255])
        if len(lacing) > 255:
            raise OggError("page needs more than 255 lacing values")
        page = bytearray(_HEADER.pack(b"OggS", 0, flags, granule, self._serial, self._seq, 0,
                                      len(lacing)) + bytes(lacing) + b"".join(packets))
        struct.pack_into("<I", page, 22, ogg_crc(bytes(page)))
        self._f.write(page)
        self._seq += 1

    def _take_page(self) -> list[bytes]:
        out, lace = [], 0
        for p in self._pending[:PAGE_PACKETS]:
            n = len(p) // 255 + 1
            if out and lace + n > 255:
                break
            out.append(p)
            lace += n
        del self._pending[:len(out)]
        return out

    def write(self, packets: list[bytes]) -> None:
        self._pending.extend(packets)
        # Keep at least one packet back so the final page (EOS) is never empty.
        while len(self._pending) > PAGE_PACKETS:
            page = self._take_page()
            self._count += len(page)
            self._page(page, self._count * G48_PER_PACKET, 0)

    def close(self, final_granule: int) -> int:
        """Flush with EOS on the last page; returns the number of audio packets."""
        if not self._pending:
            raise OggError("no audio packets")
        while self._pending:
            page = self._take_page()
            self._count += len(page)
            last = not self._pending
            self._page(page, final_granule if last else self._count * G48_PER_PACKET,
                       FLAG_EOS if last else 0)
        return self._count


def check_stream(path) -> dict:
    """Walk every page of an Ogg Opus file without decoding it.

    Raises OggError on a bad capture pattern, CRC, truncation, a sequence gap, a
    second stream, granules going backwards, a missing BOS/EOS, or a missing
    OpusHead. Returns {pages, packets, granule, pre_skip}. Reads page by page, so
    memory stays flat.
    """
    pages = packets = 0
    serial = last_seq = None
    granule = -1
    head = None
    eos = False
    with open(Path(path), "rb") as f:
        for flags, g, s, seq, lacing, body in _pages(f.read):
            if pages == 0:
                if not flags & FLAG_BOS:
                    raise OggError("first page lacks BOS")
                serial = s
                head = body
            elif s != serial:
                raise OggError("more than one logical stream")
            if eos:
                raise OggError("data after the EOS page")
            if last_seq is not None and seq != last_seq + 1:
                raise OggError(f"page sequence jumps from {last_seq} to {seq}")
            if g != -1 and g < granule:
                raise OggError(f"granule goes backwards at page {seq}")
            granule = max(granule, g)
            last_seq = seq
            pages += 1
            packets += sum(1 for n in lacing if n < 255)
            eos = bool(flags & FLAG_EOS)
    if pages == 0:
        raise OggError("empty file")
    if not eos:
        raise OggError("no EOS page (file truncated?)")
    return {"pages": pages, "packets": packets, "granule": granule, "pre_skip": pre_skip(head)}
