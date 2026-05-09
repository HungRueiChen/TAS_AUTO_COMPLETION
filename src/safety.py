"""Safety module — prevents accidental notification to instructors."""

from __future__ import annotations

import logging
from playwright.async_api import Page, ElementHandle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deny-list of dangerous button texts (spec §7.3)
# ---------------------------------------------------------------------------

DENY_TEXTS: list[str] = [
    "通知臨床教師",
    "通知導師",
    "送出通知",
    "通知教師",
    "Notify",
    "Submit to Teacher",
    "送交",
    "繳交",
]


async def register_blocker(page: Page) -> None:
    """Register a dialog handler and intercept dangerous clicks.

    Adds a JS mutation observer that disables known dangerous buttons
    as they appear in the DOM — matched by text content OR onclick attribute.
    """
    # Inject a JS snippet that continuously watches for dangerous buttons
    deny_json = ",".join(f'"{t}"' for t in DENY_TEXTS)
    # Onclick substrings that are also blocked regardless of button text
    deny_onclick = ["putTeacher", "putTeacher2"]
    deny_onclick_json = ",".join(f'"{t}"' for t in deny_onclick)
    script = f"""
    (() => {{
        const denyTexts = [{deny_json}];
        const denyOnclicks = [{deny_onclick_json}];
        function checkAndDisable() {{
            document.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn').forEach(el => {{
                const text = (el.textContent || el.value || '').trim();
                const onclick = (el.getAttribute('onclick') || '').trim();
                const blocked =
                    denyTexts.some(d => text.includes(d)) ||
                    denyOnclicks.some(d => onclick.includes(d));
                if (blocked) {{
                    el.disabled = true;
                    el.style.opacity = '0.3';
                    el.style.pointerEvents = 'none';
                    el.title = '[TAS Assistant] Blocked — notify teacher action';
                    console.warn('[TAS Safety] Blocked button:', text || onclick);
                }}
            }});
        }}
        // Run immediately and watch for mutations
        checkAndDisable();
        const observer = new MutationObserver(checkAndDisable);
        observer.observe(document.body, {{ childList: true, subtree: true }});
    }})();
    """
    try:
        await page.evaluate(script)
        logger.info("Safety blocker registered on page: %s", page.url[:80])
    except Exception as exc:
        logger.warning("Could not register safety blocker: %s", exc)


async def is_safe_to_click(element: ElementHandle) -> bool:
    """Check whether an element is safe to click (not on the deny-list)."""
    try:
        text = await element.evaluate(
            "el => (el.textContent || el.value || '').trim()"
        )
    except Exception:
        return True  # If we can't read it, allow but log

    for deny in DENY_TEXTS:
        if deny in text:
            logger.warning(
                "BLOCKED dangerous click on element with text: '%s'", text[:100]
            )
            return False

    return True
