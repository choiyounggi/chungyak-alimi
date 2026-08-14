"""공고 PDF 규칙 기반 분석 — 순수 함수(Task t1).

I/O(다운로드·DB 저장)는 하지 않는다 — 그건 파이프라인(t4)의 몫이다. 이 모듈은
바이트를 받아 텍스트를 추출하고, 키워드 규칙으로 "1순위 조건 / 유의사항 /
임대료·가격" 세 섹션을 구조화해 돌려준다. LLM 은 쓰지 않는다(사용자 결정).
"""
from __future__ import annotations

import unicodedata
from io import BytesIO

from pydantic import BaseModel
from pypdf import PdfReader

# 섹션 키·앵커(D4) — 고정 3키. t5 가 제목 매핑에 재사용할 수 있도록 모듈 상수로 export한다.
SECTION_ANCHORS: dict[str, tuple[str, ...]] = {
    "rank1": ("1순위", "신청자격", "입주자격", "청약자격"),
    "caution": ("유의사항", "유의 사항", "주의사항"),
    "price": ("임대조건", "임대료", "임대보증금", "공급금액", "분양가", "월임대료"),
}
SECTION_TITLES: dict[str, str] = {
    "rank1": "1순위 조건",
    "caution": "유의사항",
    "price": "임대료·가격",
}

_HEADING_MAX_LEN = 40  # 헤딩 줄로 인정하는 최대 길이(D5) — 넘으면 본문으로 본다.
_MAX_LINES_PER_SECTION = 40  # 섹션 하나당 수집하는 최대 줄 수(D5)
_MAX_CHARS_PER_SECTION = 3000  # 섹션 하나당 수집하는 최대 문자 수(D5)
_MAX_MATCHES_PER_KEY = 3  # 같은 키의 헤딩 매치 상한(D5) — 이후는 무시한다.
_MAX_SECTIONS_TOTAL = 12  # 섹션 전체 합계 상한(D5)


class PdfExtractError(Exception):
    """손상됐거나 암호화된 PDF 를 읽을 수 없을 때 던진다(D6)."""


class Section(BaseModel):
    key: str
    title: str
    lines: list[str]


class PdfAnalysis(BaseModel):
    page_count: int
    text_chars: int
    sections: list[Section]


def extract_text(data: bytes) -> tuple[str, int]:
    """PDF 바이트에서 정규화된 텍스트와 페이지 수를 뽑는다(D7).

    페이지별 텍스트를 개행으로 이어붙이고, 각 줄의 공백을 정규화한 뒤 NFC 로
    정규화한다(한글 자모 분리 방지). 손상됐거나 암호화된 PDF 는 PdfExtractError 로
    통일해 던진다(D6) — 원인은 `from e` 로 보존한다. 텍스트가 없는 스캔본은
    예외 없이 빈 문자열을 돌려준다.
    """
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise PdfExtractError("암호화된 PDF는 분석할 수 없습니다")
        page_count = len(reader.pages)
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
        # 실제 LH 공고 PDF에서 NUL(\x00)이 추출된다 — Postgres JSONB가
        # NUL 을 거부(UntranslatableCharacter)하므로 여기서 제거한다(2026-08-14 실측).
        raw = raw.replace("\x00", "")
    except PdfExtractError:
        raise
    except Exception as e:
        raise PdfExtractError("PDF를 읽을 수 없습니다") from e

    normalized_lines = [" ".join(line.split()) for line in raw.split("\n")]
    text = unicodedata.normalize("NFC", "\n".join(normalized_lines))
    return text, page_count


def _match_anchor(stripped_line: str) -> str | None:
    """헤딩 줄이면 매치된 섹션 키를, 아니면 None 을 돌려준다(D5)."""
    if not stripped_line or len(stripped_line) > _HEADING_MAX_LEN:
        return None
    for key, anchors in SECTION_ANCHORS.items():
        if any(anchor in stripped_line for anchor in anchors):
            return key
    return None


def summarize_text(text: str) -> list[Section]:
    """규칙 기반으로 텍스트를 최대 3키 섹션 목록으로 나눈다(D4/D5).

    헤딩 줄을 만나면 그 줄을 title 로, 다음 줄부터 다음 헤딩 줄 또는
    40줄/3000자 상한까지를 lines 로 모은다(빈 줄 제외). 같은 키는 최대 3번까지만
    새 섹션을 만들고, 전체 섹션 수는 12개를 넘지 않는다.
    """
    lines = text.split("\n")
    sections: list[Section] = []
    match_counts: dict[str, int] = {}

    i, n = 0, len(lines)
    while i < n:
        key = _match_anchor(lines[i].strip())
        if key is None:
            i += 1
            continue
        if match_counts.get(key, 0) >= _MAX_MATCHES_PER_KEY or len(sections) >= _MAX_SECTIONS_TOTAL:
            i += 1
            continue

        match_counts[key] = match_counts.get(key, 0) + 1
        section_lines: list[str] = []
        char_total = 0
        j = i + 1
        while j < n:
            candidate = lines[j].strip()
            if candidate and _match_anchor(candidate) is not None:
                break
            if candidate:
                if (
                    len(section_lines) >= _MAX_LINES_PER_SECTION
                    or char_total + len(candidate) > _MAX_CHARS_PER_SECTION
                ):
                    break
                section_lines.append(candidate)
                char_total += len(candidate)
            j += 1

        sections.append(Section(key=key, title=SECTION_TITLES[key], lines=section_lines))
        i = j

    return sections


def analyze_pdf_bytes(data: bytes) -> PdfAnalysis:
    """PDF 바이트 → 구조화된 섹션 요약(D2 합성 함수).

    추출과 요약을 그대로 이어붙인다 — 파싱 실패는 extract_text 가 던지는
    PdfExtractError 를 그대로 전파한다(호출자 t4 가 파일 단위 오류로 기록한다).
    """
    text, page_count = extract_text(data)
    sections = summarize_text(text)
    return PdfAnalysis(page_count=page_count, text_chars=len(text), sections=sections)
