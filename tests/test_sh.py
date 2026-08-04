from __future__ import annotations

from datetime import date

import httpx

from src.collectors.sh import MAX_NAME_LEN, fetch_sh_notices

# ── 픽스처 (D23: 네트워크 금지, 실측 마크업 고정 문자열) ──

EMPTY_TABLE = "<table></table>"

# 실측 임대 행(공백·주석 그대로). 공고명 칸에 주석 처리된 <a href=...seq=1> 이 들어있다.
REAL_LEASE_ROW = """
  <tr>
    <td class="td1">77</td>
    <td class="td3">청년안심주택</td> <!-- 2021-01-25 클래스 수정 -->
    <td class="txl td-m">
      <!--\t<a href="/site/main/sh/publicLease/view?seq=1&cp=1&amp;supplyType=publicLease"></a>-->
      2026년 2차 청년안심주택(공공임대) 입주자 모집공고
    </td>
    <td class="td4">  2026-07-31  </td>
    <td class="td-mdisn">  2026-12-11  </td>
    <td class="td-mdisn">모집중</td>
    <td class="td-mdisn">맞춤주택공급부</td>
    <td class="td5"><a href="https://www.i-sh.co.kr/main/lay2/program/S1T294C295/www/brd/m_241/view.do?seq=307835" class="btn-gray" title="새창 이동" target="_blank">바로가기</a></td>
  </tr>
"""  # noqa: E501


def lease_row(
    *,
    no: str = "10",
    house_type: str = "국민임대",
    name: str = "테스트 임대 공고",
    posted: str = "2026-07-01",
    announce: str = "2026-09-01",
    status: str = "모집중",
    dept: str = "임대공급부",
    href: str | None = "https://www.i-sh.co.kr/brd/view.do?seq=900001",
) -> str:
    """임대 게시판 8칸 행. 테스트가 의존하는 값만 넘긴다."""
    link = f'<a href="{href}">바로가기</a>' if href else ""
    return (
        "<tr>"
        f'<td class="td1">{no}</td>'
        f'<td class="td3">{house_type}</td>'
        f'<td class="txl td-m">{name}</td>'
        f'<td class="td4">{posted}</td>'
        f'<td class="td-mdisn">{announce}</td>'
        f'<td class="td-mdisn">{status}</td>'
        f'<td class="td-mdisn">{dept}</td>'
        f'<td class="td5">{link}</td>'
        "</tr>"
    )


def sale_row(
    *,
    no: str = "5",
    house_type: str = "공공분양",
    name: str = "테스트 분양 공고",
    posted: str = "2026-07-02",
    announce: str = "-",
    dept: str = "분양공급부",
    href: str | None = "https://www.i-sh.co.kr/brd/view.do?seq=800002",
) -> str:
    """분양 게시판 7칸 행(모집상태 컬럼 없음)."""
    link = f'<a href="{href}">바로가기</a>' if href else ""
    return (
        "<tr>"
        f'<td class="td1">{no}</td>'
        f'<td class="td3">{house_type}</td>'
        f'<td class="txl td-m">{name}</td>'
        f'<td class="td4">{posted}</td>'
        f'<td class="td-mdisn">{announce}</td>'
        f'<td class="td-mdisn">{dept}</td>'
        f'<td class="td5">{link}</td>'
        "</tr>"
    )


def page(*rows: str) -> str:
    header = "<tr><th>번호</th><th>구분</th><th>공고명</th></tr>"
    return f"<table>{header}{''.join(rows)}</table>"


def client(*, lease: str = EMPTY_TABLE, sale: str = EMPTY_TABLE) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "publicLease" in request.url.path:
            return httpx.Response(200, text=lease)
        if "publicSale" in request.url.path:
            return httpx.Response(200, text=sale)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ── 정상 ──
def test_parses_real_row():
    with client(lease=page(REAL_LEASE_ROW)) as c:
        out = fetch_sh_notices(client=c)

    assert len(out) == 1
    n = out[0]
    assert n.pblanc_no == "lease-307835"
    assert n.house_nm == "2026년 2차 청년안심주택(공공임대) 입주자 모집공고"
    assert n.area_nm == "서울"
    assert n.agency == "SH"
    assert n.bsns_mby_nm == "서울주택도시공사"
    assert n.house_secd_nm == "청년안심주택"
    assert n.house_dtl_secd_nm == "청년안심주택"
    assert n.rcrit_pblanc_de == date(2026, 7, 31)
    assert n.przwner_presnatn_de == date(2026, 12, 11)
    assert n.pblanc_url.startswith("https://www.i-sh.co.kr/")
    # D16 — 목록에 접수기간이 없다
    assert n.rcept_bgnde is None
    assert n.rcept_endde is None
    # D25 else 가지(이름 기반 정정 그룹핑)를 타게 한다
    assert n.house_manage_no is None
    assert n.raw["_sh_board"] == "lease"
    assert n.raw["_sh_status"] == "모집중"
    assert n.raw["_sh_dept"] == "맞춤주택공급부"
    assert n.raw["_sh_row_no"] == "77"


def test_ignores_commented_out_anchor():
    """공고명 칸의 주석 <a href="...view?seq=1"> 이 링크로 잘못 잡히면 안 된다."""
    with client(lease=page(REAL_LEASE_ROW)) as c:
        out = fetch_sh_notices(client=c)

    assert out[0].pblanc_no != "lease-1"
    assert out[0].pblanc_no == "lease-307835"
    assert "seq=1&" not in (out[0].pblanc_url or "")


# ── 수집 정책 ──
def test_skips_non_recruiting_rows():
    """D16 — 임대는 모집상태가 '모집중'인 행만 수집한다."""
    lease = page(
        lease_row(name="모집중 공고", status="모집중", href="https://www.i-sh.co.kr/v?seq=111"),
        lease_row(name="마감 공고", status="접수마감", href="https://www.i-sh.co.kr/v?seq=222"),
    )
    with client(lease=lease) as c:
        out = fetch_sh_notices(client=c)

    assert [n.house_nm for n in out] == ["모집중 공고"]


def test_sale_board_seven_columns():
    """분양은 7칸(모집상태 없음) — 담당부서가 texts[5] 에서 나오고 전량 수집된다."""
    with client(sale=page(sale_row())) as c:
        out = fetch_sh_notices(client=c)

    assert len(out) == 1
    n = out[0]
    assert n.pblanc_no == "sale-800002"
    assert n.raw["_sh_board"] == "sale"
    assert n.raw["_sh_status"] is None
    assert n.raw["_sh_dept"] == "분양공급부"
    assert n.rcrit_pblanc_de == date(2026, 7, 2)
    assert n.przwner_presnatn_de is None  # "-" → None


def test_correction_prefix_normalized():
    """D26 규약과 통일 — '[정정]' 접두를 '[정정공고]' 로 치환한다."""
    raw_name = "[정정] 2026년 재개발임대주택 일반모집 공고(2026. 7. 30.)"
    with client(lease=page(lease_row(name=raw_name))) as c:
        out = fetch_sh_notices(client=c)

    assert out[0].house_nm.startswith("[정정공고]")
    assert "재개발임대주택" in out[0].house_nm


# ── 신뢰 경계(D14) ──
def test_rejects_foreign_host_link():
    with client(lease=page(lease_row(href="https://evil.example.com/x"))) as c:
        out = fetch_sh_notices(client=c)

    assert len(out) == 1
    assert out[0].pblanc_url is None
    # seq 를 못 뽑으므로 게시일+이름 해시로 대체된다
    assert out[0].pblanc_no.startswith("lease-2026-07-01-")


def test_long_name_truncated():
    """경계 — 300자 공고명은 상한(200자)으로 잘린다."""
    with client(lease=page(lease_row(name="가" * 300))) as c:
        out = fetch_sh_notices(client=c)

    assert len(out[0].house_nm) == MAX_NAME_LEN


# ── 파싱 실패 행 스킵(D9/D24) ──
def test_skips_row_with_empty_name():
    lease = page(
        lease_row(name="", href="https://www.i-sh.co.kr/v?seq=333"),
        lease_row(name="정상 공고", href="https://www.i-sh.co.kr/v?seq=444"),
    )
    with client(lease=lease) as c:
        out = fetch_sh_notices(client=c)

    assert [n.house_nm for n in out] == ["정상 공고"]


# ── 빈 응답 경계 ──
def test_empty_table_returns_empty():
    with client(lease=EMPTY_TABLE, sale=EMPTY_TABLE) as c:
        assert fetch_sh_notices(client=c) == []
