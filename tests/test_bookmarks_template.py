"""bookmarks.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

base 상속 + 공유 카드 매크로(notice_card) 사용 + 빈 상태를 검증한다.
컨텍스트 키는 app.py::bookmarks_page 가 넘기는 {items} 와 맞춘다.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import jinja2

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
BM = TEMPLATES / "bookmarks.html"


def _env() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)


def _notice(**over):
    base = dict(
        house_nm="북마크힐스테이트", pblanc_no="2026000999", area_nm="서울",
        house_secd_nm="APT", house_dtl_secd_nm="민영",
        rcept_bgnde="2026-08-01", rcept_endde="2026-08-05", tot_suply_hshldco=200,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _item(**over):
    base = dict(
        notice=_notice(), my_rank="1순위", specials=[], adres="서울특별시 강남구 대치동",
        lat=37.5, lng=127.05, price_lo=60000, price_hi=60000, area_lo=84.9, area_hi=84.9,
        deadline=None, dday=3, bookmarked=True,
    )
    base.update(over)
    return base


def _render(items) -> str:
    return _env().get_template("bookmarks.html").render(items=items)


# ── 정상: 북마크 목록 1건 → base 상속 + 매크로 카드 + 북마크 on 상태 ──
def test_bookmarks_renders_list():
    out = _render([_item()])
    assert 'id="i-pin"' in out            # base 상속(스프라이트)
    assert 'class="topnav"' in out        # 상단 내비 상속
    assert "북마크" in out                # 헤더
    assert 'class="count"' in out
    assert "북마크힐스테이트" in out       # 공유 카드 매크로 렌더
    assert 'href="/notice/2026000999"' in out
    assert "bookmark-btn is-on" in out    # 북마크 목록이므로 on
    assert 'aria-pressed="true"' in out
    assert "공공 오픈API(청약홈/LH) 기반" in out  # 푸터


# ── 경계값: 빈 목록 → 빈 상태 안내 + 카드 0개, 예외 없음 ──
def test_bookmarks_empty():
    out = _render([])
    assert "아직 북마크한 공고가 없어요" in out
    assert 'class="card"' not in out


# ── error-assert(DoD): 이모지 0 + 첫 줄 extends + 블록 계약 ──
def test_bookmarks_source_no_emoji_and_extends_base():
    raw = BM.read_text(encoding="utf-8")
    emoji = re.findall(r"[\U0001F000-\U0001FAFF☀-➿]", raw)
    assert emoji == [], f"bookmarks.html 내 이모지 발견: {emoji}"
    first = next(ln for ln in raw.splitlines() if ln.strip())
    assert first.strip() == '{% extends "base.html" %}', f"첫 줄 extends 아님: {first!r}"
    for blk in ("title", "content", "footer"):
        assert f"block {blk}" in raw, f"블록 누락: {blk}"
    assert "<!DOCTYPE" not in raw
    assert ":root{" not in raw
