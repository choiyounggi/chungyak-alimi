from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from src.db import (
    SUPERSEDED_REASON,
    MatchResult,
    Notice,
    NoticeHouseType,
    NotifyLog,
    SessionLocal,
    engine,
    evaluate_all,
    init_db,
)
from src.filters import FilterConfig, find_superseded


def _lh(pblanc_no: str, name: str, bgnde: date):
    return SimpleNamespace(
        pblanc_no=pblanc_no,
        source="lh",
        house_manage_no=None,
        house_nm=name,
        rcrit_pblanc_de=None,
        rcept_bgnde=bgnde,
    )


def _ah(pblanc_no: str, hmn: str | None, name: str, pblanc_de: date):
    return SimpleNamespace(
        pblanc_no=pblanc_no,
        source="applyhome",
        house_manage_no=hmn,
        house_nm=name,
        rcrit_pblanc_de=pblanc_de,
        rcept_bgnde=None,
    )


# ── LH: 원본 + 정정 2건 → 최신 정정만 남김 (실데이터 시흥 케이스) ──
def test_lh_original_and_two_corrections():
    ns = [
        _lh("A1", "시흥시 10년 공공임대주택 예비입주자 모집공고", date(2026, 5, 14)),
        _lh("A2", "[정정공고]시흥시 10년 공공임대주택 예비입주자 모집공고", date(2026, 5, 21)),
        _lh("A3", "[정정공고]시흥시 10년 공공임대주택 예비입주자 모집공고", date(2026, 5, 27)),
    ]
    assert find_superseded(ns) == {"A1": "A3", "A2": "A3"}


# ── LH: 정정의 정정(이중 접두사), 원본 미수집 (실데이터 남양주 케이스) ──
def test_lh_correction_of_correction():
    ns = [
        _lh("B1", "[정정공고]남양주왕숙2 A-3BL 공공분양주택 입주자모집공고", date(2026, 5, 8)),
        _lh("B2", "[정정공고][정정공고]남양주왕숙2 A-3BL 공공분양주택 입주자모집공고", date(2026, 5, 13)),
    ]
    assert find_superseded(ns) == {"B1": "B2"}


# ── LH: 게시일이 같으면 정정 횟수로 판별 (경계값) ──
def test_lh_same_date_tiebreak_by_prefix():
    ns = [
        _lh("C1", "고양시 국민임대 예비입주자 모집공고", date(2026, 5, 6)),
        _lh("C2", "[정정공고]고양시 국민임대 예비입주자 모집공고", date(2026, 5, 6)),
    ]
    assert find_superseded(ns) == {"C1": "C2"}


# ── LH: 동명이지만 정정공고가 없는 그룹은 건드리지 않음 (오탐 방지) ──
def test_lh_same_name_without_correction_untouched():
    ns = [
        _lh("D1", "전국 매입임대 모집공고", date(2026, 3, 1)),
        _lh("D2", "전국 매입임대 모집공고", date(2026, 6, 1)),
    ]
    assert find_superseded(ns) == {}


# ── LH: 정정공고만 있고 원본이 없으면 대체 없음 ──
def test_lh_correction_alone_untouched():
    ns = [_lh("E1", "[정정공고]단독 정정공고", date(2026, 5, 1))]
    assert find_superseded(ns) == {}


# ── 청약홈: 같은 주택관리번호 → 최신 공고만, 번호 없으면 제외 ──
def test_applyhome_by_house_manage_no():
    ns = [
        _ah("F1", "H100", "래미안 어쩌구", date(2026, 4, 1)),
        _ah("F2", "H100", "래미안 어쩌구(정정)", date(2026, 4, 10)),
        _ah("F3", "H200", "힐스테이트 저쩌구", date(2026, 4, 1)),
        _ah("F4", None, "관리번호 없는 공고", date(2026, 4, 1)),
        _ah("F5", None, "관리번호 없는 공고", date(2026, 4, 2)),
    ]
    assert find_superseded(ns) == {"F1": "F2"}


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


# ── 통합: evaluate_all이 대체된 공고를 matched=false + 사유로 기록 ──
@pytestmark_db
def test_evaluate_all_marks_superseded():
    from src.collectors.lh import LhNotice
    from src.db import upsert_notices

    init_db()
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        common = {
            "CNP_CD_NM": "경기도", "CLSG_DT": "2099.12.31", "CCR_CNNT_SYS_DS_CD": "03",
            "SPL_INF_TP_CD": "050", "UPP_AIS_TP_CD": "05", "AIS_TP_CD": "05",
        }
        orig = LhNotice.model_validate({
            **common, "PAN_ID": "SUP1", "PAN_NM": "정정테스트 모집공고",
            "PAN_NT_ST_DT": "2026.05.14",
        })
        corr = LhNotice.model_validate({
            **common, "PAN_ID": "SUP2", "PAN_NM": "[정정공고]정정테스트 모집공고",
            "PAN_NT_ST_DT": "2026.05.21",
        })
        upsert_notices([orig, corr], source="lh", session=s)

    evaluate_all(FilterConfig(), today=date(2026, 7, 6))

    with SessionLocal() as s:
        mr_orig = s.scalar(select(MatchResult).where(MatchResult.pblanc_no == "SUP1"))
        mr_corr = s.scalar(select(MatchResult).where(MatchResult.pblanc_no == "SUP2"))
        assert mr_orig.matched is False
        assert mr_orig.fail_reasons == [f"{SUPERSEDED_REASON}:SUP2"]
        assert mr_corr.matched is True  # 빈 필터 → 정정공고는 정상 매칭
        for t in (NotifyLog, MatchResult, Notice):
            s.execute(delete(t))
        s.commit()
