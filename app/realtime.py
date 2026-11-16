"""실시간 알림 — 책 단위로 접속한 WebSocket에 이벤트를 보낸다.

지금은 한 프로세스 안의 메모리 브로커다. 구독자가 이 인스턴스 메모리에만 있으므로, 인스턴스가 여럿인
배포(Vercel 함수 등)에서는 다른 인스턴스에 붙은 친구의 소식이 오지 않는다. Vercel Python 함수도 이제
WebSocket을 열 수 있지만 그래서 기본값은 REALTIME_WS=false(VERCEL_ENV가 있으면)이고, 그때 클라이언트
(presence.js 의 haedokLive)는 소켓 없이 짧은 간격으로 폴링한다 — 폴링 응답도 같은 스포일러 가드를 지난다.
소켓을 켠 배포에서도 haedokLive 는 느린 안전망 폴링을 계속 돌린다.
서버를 여러 대로 늘리면 같은 인터페이스로 Redis Pub/Sub 구현을 바꿔 끼운다 (docs/architecture.md).
이벤트에는 반응 본문을 싣지 않는다 — 받는 쪽이 스포일러 가드가 걸린 API로 다시 읽는다.
"""

import asyncio
import contextlib
from collections import defaultdict

from fastapi import WebSocket


class Broker:
    def __init__(self) -> None:
        self._subs: dict[int, dict[int, set[WebSocket]]] = defaultdict(lambda: defaultdict(set))

    def join(self, book_id: int, user_id: int, ws: WebSocket) -> None:
        self._subs[book_id][user_id].add(ws)

    def leave(self, book_id: int, user_id: int, ws: WebSocket) -> None:
        conns = self._subs.get(book_id, {}).get(user_id)
        if conns is not None:
            conns.discard(ws)
            if not conns:
                del self._subs[book_id][user_id]
                if not self._subs[book_id]:  # 빈 책은 지운다 — 오래 떠 있는 서버에서 키가 쌓이지 않게
                    del self._subs[book_id]

    async def publish(self, book_id: int, recipients: set[int], message: dict) -> None:
        targets = [ws for uid in recipients for ws in list(self._subs.get(book_id, {}).get(uid, ()))]
        if targets:
            await asyncio.gather(*(self._send(ws, message) for ws in targets))

    @staticmethod
    async def _send(ws: WebSocket, message: dict) -> None:
        with contextlib.suppress(Exception):  # 끊긴 연결은 leave에서 정리된다
            await ws.send_json(message)


broker = Broker()
