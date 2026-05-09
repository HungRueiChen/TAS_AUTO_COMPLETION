"""Golden-snapshot tests for standalone (form-group) question extraction."""

import pytest

from src.extractor import extract_form_structure
from src.models import WidgetType
from tests.conftest import snapshot


@pytest.mark.asyncio
async def test_radio_question_detected(page):
    await page.set_content(snapshot("standalone_radio.html"))
    result = await extract_form_structure(page)

    questions = result["questions"]
    assert len(questions) >= 1


@pytest.mark.asyncio
async def test_radio_widget_type(page):
    await page.set_content(snapshot("standalone_radio.html"))
    result = await extract_form_structure(page)

    q = result["questions"][0]
    assert q.widget_type == WidgetType.RADIO


@pytest.mark.asyncio
async def test_radio_question_prompt(page):
    await page.set_content(snapshot("standalone_radio.html"))
    result = await extract_form_structure(page)

    q = result["questions"][0]
    assert "整體評估" in q.prompt


@pytest.mark.asyncio
async def test_radio_options_extracted(page):
    await page.set_content(snapshot("standalone_radio.html"))
    result = await extract_form_structure(page)

    q = result["questions"][0]
    assert len(q.options) == 3
    option_texts = " ".join(q.options)
    assert "優" in option_texts
    assert "良" in option_texts


@pytest.mark.asyncio
async def test_radio_required_flag(page):
    await page.set_content(snapshot("standalone_radio.html"))
    result = await extract_form_structure(page)

    q = result["questions"][0]
    assert q.required is True


@pytest.mark.asyncio
async def test_datepicker_widget_type(page):
    await page.set_content(snapshot("standalone_datepicker.html"))
    result = await extract_form_structure(page)

    questions = result["questions"]
    assert len(questions) >= 1
    q = questions[0]
    assert q.widget_type == WidgetType.DATE


@pytest.mark.asyncio
async def test_datepicker_prompt(page):
    await page.set_content(snapshot("standalone_datepicker.html"))
    result = await extract_form_structure(page)

    q = result["questions"][0]
    assert "看診日期" in q.prompt
