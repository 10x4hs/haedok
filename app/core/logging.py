"""구조화 로그 — stdlib logging + JSON 한 줄 포맷(stdout).

서버리스(Vercel)에서는 stdout 한 줄이 로그 한 건이라 JSON으로 내보내야 필드별 검색이 된다.
요청 id는 contextvar에 담아, 요청 처리 중 찍히는 모든 로그(메일 등)에 같이 붙는다.
"""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# 로그 레코드에 붙이는 요청 필드 — access 로그가 extra로 넘긴다
REQUEST_FIELDS = ("request_id", "path", "method", "status", "duration_ms", "client")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None) or request_id_var.get()
        if rid:
            entry["request_id"] = rid
        for key in REQUEST_FIELDS:
            if key != "request_id" and (val := getattr(record, key, None)) is not None:
                entry[key] = val
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """로컬 개발용 — 같은 필드를 사람이 읽기 쉬운 한 줄로."""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None) or request_id_var.get()
        when = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        head = f"{when} {record.levelname:<7} {record.name}"
        tail = " ".join(
            f"{k}={getattr(record, k)}" for k in REQUEST_FIELDS if getattr(record, k, None) is not None
        )
        line = f"{head} {record.getMessage()}"
        if rid and "request_id" not in tail:
            line += f" request_id={rid}"
        if tail:
            line += f" {tail}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO", json_lines: bool = True) -> None:
    """루트 로거를 stdout 핸들러 하나로 맞춘다. 여러 번 불려도(테스트·리로드) 핸들러가 쌓이지 않는다."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_haedok", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler._haedok = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_lines else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn의 기본 access 로그는 우리 요청 로그와 겹치므로 끈다 (오류 로그는 둔다)
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
