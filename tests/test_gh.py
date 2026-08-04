from __future__ import annotations

import logging
import ssl

import httpx

from src.collectors.gh import GH_NOTICE_URL, GH_URL, _ssl_context, fetch_gh_notices

# 실측 카드 마크업(2026-08-04, apply.gh.or.kr) — 속성이 일부 깨진 원본 그대로 쓴다.
REAL_CARD = """
  <li class="all">
    <a class="thbox brd-df apy-c1" href="javascript:void(0);"
    data-previewYn="N"
    data-pbancNo="801"
    data-pbancKndCd="01"
    data-bizTyNm="국민임대"
    data-bizTyCd="02"
    >
      <span class="bg-blue_mt px-3 py-2 rounded-[4px] text-white text-xs" rentalhouse ">
      국민임대
      </span>
      <p class="leading-6 line-clamp-2 max-h-[46px] my-2">다산센트럴파크6단지 국민임대주택 예비입주자 모집공고
      </p>
      <p class="calender_box"><span class="">신청기간  ~  </span></p>
      <span class="  status-on  mt-3 py-2 py-3 text-center w-full "></span>
    </a>
  </li>
"""

# 현재 프로덕션 루트 스텁 상태 — 대기열 호출이 주석 처리되어 있다.
COMMENTED_NETFUNNEL = """
    // NetFunnel_Action({action_id:"service_main"}, function(ev, ret) {});
"""

# 대기열이 다시 켜졌을 때의 형태.
ACTIVE_NETFUNNEL = """
    NetFunnel_Action({action_id:"service_main"}, function(ev, ret) {});
"""


def _card(pbanc_no: str, biz_ty: str, name: str) -> str:
    """실측 카드와 같은 구조의 카드를 만든다(속성 깨짐 포함)."""
    return f"""
  <li class="all">
    <a class="thbox brd-df apy-c1" href="javascript:void(0);"
    data-previewYn="N"
    data-pbancNo="{pbanc_no}"
    data-pbancKndCd="01"
    data-bizTyNm="{biz_ty}"
    data-bizTyCd="02"
    >
      <span class="bg-blue_mt px-3 py-2 rounded-[4px] text-white text-xs" rentalhouse ">
      {biz_ty}
      </span>
      <p class="leading-6 line-clamp-2 max-h-[46px] my-2">{name}
      </p>
      <p class="calender_box"><span class="">신청기간  ~  </span></p>
      <span class="  status-on  mt-3 py-2 py-3 text-center w-full "></span>
    </a>
  </li>
"""


def _page(cards: str = "", script: str = COMMENTED_NETFUNNEL) -> str:
    return (
        "<html><body><ul class='apy-list'>"
        + cards
        + "</ul>\n<script>"
        + script
        + "</script></body></html>"
    )


def _fetch(html_text: str):
    """MockTransport 로 고정 HTML 을 주입해 수집한다(실제 네트워크 호출 없음, D23)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=html_text)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = fetch_gh_notices(client=c)
    assert calls == [GH_URL]
    return out


# ── 정상 ──
def test_parses_real_card():
    out = _fetch(_page(REAL_CARD))
    assert len(out) == 1
    n = out[0]
    assert n.pblanc_no == "gh-801"
    assert n.house_nm.startswith("다산센트럴파크6단지")
    assert n.house_secd_nm == "국민임대"
    assert n.house_dtl_secd_nm == "국민임대"
    assert n.area_nm == "경기"
    assert n.agency == "GH"
    assert n.bsns_mby_nm == "경기주택도시공사"
    assert n.house_manage_no is None
    assert n.rcept_bgnde is None
    assert n.rcept_endde is None  # 목록에 접수기간이 없다(D16)
    assert n.pblanc_url == GH_NOTICE_URL
    assert n.raw == {"_gh_pbanc_no": "801", "_gh_biz_ty": "국민임대"}


def test_excludes_commercial_lease():
    html_text = _page(REAL_CARD + _card("900", "상가임대", "광교 상가 임대 공고"))
    out = _fetch(html_text)
    assert [n.pblanc_no for n in out] == ["gh-801"]


# ── NetFunnel(D15) ──
def test_netfunnel_active_returns_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="src.collectors.gh"):
        out = _fetch(_page(REAL_CARD, script=ACTIVE_NETFUNNEL))
    assert out == []
    assert any("NetFunnel" in r.message % r.args for r in caplog.records)


def test_netfunnel_commented_out_is_inactive():
    out = _fetch(_page(REAL_CARD, script=COMMENTED_NETFUNNEL))
    assert [n.pblanc_no for n in out] == ["gh-801"]


# ── 파싱 실패/누락 행 스킵 ──
def test_card_without_name_skipped():
    html_text = _page(_card("900", "국민임대", "") + REAL_CARD)
    out = _fetch(html_text)
    assert [n.pblanc_no for n in out] == ["gh-801"]


# ── 경계 ──
def test_no_cards_returns_empty():
    assert _fetch(_page("")) == []


def test_long_name_truncated():
    out = _fetch(_page(_card("801", "국민임대", "가" * 300)))
    assert len(out) == 1
    assert len(out[0].house_nm) <= 200


def test_dedup_same_pbanc_no():
    out = _fetch(_page(REAL_CARD + REAL_CARD))
    assert len(out) == 1
    assert out[0].pblanc_no == "gh-801"


# ── 회귀: TLS handshake 실패(2026-08-04 라이브 검증에서 발견) ──
# apply.gh.or.kr 은 forward secrecy 없는 RSA 키교환 암호만 제시해 기본 컨텍스트로는
# 연결 자체가 안 된다. 목 기반 테스트로는 잡히지 않아 실물 호출에서 드러났다.
def test_ssl_context_allows_gh_rsa_cipher():
    ctx = _ssl_context()
    assert "AES256-GCM-SHA384" in {c["name"] for c in ctx.get_ciphers()}


def test_ssl_context_still_verifies_certificates():
    """암호를 완화하되 인증서 검증을 끄지 않았는지 — 완화가 검증 무력화로 번지면 안 된다."""
    ctx = _ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
