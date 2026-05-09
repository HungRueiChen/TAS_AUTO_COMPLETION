"""Golden-snapshot tests for fixed table section extraction."""

import pytest

from src.extractor import extract_form_structure
from tests.conftest import snapshot


@pytest.mark.asyncio
async def test_fixed_section_detected(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    fixed = result["fixed_sections"]
    assert len(fixed) == 1, f"Expected 1 fixed section, got {len(fixed)}"


@pytest.mark.asyncio
async def test_fixed_section_title(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    section = result["fixed_sections"][0]
    assert section["title"] == "觀察項目"


@pytest.mark.asyncio
async def test_fixed_section_row_count(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    rows = result["fixed_sections"][0]["rows"]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_fixed_section_row_labels(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    rows = result["fixed_sections"][0]["rows"]
    assert rows[0].row_label == "病史詢問"
    assert rows[1].row_label == "身體檢查"


@pytest.mark.asyncio
async def test_fixed_section_cell_types(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    row = result["fixed_sections"][0]["rows"][0]
    types = [c["type"] for c in row.cells]
    assert "radio" in types
    assert "text" in types


@pytest.mark.asyncio
async def test_fixed_section_not_in_dynamic(page):
    await page.set_content(snapshot("fixed_table.html"))
    result = await extract_form_structure(page)

    assert result["sections"] == [], "Fixed-only page should have no dynamic sections"
