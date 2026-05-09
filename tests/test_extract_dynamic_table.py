"""Golden-snapshot tests for dynamic table extraction."""

import pytest
import pytest_asyncio

from src.extractor import extract_form_structure
from tests.conftest import snapshot


@pytest.mark.asyncio
async def test_dynamic_section_detected(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    sections = result["sections"]
    assert len(sections) == 1, f"Expected 1 dynamic section, got {len(sections)}"


@pytest.mark.asyncio
async def test_dynamic_section_title(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    section = result["sections"][0]
    assert section.section_title == "病歷資料"


@pytest.mark.asyncio
async def test_dynamic_section_column_headers(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    section = result["sections"][0]
    assert section.column_headers == ["就醫日期", "診斷"]


@pytest.mark.asyncio
async def test_dynamic_section_column_types(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    section = result["sections"][0]
    assert section.column_types == ["date", "text"]


@pytest.mark.asyncio
async def test_dynamic_section_existing_rows(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    section = result["sections"][0]
    assert section.existing_rows == 1, (
        f"Expected 1 existing row (the visible data row), got {section.existing_rows}"
    )


@pytest.mark.asyncio
async def test_dynamic_section_index(page):
    await page.set_content(snapshot("dynamic_table.html"))
    result = await extract_form_structure(page)

    section = result["sections"][0]
    assert section.section_index == 0
