"""게시물 사진의 JPEG 마커 걷기 — EXIF · GPS · XMP · COM · 꼬리를 버리고, 크기를 SOF 에서 읽는다."""

import base64
import struct

import pytest

from app.services import post_photos as pp

# macOS sips 로 만든 진짜 16×12 JPEG(814 바이트). sips 가 넣은 APP1(EXIF) · APP13(Photoshop)도 들어 있다.
TINY = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQA"
    "AAABAAAAEKADAAQAAAABAAAADAAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmA"
    "CZjs+EJ+/8AAEQgADAAQAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQE"
    "AAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldY"
    "WVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk"
    "5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMR"
    "BAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdo"
    "aWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz"
    "9PX29/j5+v/bAEMABAQEBAQEBgQEBgkGBgYJDAkJCQkMDwwMDAwMDxIPDw8PDw8SEhISEhISEhUVFRUVFRkZGRkZHBwcHBwcHBwc"
    "HP/bAEMBBAUFBwcHDAcHDB0UEBQdHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHf/dAAQA"
    "Af/aAAwDAQACEQMRAD8A8Q0nwV9393+lemaT4K+7+7/SvUtJ0qy4+SvTdJ0qy+X5KnNOJq2pxcC8YYj3Nz//2Q=="
)


def segment(marker: int, body: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def gps_exif() -> bytes:
    """APP1 'Exif' — IFD0 에 GPSInfo(0x8825) 하나, GPS IFD 에 위도 N 37°33'59" · 경도 E 126°58'40"."""

    def rat(*vals):
        return b"".join(struct.pack(">II", v, 1) for v in vals)

    tiff = bytearray(b"MM\x00\x2a\x00\x00\x00\x08")
    # IFD0: 1 entry (GPSInfo LONG → offset 26)
    tiff += struct.pack(">H", 1) + struct.pack(">HHII", 0x8825, 4, 1, 26) + struct.pack(">I", 0)
    gps_ifd_at = len(tiff)
    assert gps_ifd_at == 26
    lat, lon = rat(37, 33, 59), rat(126, 58, 40)
    data_at = gps_ifd_at + 2 + 4 * 12 + 4
    entries = [
        struct.pack(">HHI", 1, 2, 2) + b"N\x00\x00\x00",  # GPSLatitudeRef
        struct.pack(">HHII", 2, 5, 3, data_at),  # GPSLatitude
        struct.pack(">HHI", 3, 2, 2) + b"E\x00\x00\x00",  # GPSLongitudeRef
        struct.pack(">HHII", 4, 5, 3, data_at + len(lat)),  # GPSLongitude
    ]
    tiff += struct.pack(">H", len(entries)) + b"".join(entries) + struct.pack(">I", 0) + lat + lon
    return segment(0xE1, b"Exif\x00\x00" + bytes(tiff))


GPS = gps_exif()
XMP = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>exif:GPSLatitude=37,33.98N</x:xmpmeta>")
COM = segment(0xFE, b"shot at 37.5665N 126.9780E")
TRAILER = b"SEFHGPS-TRAILER-37.5665"


def with_metadata() -> bytes:
    """TINY 의 SOI 바로 뒤에 GPS EXIF · XMP · COM 을 끼우고, EOI 뒤에 휴대폰식 꼬리를 붙인다."""
    return TINY[:2] + GPS + XMP + COM + TINY[2:] + TRAILER


def markers(jpeg: bytes) -> list[int]:
    """SOS 전까지의 조각 표시들."""
    out, i = [], 2
    while i < len(jpeg):
        m = jpeg[i + 1]
        out.append(m)
        if m == 0xDA:
            break
        i += 2 + int.from_bytes(jpeg[i + 2 : i + 4], "big")
    return out


def test_strips_gps_exif_xmp_comment_and_trailer():
    raw = with_metadata()
    assert GPS[4:] in raw and b"\xff\xe1" in raw
    clean = pp.check_upload(raw)
    assert (clean.width, clean.height) == (16, 12)
    assert GPS[4:] not in clean.data
    assert b"GPS" not in clean.data and b"37.5665" not in clean.data and b"Exif" not in clean.data
    assert b"Photoshop" not in clean.data  # sips 가 넣은 APP13 도 간다
    found = markers(clean.data)
    assert not [m for m in found if 0xE1 <= m <= 0xEF or m == 0xFE], [hex(m) for m in found]
    assert found[0] == 0xE0 and 0xC0 in found and 0xDB in found and 0xC4 in found  # JFIF · 그림 조각은 남는다
    assert clean.data.startswith(b"\xff\xd8") and clean.data.endswith(b"\xff\xd9")
    # 압축 데이터는 한 바이트도 바뀌지 않는다
    sos = TINY.index(b"\xff\xda")
    assert clean.data.endswith(TINY[sos:])


def test_already_clean_jpeg_is_idempotent():
    once = pp.strip_jpeg(TINY).data
    assert pp.strip_jpeg(once).data == once


def test_app_segment_between_progressive_scans_is_dropped():
    """점진 JPEG 처럼 첫 스캔 뒤에 조각이 더 오는 경우 — 스캔 사이에 끼운 APP1 도 걸러진다."""
    sos = TINY.index(b"\xff\xda")
    eoi = len(TINY) - 2
    head, scan = TINY[:sos], TINY[sos:eoi]
    raw = (
        head
        + scan
        + GPS
        + segment(0xC4, TINY[TINY.index(b"\xff\xc4") + 4 : TINY.index(b"\xff\xc4") + 33])
        + scan
        + b"\xff\xd9"
    )
    clean = pp.strip_jpeg(raw)
    assert GPS[4:] not in clean.data
    assert clean.data.count(b"\xff\xda") == 2


def test_entropy_data_with_stuffed_ff_and_restart_markers_kept():
    sos = TINY.index(b"\xff\xda")
    sos_len = int.from_bytes(TINY[sos + 2 : sos + 4], "big")
    body_at = sos + 2 + sos_len
    data = b"\x12\xff\x00\x34\xff\xd0\x56\xff\xff\xd1\x78"
    raw = TINY[:body_at] + data + b"\xff\xd9"
    clean = pp.strip_jpeg(raw)
    assert clean.data.endswith(data + b"\xff\xd9")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"GIF89a....",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
        b"\xff\xd8\xff\xe0\x00\x10JFIF",  # 잘린 파일
        TINY[: len(TINY) // 2],  # 반쯤 받은 파일(EOI 없음)
        TINY.replace(b"\xff\xc0", b"\xff\xe2", 1),  # SOF 가 없다
        b"\xff\xd8\xff\xe1\xff\xff" + b"\x00" * 20,  # 길이가 파일보다 길다
    ],
)
def test_rejects_non_jpeg_and_broken(raw):
    with pytest.raises(pp.PhotoError):
        pp.check_upload(raw)


def test_rejects_too_big():
    big = TINY[:2] + segment(0xE1, b"x" * 60000) * 7 + TINY[2:]
    assert len(big) > pp.MAX_BYTES
    with pytest.raises(pp.PhotoError, match="400 KB"):
        pp.check_upload(big)


def test_rejects_absurd_dimensions():
    sof = TINY.index(b"\xff\xc0")
    raw = bytearray(TINY)
    raw[sof + 5 : sof + 9] = struct.pack(">HH", 9000, 9000)
    with pytest.raises(pp.PhotoError):
        pp.check_upload(bytes(raw))


def test_digest_is_content_hash():
    a, b = pp.strip_jpeg(TINY), pp.strip_jpeg(with_metadata())
    assert a.digest == b.digest and len(a.digest) == 16  # 걷어 낸 뒤 같은 그림이면 같은 해시
