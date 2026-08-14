"""src/analysis.py 단위 테스트(Task t1).

extract_text 의 정상 경로는 PDF 파싱이 필요하므로 원시 PDF 1.4 문법으로 조립한
최소 픽스처(_make_pdf)를 쓴다 — 외부 의존성 없이 ASCII 텍스트만 담는다.
한글 텍스트를 쓰는 테스트는 인코딩을 우회하기 위해 summarize_text 를 직접 호출한다.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

from src.analysis import (
    SECTION_ANCHORS,
    SECTION_TITLES,
    PdfExtractError,
    Section,
    analyze_pdf_bytes,
    extract_text,
    summarize_text,
)


def _make_pdf(lines: list[str]) -> bytes:
    """ASCII 텍스트를 담은 최소 1페이지 PDF 를 원시 PDF 1.4 문법으로 조립한다."""
    content_lines = ["BT", "/F1 12 Tf", "10 780 Td", "14 TL"]
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i > 0:
            content_lines.append("T*")
        content_lines.append(f"({escaped}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1")
        out += obj
        out += b"\nendobj\n"

    xref_offset = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += b"trailer\n"
    out += f"<< /Size {n} /Root 1 0 R >>\n".encode("latin-1")
    out += b"startxref\n"
    out += f"{xref_offset}\n".encode("latin-1")
    out += b"%%EOF"
    return bytes(out)


def _make_blank_pdf() -> bytes:
    """텍스트가 전혀 없는 스캔본 흉내(빈 페이지)."""
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── extract_text ──────────────────────────────────────────────────────────


def test_extract_text_returns_normalized_text_and_page_count():
    data = _make_pdf(["Hello World", "Second Line"])
    text, page_count = extract_text(data)
    assert page_count == 1
    assert "Hello World" in text
    assert "Second Line" in text


def test_extract_text_scanned_pdf_returns_empty_text_not_error():
    data = _make_blank_pdf()
    text, page_count = extract_text(data)
    assert page_count == 1
    assert text == ""


def test_extract_text_corrupted_bytes_raise_pdf_extract_error():
    with pytest.raises(PdfExtractError):
        extract_text(b"not a pdf")


# ── analyze_pdf_bytes ─────────────────────────────────────────────────────


def test_analyze_pdf_bytes_normal_path_reports_char_count():
    data = _make_pdf(["Hello World"])
    result = analyze_pdf_bytes(data)
    assert result.page_count == 1
    assert result.text_chars > 0


def test_analyze_pdf_bytes_corrupted_pdf_raises():
    with pytest.raises(PdfExtractError):
        analyze_pdf_bytes(b"not a pdf")


def test_analyze_pdf_bytes_scanned_pdf_has_no_sections():
    data = _make_blank_pdf()
    result = analyze_pdf_bytes(data)
    assert result.text_chars == 0
    assert result.sections == []


# ── summarize_text: 정상 ──────────────────────────────────────────────────


def test_summarize_text_extracts_all_three_section_keys():
    text = "\n".join(
        [
            "1순위 신청자격",
            "무주택세대구성원만 신청 가능",
            "청약통장 가입 24개월 이상",
            "유의사항",
            "허위 신청 시 당첨 취소됩니다",
            "임대조건",
            "보증금 5천만원, 월세 30만원 예정",
        ]
    )
    sections = summarize_text(text)
    keys = [s.key for s in sections]
    assert keys == ["rank1", "caution", "price"]

    rank1 = sections[0]
    assert isinstance(rank1, Section)
    assert rank1.title == SECTION_TITLES["rank1"]
    assert rank1.lines == ["무주택세대구성원만 신청 가능", "청약통장 가입 24개월 이상"]

    caution = sections[1]
    assert caution.title == SECTION_TITLES["caution"]
    assert caution.lines == ["허위 신청 시 당첨 취소됩니다"]

    price = sections[2]
    assert price.title == SECTION_TITLES["price"]
    assert price.lines == ["보증금 5천만원, 월세 30만원 예정"]


# ── summarize_text: 경계 ──────────────────────────────────────────────────


def test_summarize_text_empty_input_returns_empty_list():
    assert summarize_text("") == []


def test_summarize_text_anchor_in_long_line_is_not_treated_as_heading():
    long_line = "이 공고문은 임대료 관련 세부 안내를 포함하는 매우 긴 설명 문장입니다 40자 초과 확인용"
    assert len(long_line) > 40
    text = "\n".join([long_line, "본문 내용"])
    assert summarize_text(text) == []


def test_summarize_text_fourth_match_of_same_key_is_ignored():
    blocks = []
    for i in range(4):
        blocks.append("유의사항")
        blocks.append(f"본문 {i}")
    text = "\n".join(blocks)
    sections = summarize_text(text)
    caution_sections = [s for s in sections if s.key == "caution"]
    assert len(caution_sections) == 3


def test_summarize_text_truncates_section_at_40_lines():
    body_lines = [f"본문 {i}줄" for i in range(50)]
    text = "\n".join(["유의사항", *body_lines])
    sections = summarize_text(text)
    assert len(sections) == 1
    assert len(sections[0].lines) == 40


def test_summarize_text_removes_blank_lines_from_section_body():
    text = "\n".join(["유의사항", "", "실제 내용 줄", "", "", "다음 줄"])
    sections = summarize_text(text)
    assert sections[0].lines == ["실제 내용 줄", "다음 줄"]


def test_section_anchors_and_titles_share_the_same_three_keys():
    assert set(SECTION_ANCHORS.keys()) == {"rank1", "caution", "price"}
    assert set(SECTION_TITLES.keys()) == {"rank1", "caution", "price"}


# ── NUL(\x00) 제거: 실제 LH PDF에서 NUL이 추출되어 Postgres JSONB 저장이 깨졌다(2026-08-14 실측) ──
def test_extract_text_strips_nul_characters():
    data = _make_pdf(["AB\x00CD", "line\x00two"])
    text, page_count = extract_text(data)
    assert page_count == 1
    assert "\x00" not in text
    assert "ABCD" in text  # NUL 만 제거되고 양옆 문자는 보존된다


def test_analyze_pdf_bytes_output_never_contains_nul():
    data = _make_pdf(["1st rank\x00section"])
    result = analyze_pdf_bytes(data)
    assert "\x00" not in str(result.model_dump())
    assert result.text_chars > 0
