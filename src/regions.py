"""지역 정규화 · 해당지역 매칭 (D18/D19).

정책 주의(D19): 여기서 제공하는 "해당지역" 판정은 **거주지 ∪ 소득본거지**를 모두
해당지역으로 인정하는 *사용자 지정 정책*이다. 공식 청약 규칙(주택공급규칙)은
원칙적으로 입주자모집공고일 현재의 **거주지**만을 해당지역으로 본다.
소득본거지(직장 소재지 등) 인정은 이 서비스가 사용자에게 "볼 만한 공고"를 넓게
보여주기 위한 편의 정책이므로, 실제 청약 자격은 각 공고문으로 확인해야 한다.

area_nm 표본조사(2026-08, 수집기 코드 + 기존 테스트 픽스처 기준):

| 수집원      | notice.area_nm 값                      | 근거                                   |
|-------------|----------------------------------------|----------------------------------------|
| 청약홈      | "서울" / "경기" … (API가 약칭으로 제공) | models.py SUBSCRPT_AREA_CODE_NM        |
| LH          | "경남" … (REGION_MAP으로 약칭 변환)     | collectors/lh.py normalize_region      |
| 마이홈      | "경남" … (동일 변환)                    | collectors/myhome.py                   |
| HUG         | "서울" / "인천" … (동일 변환)           | collectors/hug.py                      |
| SH          | "서울" 고정                             | collectors/sh.py                       |
| GH          | "경기" 고정                             | collectors/gh.py                       |
| (모든 수집원) | None 가능                              | 지역 미상 공고                          |

→ 공고 쪽은 **시/도 약칭 또는 None**뿐이다. 반면 회원 지역
(`member_profile.residence_regions` / `income_base_regions`, JSONB list[str])은
사용자가 직접 입력하므로 "서울특별시", "경기도 성남시" 같은 자유 표기가 들어온다.
따라서 정규화는 풀네임·약칭·시 단위 표기를 모두 같은 정규형으로 접어야 한다.
"""

from __future__ import annotations

# 시/도 풀네임 → 약칭. 공고 쪽 저장 형태(약칭)에 맞춘다.
# (collectors/lh.py REGION_MAP과 같은 표 — 수집기는 수집 시점에, 여기는 회원 입력에 적용.
#  통합은 수집기 리팩터링 범위라 이 태스크에서는 건드리지 않는다.)
_CANON = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}

# _CANON에 없는 표기를 위한 접미사 제거 규칙(긴 것부터). 개편으로 새 표기가
# 생겨도("전남특별자치도") 정규형이 나오게 하는 안전망.
_SUFFIXES = ("특별자치도", "특별자치시", "특별시", "광역시", "도")

# 시 단위 예외맵(D18): 소득본거지 정책상 시 단위로 구분해야 하는 도시는
# 시/도로 접지 않고 시 단위 정규형을 유지한다. 여기에 없는 시("수원시")는
# 시/도로 승격되지 않으므로 안전하게 불일치 처리된다.
_CITY_ALIAS = {
    "성남": "성남",
    "성남시": "성남",
}

# 예외 시 → 상위 시/도. 회원 지역이 시 단위여도 그 시를 포함하는 시/도 공고는
# 해당지역으로 인정한다(포함 규칙). 역방향(시/도 거주 → 시 단위 공고)은
# 인정하지 않는다 — 경기도 거주자가 성남시 공고의 해당지역은 아니기 때문.
_CITY_PROVINCE = {
    "성남": "경기",
}


def normalize_region(s: str | None) -> str:
    """지역 표기를 정규형(시/도 약칭 또는 예외 시 이름)으로 바꾼다.

    빈 값·None·문자열이 아닌 값은 모두 `""`를 돌려준다(호출측에서 예외 처리 불필요).
    """
    if not isinstance(s, str):
        return ""
    compact = "".join(s.split())
    if not compact:
        return ""

    if compact in _CITY_ALIAS:
        return _CITY_ALIAS[compact]
    # "경기도성남시"처럼 시/도가 앞에 붙은 입력도 시 단위 예외로 접는다.
    for key, canon in _CITY_ALIAS.items():
        if compact.endswith(key):
            return canon

    if compact in _CANON:
        return _CANON[compact]

    for suffix in _SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix):
            stripped = compact[: -len(suffix)]
            return _CANON.get(stripped, stripped)
    return compact


def region_matches(notice_area: str | None, member_regions: list[str] | None) -> bool:
    """공고 지역이 회원 지역(거주지 ∪ 소득본거지) 중 하나와 해당지역으로 맞는가.

    정책(D19): 소득본거지 기반 인정은 사용자 지정 정책이며 공식 청약 규칙이 아니다.
    빈 공고 지역·빈 회원 지역은 안전하게 False(기타지역).
    """
    na = normalize_region(notice_area)
    if not na:
        return False

    for raw in member_regions or []:
        mr = normalize_region(raw)
        if not mr:
            continue
        if mr == na:
            return True
        # 포함 규칙: 회원 지역(성남)이 공고 지역(경기)에 속하면 해당지역.
        if _CITY_PROVINCE.get(mr) == na:
            return True
    return False
