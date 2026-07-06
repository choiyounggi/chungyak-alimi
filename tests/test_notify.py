from __future__ import annotations

import httpx
import pytest
from sqlalchemy import delete, func, select

from src.db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    NotifyLog,
    SessionLocal,
    engine,
    init_db,
    save_match_results,
    upsert_notices,
)
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.notify import format_notice, notify_new_matches, send_telegram

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


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


# ── 포맷: 서비스 상세페이지 링크 포함 ──
def test_format_contains_service_link(monkeypatch):
    from src import notify

    monkeypatch.setattr(notify.settings, "public_base_url", "https://chungyak.duckdns.org")
    n = _notice(PBLANC_NO="2026000001")
    txt = format_notice(n, [])
    assert "https://chungyak.duckdns.org/notice/2026000001" in txt


# ── 포맷: 공고번호 URL 인코딩 + base 뒤 슬래시 정규화 (경계값) ──
def test_format_service_link_encodes_pblanc_no(monkeypatch):
    from src import notify

    monkeypatch.setattr(notify.settings, "public_base_url", "https://example.com/")
    n = _notice(PBLANC_NO="LH 2026/01")
    txt = format_notice(n, [])
    assert "https://example.com/notice/LH%202026%2F01" in txt
    assert "example.com//notice" not in txt


# ── 포맷: base URL 비우면 서비스 링크 미표시 ──
def test_format_service_link_disabled(monkeypatch):
    from src import notify

    monkeypatch.setattr(notify.settings, "public_base_url", "")
    txt = format_notice(_notice(), [])
    assert "/notice/" not in txt


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


# ── 보안: HTTP 에러 시 예외에 토큰이 노출되지 않음 ──
def test_send_http_error_no_token_leak():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError) as ei:
            send_telegram("hi", token="SECRET_TOKEN", chat_id="1", client=c)
    assert "SECRET_TOKEN" not in str(ei.value)
    assert "401" in str(ei.value)


# ── 에러: 토큰/chat_id 없으면 예외 ──
def test_send_requires_credentials(monkeypatch):
    # .env 에 값이 있어도 기본 폴백까지 비워 '자격증명 없음' 경로를 강제
    from src import notify

    monkeypatch.setattr(notify.settings, "tg_bot_token", "")
    monkeypatch.setattr(notify.settings, "tg_chat_id", "")
    with pytest.raises(RuntimeError):
        send_telegram("hi", token="", chat_id="")


db_only = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


# ── 핵심 불변조건: 한 공고는 한 번만 알림(dedup) ──
@db_only
def test_notify_new_matches_dedup(monkeypatch):
    from src import notify as notify_mod

    monkeypatch.setattr(notify_mod.settings, "tg_bot_token", "T")
    monkeypatch.setattr(notify_mod.settings, "tg_chat_id", "1")
    init_db()
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": "N1", "HOUSE_MANAGE_NO": "N1"})
        upsert_notices([n], session=s)
        save_match_results([("N1", True, [])], session=s)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        sent1 = notify_new_matches(client=c)
        sent2 = notify_new_matches(client=c)

    assert sent1 == 1          # 최초 발송
    assert sent2 == 0          # 재실행 시 중복 발송 없음
    assert calls["n"] == 1     # 텔레그램 호출도 1회뿐
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(NotifyLog)) == 1
        for t in (NotifyLog, MatchResult, Notice):
            s.execute(delete(t))
        s.commit()
