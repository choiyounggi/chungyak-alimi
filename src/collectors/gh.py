from __future__ import annotations

import html
import logging
import re
import ssl
from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

GH_URL = "https://apply.gh.or.kr/co/coa/selectMainView.do"
# 상세 URL이 목록에 없다 — 청약센터 메인을 공고 링크로 쓴다.
GH_NOTICE_URL = GH_URL
# 주택이 아닌 공급유형은 수집 대상이 아니다(실측: "상가임대"가 섞여 있다).
EXCLUDED_BIZ_TY = ("상가",)
MAX_NAME_LEN = 200

# 대기열 호출문. 주석(`// NetFunnel_Action(`)은 비활성으로 본다.
_NETFUNNEL_RE = re.compile(r"^\s*(?!//)\s*NetFunnel_Action\s*\(", re.M)

_CARD_RE = re.compile(r'<a\b[^>]*\bdata-pbancNo="(\d+)"[^>]*>(.*?)</a>', re.S | re.I)
_BIZ_RE = re.compile(r'data-bizTyNm="([^"]*)"', re.I)
_NAME_RE = re.compile(r'<p class="leading-6[^"]*">(.*?)</p>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _netfunnel_active(html_text: str) -> bool:
    """대기열(NetFunnel)이 켜졌는지 판정한다.

    켜져 있으면 GH가 트래픽을 통제하겠다는 뜻이므로 우회하지 않고 수집을 포기한다(D15).
    주석(`// NetFunnel_Action(`) 상태는 비활성으로 본다.
    """
    return bool(_NETFUNNEL_RE.search(html_text))


def _clean(fragment: str) -> str:
    """외부 HTML 조각을 표시 가능한 평문으로 만든다: 태그 제거 → 언이스케이프 → 공백 정규화 → 길이 상한(D14)."""
    text = html.unescape(_TAG_RE.sub(" ", fragment))
    return " ".join(text.split())[:MAX_NAME_LEN]


def _parse_cards(html_text: str) -> list[dict]:
    """메인의 공고 카드를 `{"pbanc_no", "biz_ty", "name"}` 목록으로 뽑는다."""
    out: list[dict] = []
    for m in _CARD_RE.finditer(html_text):
        # data-bizTyNm 은 열림 태그 쪽에 있으므로 내부 HTML(그룹 2)이 아니라 매치 전체에서 찾는다.
        biz_m = _BIZ_RE.search(m.group(0))
        biz_ty = _clean(biz_m.group(1)) if biz_m else ""
        if any(x in biz_ty for x in EXCLUDED_BIZ_TY):
            continue
        name_m = _NAME_RE.search(m.group(2))
        if not name_m:
            continue
        name = _clean(name_m.group(1))
        if not name:
            continue
        out.append({"pbanc_no": m.group(1), "biz_ty": biz_ty or None, "name": name})
    return out


class GhNotice(BaseModel):
    """GH 공고 1건 (통합 notice 스키마에 맞춘 정규화)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pblanc_no: str
    house_nm: str
    house_manage_no: str | None = None  # 이름 기반 정정 그룹핑 경로(D25)
    house_secd_nm: str | None = None
    house_dtl_secd_nm: str | None = None
    rent_secd_nm: str | None = None
    area_nm: str | None = None
    hsslpy_adres: str | None = None
    bsns_mby_nm: str | None = None
    agency: str = "GH"

    # 목록에 접수기간·세대수·임대료가 없다(D16) — 전부 None 고정
    rcrit_pblanc_de: date | None = None
    rcept_bgnde: date | None = None
    rcept_endde: date | None = None
    spsply_rcept_bgnde: date | None = None
    spsply_rcept_endde: date | None = None
    przwner_presnatn_de: date | None = None
    tot_suply_hshldco: int | None = None
    mvn_prearnge_ym: str | None = None
    rent_gtn: int | None = None
    mt_rntchrg: int | None = None

    pblanc_url: str | None = None

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _prep(cls, data):
        if not isinstance(data, dict) or "pbanc_no" not in data:
            return data
        pbanc_no = str(data["pbanc_no"])
        biz_ty = data.get("biz_ty") or None
        return {
            "pblanc_no": f"gh-{pbanc_no}",
            "house_nm": data.get("name"),
            "house_secd_nm": biz_ty,
            "house_dtl_secd_nm": biz_ty,
            "area_nm": "경기",  # GH는 경기도 전용 공사
            "bsns_mby_nm": "경기주택도시공사",
            "pblanc_url": GH_NOTICE_URL,
            "raw": {"_gh_pbanc_no": pbanc_no, "_gh_biz_ty": biz_ty},
        }

    @field_validator("house_nm", mode="before")
    @classmethod
    def _correction_prefix(cls, v):
        # 정정공고 표기를 기존 규약(`[정정공고]`)으로 통일한다.
        s = str(v or "")
        if s.startswith("[정정]"):
            s = "[정정공고]" + s[len("[정정]") :]
        return s[:MAX_NAME_LEN]


def _ssl_context() -> ssl.SSLContext:
    """apply.gh.or.kr 전용 SSL 컨텍스트. 두 가지가 함께 필요하다(2026-08-04 실측).

    1) 서버가 forward secrecy 없는 RSA 키교환 암호(AES256-GCM-SHA384)만 제시한다.
       Python/OpenSSL 기본 암호 목록은 이를 거부해 handshake 가 실패하므로 그 한
       종류만 추가한다 — DEFAULT 강도와 인증서 검증은 그대로 둔다.
    2) 인증서 체인이 certifi 번들만으로는 검증되지 않아 OS 신뢰저장소가 필요하다.

    curl 은 두 조건을 기본으로 만족해 성공하므로, curl 로 되는데 httpx 로 안 되는
    형태로 드러난다.
    """
    try:
        import truststore

        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover — config.py 와 같은 best-effort 폴백
        ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384")
    return ctx


def fetch_gh_notices(*, client: httpx.Client | None = None) -> list[GhNotice]:
    """GH주택청약·임대센터 메인에서 공고 목록을 수집한다.

    대기열(NetFunnel)이 켜져 있으면 우회하지 않고 빈 리스트를 반환한다(D15).
    """
    own_client = client is None
    client = client or httpx.Client(
        timeout=30.0, follow_redirects=True, verify=_ssl_context()
    )
    try:
        resp = client.get(GH_URL)
        resp.raise_for_status()
        text = resp.text
    finally:
        if own_client:
            client.close()

    if _netfunnel_active(text):
        logger.warning("GH 대기열(NetFunnel) 활성 — 이번 회차 수집 건너뜀")
        return []

    out: list[GhNotice] = []
    seen: set[str] = set()
    for card in _parse_cards(text):
        try:
            n = GhNotice.model_validate(card)
        except ValidationError as e:
            logger.warning("GH 공고 파싱 실패 스킵: %s", e)
            continue
        if n.pblanc_no not in seen:
            seen.add(n.pblanc_no)
            out.append(n)
    return out
