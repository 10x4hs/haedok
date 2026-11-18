"""게시물 사진 — 받은 JPEG를 검사하고, 위치·기기 정보를 걷어 낸 뒤 저장할 바이트로 만든다.

런타임에 Pillow 가 없다(의존성을 늘리지 않는다). 그래서 JPEG 를 '그리지' 않고 마커만 걷는다:

  FFD8(SOI) · [FFxx 길이 내용]… · FFDA(SOS) 머리 · 압축 데이터 · (점진 JPEG면 다시 DHT · SOS …) · FFD9(EOI)

- APP1~APP15(FFE1~FFEF: EXIF · GPS · XMP · ICC · 제조사 조각)와 COM(FFFE)은 버린다.
  브라우저가 캔버스로 다시 만든 JPEG 에는 원래 없지만,
  캔버스를 건너뛴 요청이 와도 위치가 새지 않게 서버가 한 번 더 걷는다.
- APP0(JFIF)과 그림에 필요한 조각(DQT · DHT · SOF · SOS · DRI …)은 그대로 둔다.
- 압축 데이터 안의 FF00(채움) · FFD0~D7(RST)은 데이터로 보고, 그 밖의 FFxx 를 만나면 다음 조각으로 돌아간다 —
  점진(progressive) JPEG 의 스캔 사이에 끼운 APPn 도 걸러진다.
- EOI 뒤에 붙은 꼬리(일부 휴대폰이 붙이는 추가 정보)는 버린다.
- 크기는 SOF 조각에서 읽는다(세로 · 가로).
"""

import hashlib
from dataclasses import dataclass

MAX_BYTES = 400_000  # 한 장(클라이언트는 ~350 KB 를 목표로 줄여 보낸다)
MAX_PHOTOS = 5  # 한 게시물
MAX_SIDE = 4096  # SOF 의 가로·세로 상한(1080 폭으로 줄여 오지만 넉넉히)
DAILY_PHOTOS = 30  # 한 사람이 하루(한국 날짜)에 올릴 수 있는 사진

SOI, EOI, SOS = 0xD8, 0xD9, 0xDA
# 크기를 담은 프레임 시작 조각(SOF0~15 가운데 DHT C4 · JPG C8 · DAC CC 를 뺀 것)
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
STANDALONE = {0x01, *range(0xD0, 0xD8)}  # 길이가 없는 표시(TEM · RST0~7)


class PhotoError(ValueError):
    """사용자에게 보여 줄 수 있는 거절."""


@dataclass
class CleanPhoto:
    data: bytes
    width: int
    height: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:16]


def _dropped(marker: int) -> bool:
    return 0xE1 <= marker <= 0xEF or marker == 0xFE


def _skip_entropy(raw: bytes, i: int) -> int:
    """압축 데이터를 지나 다음 표시(FFxx)의 위치를 돌려준다. 끝까지 없으면 len(raw)."""
    n = len(raw)
    while True:
        j = raw.find(b"\xff", i)
        if j < 0 or j + 1 >= n:
            return n
        nxt = raw[j + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # 채움 바이트 · 재시작 표시 — 데이터의 일부
            i = j + 2
        elif nxt == 0xFF:  # 표시 앞의 채움 FF
            i = j + 1
        else:
            return j


def strip_jpeg(raw: bytes) -> CleanPhoto:
    """JPEG 를 검사하고 APP1~15 · COM · EOI 뒤 꼬리를 걷어 낸 바이트와 크기를 돌려준다.

    JPEG 가 아니거나 조각이 깨져 있으면 PhotoError.
    """
    bad = PhotoError("사진을 읽지 못했어요. 다른 사진으로 해 주세요")
    if len(raw) < 4 or raw[0] != 0xFF or raw[1] != SOI or raw[2] != 0xFF:
        raise bad
    out = bytearray(b"\xff\xd8")
    n, i = len(raw), 2
    size: tuple[int, int] | None = None
    scanned = ended = False
    while i < n:
        if raw[i] != 0xFF:
            raise bad
        while i < n and raw[i] == 0xFF:  # 표시 앞 채움 FF 는 몇 개든 하나로 본다
            i += 1
        if i >= n:
            raise bad
        marker = raw[i]
        i += 1
        if marker == EOI:
            out += b"\xff\xd9"
            ended = True
            break  # 뒤에 붙은 꼬리는 버린다
        if marker in STANDALONE:
            out += bytes((0xFF, marker))
            continue
        if marker == SOI or i + 2 > n:
            raise bad
        length = int.from_bytes(raw[i : i + 2], "big")
        if length < 2 or i + length > n:
            raise bad
        segment = raw[i - 2 : i + length]  # FF xx 길이 내용
        body = raw[i + 2 : i + length]
        i += length
        if marker in SOF:
            if len(body) < 5:
                raise bad
            h, w = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big")
            size = (w, h)
        if not _dropped(marker):
            out += b"\xff" + bytes((marker,)) + segment[2:]
        if marker == SOS:
            scanned = True
            j = _skip_entropy(raw, i)
            out += raw[i:j]
            i = j
    if not (scanned and ended and size):
        raise bad
    w, h = size
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise PhotoError("사진이 너무 커요. 다시 골라 주세요")
    return CleanPhoto(bytes(out), w, h)


def check_upload(raw: bytes) -> CleanPhoto:
    """올린 한 장: JPEG 매직 넘버 · 400 KB · 마커 검사 후 걷어 낸 바이트."""
    if len(raw) > MAX_BYTES:
        raise PhotoError("사진 한 장이 너무 커요(400 KB까지). 다시 골라 주세요")
    if not raw.startswith(b"\xff\xd8\xff"):
        raise PhotoError("JPEG 사진만 올릴 수 있어요")
    return strip_jpeg(raw)
