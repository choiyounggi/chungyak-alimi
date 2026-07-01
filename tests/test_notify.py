from __future__ import annotations

import httpx
import pytest

from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.notify import format_notice, send_telegram

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT


def _notice(**over) -> ApplyhomeNotice:
    return ApplyhomeNotice.model_validate({**SAMPLE, **over})


def _ht(**over) -> ApplyhomeHouseType:
    return ApplyhomeHouseType.model_validate({**SAMPLE_HT, **over})


# ── 포맷: 핵심 정보 포함 ──
def test_format_contains_key_fields():
    n = _notice()
    txt = format_notice(n, [_ht(LTTOT_TOP_AMOUNT="50724")])
    assert n.house_nm in txt
    assert "경기" in txt
    assert "50,724만원" in txt


# ── 포맷: 분양가 범위 표기 ──
def test_format_price_range():
    n = _notice()
    txt = format_notice(n, [_ht(HOUSE_TY="A", LTTOT_TOP_AMOUNT="50000"),
                            _ht(HOUSE_TY="B", LTTOT_TOP_AMOUNT="70000")])
    assert "50,000~70,000만원" in txt


# ── 포맷: 분양가 없으면 '-' (경계값) ──
def test_format_no_price():
    txt = format_notice(_notice(), [_ht(LTTOT_TOP_AMOUNT="")])
    assert "-" in txt


# ── 발송: 성공 페이로드 검증 ──
def test_send_telegram_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = send_telegram("hi", token="T", chat_id="123", client=c)
    assert out["ok"] is True
    assert "botT/sendMessage" in captured["url"]
    assert captured["body"]["chat_id"] == "123"
    assert captured["body"]["text"] == "hi"


# ── 에러: 토큰/chat_id 없으면 예외 ──
def test_send_requires_credentials(monkeypatch):
    # .env 에 값이 있어도 기본 폴백까지 비워 '자격증명 없음' 경로를 강제
    from src import notify

    monkeypatch.setattr(notify.settings, "tg_bot_token", "")
    monkeypatch.setattr(notify.settings, "tg_chat_id", "")
    with pytest.raises(RuntimeError):
        send_telegram("hi", token="", chat_id="")
