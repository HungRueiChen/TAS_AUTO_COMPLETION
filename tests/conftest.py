"""Shared pytest fixtures for TAS extractor tests."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

SNAPSHOTS = Path(__file__).parent / "snapshots"


@pytest.fixture
async def page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        pg = await browser.new_page()
        yield pg
        await browser.close()


def snapshot(name: str) -> str:
    """Return the HTML content of a snapshot file."""
    return (SNAPSHOTS / name).read_text(encoding="utf-8")
