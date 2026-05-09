"""Navigator module — Playwright browser automation for TAS site navigation."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from .config import get_settings
from .models import FormSummary
from .safety import register_blocker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state (managed by start / stop helpers)
# ---------------------------------------------------------------------------

_playwright: Playwright | None = None
_context: BrowserContext | None = None


async def launch_browser() -> BrowserContext:
    """Launch (or reuse) a persistent Chromium browser context.

    The user logs in manually; automation waits for authenticated state.
    """
    global _playwright, _context
    if _context is not None:
        return _context

    settings = get_settings()
    data_dir = Path(settings.browser_data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    _playwright = await async_playwright().start()
    _context = await _playwright.chromium.launch_persistent_context(
        user_data_dir=str(data_dir),
        headless=False,
        bypass_csp=True,  # Bypass Content Security Policy — fixes Cloudflare/script errors
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
        ],
    )

    # Register the safety blocker on every new page
    _context.on("page", lambda page: asyncio.ensure_future(register_blocker(page)))
    for page in _context.pages:
        await register_blocker(page)

    logger.info("Browser launched with persistent context at %s", data_dir)
    return _context


async def get_page() -> Page:
    """Return the first page of the browser context, or create one."""
    ctx = await launch_browser()
    if ctx.pages:
        return ctx.pages[0]
    return await ctx.new_page()


async def close_browser() -> None:
    """Gracefully shut down the browser context."""
    global _playwright, _context
    if _context:
        await _context.close()
        _context = None
    if _playwright:
        await _playwright.stop()
        _playwright = None


async def start_trace() -> None:
    """Begin a Playwright trace for the current fill run (screenshots + DOM snapshots)."""
    if _context is None:
        return
    await _context.tracing.start(screenshots=True, snapshots=True)


async def stop_trace(form_title: str) -> Path | None:
    """Stop the active trace and save it to traces/<timestamp>_<form>.zip."""
    if _context is None:
        return None
    traces_dir = Path("traces")
    traces_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w-]", "_", form_title)[:40]
    path = traces_dir / f"{ts}_{safe}.zip"
    await _context.tracing.stop(path=str(path))
    logger.info("Trace saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


async def navigate_to_home(page: Page) -> None:
    """Navigate to the TAS main page (Index.action — the post-login landing).

    After login, TAS redirects here.  This page has navigation links
    including '我的護照' which leads to the course list.
    """
    settings = get_settings()
    # Initial launch goes to the TAS main page
    base = settings.tas_base_url.rstrip("/")
    url = base.rsplit("/", 1)[0] + "/tas/Index.action"
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    logger.info("Navigated to TAS home: %s", url)


async def _is_logged_in(page: Page) -> bool:
    """Check whether the user is logged in.

    Works on both the mainpage (Index.action) and the course list
    (UserPassport.action) by looking for authenticated-only elements.
    """
    # Course list page has this table
    table = await page.query_selector("table.table-hover.table-striped")
    if table:
        return True
    # Main page has a menuBar nav and/or userBar div (only visible when logged in)
    menu = await page.query_selector("nav.menuBar")
    if menu:
        return True
    user_bar = await page.query_selector("div.userBar")
    if user_bar:
        return True
    # Header navbar has user name link in li.n-amber
    user_link = await page.query_selector("li.n-amber a")
    if user_link:
        return True
    return False


async def list_courses(page: Page) -> list[dict]:
    """Navigate to UserPassport.action and scrape the course list.

    TAS DOM structure (from real HTML):
      table.table-hover.table-striped > tbody > tr
        td > a.center-block[href="...UserPassport!one.action?pid=XXXX"]  → title
        td > div.progress > div.progress-bar                            → completion %
        td > button.savebtn                                              → action

    Returns a list of dicts: {title, completion_percent, url}
    """
    settings = get_settings()

    # Navigate to the passport / course list page
    passport_url = settings.tas_base_url.rstrip("/")
    if not passport_url.endswith("UserPassport.action"):
        passport_url = passport_url.rstrip("/") + "/UserPassport.action"

    await page.goto(passport_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=15_000)

    current_url = page.url
    logger.info("Scanning courses at: %s", current_url)

    # Check if we're actually logged in
    if not await _is_logged_in(page):
        logger.warning("Not logged in — no authenticated elements found.")
        raise RuntimeError(
            "你尚未登入 TAS，或登入已過期。\n"
            "請在瀏覽器中手動登入 TAS，完成後再點擊 Refresh。"
        )

    # If we landed on the mainpage (Index.action) instead of the course list,
    # click the "我的護照" link to navigate there.
    course_table = await page.query_selector("table.table-hover.table-striped")
    if not course_table:
        logger.info("Course table not found — trying to click '我的護照' link...")
        passport_link = await page.query_selector(
            "a[href*='UserPassport.action']"
        )
        if passport_link:
            await passport_link.click()
            await page.wait_for_load_state("networkidle", timeout=15_000)
            logger.info("Navigated to course list via click: %s", page.url)
        else:
            logger.warning("Could not find '我的護照' link on the page.")

    courses: list[dict] = []

    # Real selector: table.table-hover.table-striped tbody tr
    rows = await page.query_selector_all(
        "table.table-hover.table-striped tbody tr"
    )

    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 2:
            continue

        # Title: first a.center-block in the row
        link_el = await row.query_selector("a.center-block")
        if not link_el:
            continue

        href = await link_el.get_attribute("href") or ""
        title_text = (await link_el.inner_text()).strip()

        # Completion: read the progress-bar text (e.g. "92%")
        progress_el = await row.query_selector(".progress-bar")
        completion = 0.0
        if progress_el:
            completion = await _read_progress_bar(progress_el)

        courses.append({
            "title": title_text,
            "completion_percent": completion,
            "url": _resolve_url(href),
        })

    logger.info("Found %d courses", len(courses))
    return courses


async def list_forms(page: Page, course_url: str) -> list[FormSummary]:
    """Navigate into a course page and list its forms.

    TAS DOM structure for the form list (from real HTML):
      hgroup > h3 > a (last one) → course title breadcrumb
      table.table-responsive > tbody > tr
        td > a.center-block[href="...UserPassform.action?pid=...&pcid=...&sfid=..."]  → form title + url
        td > div.progress > div.progress-bar                                          → completion %
    """
    await page.goto(course_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=15_000)

    # Course title from breadcrumb: hgroup > h3 contains links, last one is course name
    course_title = "Unknown Course"
    breadcrumb_links = await page.query_selector_all("hgroup h3 a")
    if breadcrumb_links:
        last_link = breadcrumb_links[-1]
        course_title = (await last_link.inner_text()).strip()

    forms: list[FormSummary] = []

    # Real selector: table.table-responsive tbody tr
    rows = await page.query_selector_all("table.table-responsive tbody tr")

    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 2:
            continue

        # Form title from a.center-block (shared across all instances)
        link_el = await row.query_selector("a.center-block")
        if not link_el:
            continue

        form_title = (await link_el.inner_text()).strip()
        if not form_title:
            continue

        # Check for multi-instance blocks (div.multifrm in the 3rd td)
        multifrm_blocks = await row.query_selector_all("div.multifrm")

        if multifrm_blocks:
            # Multi-instance form: one FormSummary per opened instance.
            # Row-level progress bar (in its own <td>) shows the overall completion
            # and is used as a fallback when the per-instance block has no own indicator.
            row_prog_el = await row.query_selector(".progress-bar")
            row_completion = 0.0
            if row_prog_el:
                row_completion = await _read_progress_bar(row_prog_el)

            for block in multifrm_blocks:
                instance_link = await block.query_selector("a[href*='UserPassform']")
                if not instance_link:
                    continue
                instance_href = await instance_link.get_attribute("href") or ""
                if not instance_href:
                    continue
                # sfid=0 = uncreated instance; include it so auto-fill can create and fill it
                instance_url = _resolve_url(instance_href)

                # Per-instance completion: prefer .multiprogress inside the block,
                # then .progress-bar inside the block, else fall back to row-level bar.
                prog_el = await block.query_selector(".multiprogress")
                if not prog_el:
                    prog_el = await block.query_selector(".progress-bar")
                if prog_el:
                    completion = await _read_progress_bar(prog_el)
                else:
                    completion = row_completion

                forms.append(FormSummary(
                    course_title=course_title,
                    form_title=form_title,
                    form_url=instance_url,
                    completion_percent=completion,
                ))
        else:
            # Single-instance form: use the center-block URL
            href = await link_el.get_attribute("href") or ""
            if not href:
                continue

            progress_el = await row.query_selector(".progress-bar")
            completion = 0.0
            if progress_el:
                completion = await _read_progress_bar(progress_el)

            forms.append(FormSummary(
                course_title=course_title,
                form_title=form_title,
                form_url=_resolve_url(href),
                completion_percent=completion,
            ))

    logger.info("Found %d forms in course '%s'", len(forms), course_title)
    return forms


async def open_form(page: Page, form_url: str) -> None:
    """Navigate to a specific form page."""
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=15_000)
    logger.info("Opened form: %s", form_url)


async def click_save(page: Page) -> bool:
    """Click the save button on the current form.

    Returns True if a save button was found and clicked, False otherwise.
    Respects DRY_RUN and safety checks.
    """
    from .safety import is_safe_to_click

    settings = get_settings()

    # Try common save button selectors (TAS uses Bootstrap btn classes)
    save_selectors = [
        "button.savebtn",
        "button:has-text('儲存')",
        "input[type='submit'][value*='儲存']",
        "button:has-text('存檔')",
        "button:has-text('Save')",
        "input[type='button'][value*='儲存']",
        # Evaluation forms (CbD, DOPS, 教學門診) use a yellow tmpSaveRuler button
        "button[onclick*='tmpSaveRuler']",
        "button[onclick*='tmpSaveRulerGroup']",
    ]

    for selector in save_selectors:
        btn = await page.query_selector(selector)
        if btn and await is_safe_to_click(btn):
            if settings.dry_run:
                logger.info("[DRY RUN] Would click save button: %s", selector)
                return True
            await btn.click()
            logger.info("Clicked save button: %s", selector)
            # Wait for save to process
            await page.wait_for_load_state("networkidle", timeout=10_000)
            return True

    logger.warning("No save button found on the current page.")
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_percent(text: str) -> float:
    """Extract a percentage value from text like '80%' or '完成度 80%'."""
    import re
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) if match else 0.0


async def _read_progress_bar(el) -> float:
    """Read a Bootstrap progress-bar value.

    TAS hardcodes aria-valuenow="10" on every bar regardless of actual value,
    so that attribute is ignored.  The real value appears in inner text ("91%")
    and in style="...width: 91%".
    """
    # 1. Inner text — present as "0%", "91%", etc.
    text = (await el.inner_text()).strip()
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if m:
        return float(m.group(1))  # return even when 0 — 0% is a valid answer

    # 2. style="min-width:2em; width: 91%"
    style = await el.get_attribute("style") or ""
    return _extract_percent(style)


def _resolve_url(href: str) -> str:
    """Turn a relative href into an absolute TAS URL."""
    if not href:
        return ""
    if href.startswith("http"):
        return href
    settings = get_settings()
    base = settings.tas_base_url.rstrip("/")
    if href.startswith("/"):
        # Absolute path — extract origin from base
        from urllib.parse import urlparse
        parsed = urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return f"{base}/{href}"

