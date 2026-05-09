"""Diagnostics module — auto-captures DOM structure during fill runs.

After navigating to a form, `run_diagnostics` is called to:
1. Extract all onclick patterns from interactive elements.
2. Cross-reference them against known-handled patterns.
3. Compare against what the extractor found (section/column coverage).
4. Generate human-readable suggestions for code improvements.
5. Write a JSON report to diagnostics/<timestamp>_<slug>.json.

No manual page downloading needed — issues surface in logs as [DIAG] warnings
and in the report file after each fill run.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known-handled onclick substrings
# If any of these appear in an onclick value, the element is considered handled.
# ---------------------------------------------------------------------------

_HANDLED_PATTERNS: list[str] = [
    "newTblrow",
    "delTblrow",
    "inContent2",           # dynamic table content dialog (must check before inContent)
    "inConetentDate2",      # dynamic table date picker
    "inContent(",           # fixed-table content dialog
    "inConetentDate(",      # fixed-table date picker
    "showDatePicker",
    "updCombo",
    "updAtext2",
    "updAtext",
    "updAdate2",
    "updAdate",
    "updAradio",            # standalone radio save (CbD, DOPS, 教學門診 forms)
    "tmpSaveRuler",         # yellow Save button on evaluation forms
    "tmpSaveRulerGroup",    # group variant of above
    "putTeacher",           # teacher-notification button — handled by safety blocker
    "putTeacher2",          # variant 2
]

# Patterns that are safe to ignore (navigation/UI boilerplate or teacher-management UI)
_IGNORABLE_PATTERNS: list[str] = [
    "javascript:void",
    "javascript:;",
    "return false",
    "$(this)",
    "scrollTo",
    "toggle",
    "collapse",
    "modal",
    "tooltip",
    "popover",
    "preventDefault",
    "chkallteacher",        # teacher selection checkbox — not filled by assistant
]

# Column types the extractor knows how to fill
_HANDLED_COLUMN_TYPES: set[str] = {
    "date", "text", "textarea", "checkbox", "radio",
    "teacheckbox", "tearadio", "teatext", "teatextarea", "teadate",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_diagnostics(
    page: Page,
    structure: dict,
    form_url: str,
    form_title: str,
) -> dict:
    """Capture DOM state and produce a structured diagnostic report.

    Args:
        page: Playwright page (must be on the form URL already).
        structure: Output of extract_form_structure().
        form_url: URL of the form.
        form_title: Human-readable form title.

    Returns:
        Report dict (also written to disk by write_report).
    """
    onclick_patterns = await _extract_onclick_patterns(page)
    section_coverage, suggestions = _analyze_coverage(structure, onclick_patterns)
    unhandled = [p["pattern"] for p in onclick_patterns if not p["handled"]]

    report = {
        "form_title": form_title,
        "form_url": form_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "onclick_patterns": onclick_patterns,
        "section_coverage": section_coverage,
        "suggestions": suggestions,
        "unhandled_patterns": unhandled,
        "summary": {
            "total_onclick": len(onclick_patterns),
            "unhandled_onclick": len(unhandled),
            "dynamic_sections": len(structure.get("sections", [])),
            "fixed_sections": len(structure.get("fixed_sections", [])),
            "standalone_questions": len(structure.get("questions", [])),
            "suggestion_count": len(suggestions),
        },
    }

    return report


def write_report(report: dict, form_title: str) -> Path:
    """Write report to diagnostics/<timestamp>_<slug>.json and return the path."""
    diag_dir = Path("diagnostics")
    diag_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(form_title)[:40]
    path = diag_dir / f"{ts}_{slug}.json"

    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Diagnostic report written: %s", path)
    return path


# ---------------------------------------------------------------------------
# DOM onclick extraction
# ---------------------------------------------------------------------------


async def _extract_onclick_patterns(page: Page) -> list[dict]:
    """Use JS to collect all unique onclick values from interactive elements.

    Returns a list of dicts: {pattern, count, handled, example_selector}.
    """
    raw: list[dict] = await page.evaluate("""() => {
        const counts = {};
        const examples = {};

        document.querySelectorAll('[onclick]').forEach(el => {
            const raw = (el.getAttribute('onclick') || '').trim();
            if (!raw) return;

            // Normalise: collapse whitespace, truncate at 120 chars for grouping
            const key = raw.replace(/\\s+/g, ' ').slice(0, 120);
            counts[key] = (counts[key] || 0) + 1;
            if (!examples[key]) {
                // Build a short selector description
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : '';
                const cls = el.className ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '';
                examples[key] = tag + id + cls;
            }
        });

        return Object.entries(counts).map(([pattern, count]) => ({
            pattern, count, example_selector: examples[pattern]
        }));
    }""")

    results: list[dict] = []
    for item in raw:
        pattern: str = item["pattern"]
        handled = _is_handled(pattern)
        ignorable = _is_ignorable(pattern)
        results.append({
            "pattern": pattern,
            "count": item["count"],
            "handled": handled,
            "ignorable": ignorable,
            "example_selector": item.get("example_selector", ""),
        })

    # Sort: unhandled + non-ignorable first, then by count desc
    results.sort(key=lambda x: (x["handled"] or x["ignorable"], -x["count"]))
    return results


def _is_handled(pattern: str) -> bool:
    return any(h in pattern for h in _HANDLED_PATTERNS)


def _is_ignorable(pattern: str) -> bool:
    p = pattern.lower()
    return any(ig in p for ig in _IGNORABLE_PATTERNS)


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------


def _analyze_coverage(
    structure: dict,
    onclick_patterns: list[dict],
) -> tuple[list[dict], list[str]]:
    """Cross-reference extractor output with raw onclick patterns.

    Returns (section_coverage, suggestions).
    """
    suggestions: list[str] = []
    section_coverage: list[dict] = []

    # ---- Dynamic sections ----
    for sec in structure.get("sections", []):
        col_info = []
        for col_idx, (header, ctype) in enumerate(
            zip(sec.column_headers, sec.column_types)
        ):
            handled = ctype in _HANDLED_COLUMN_TYPES
            entry: dict = {
                "header": header,
                "detected_type": ctype,
                "handled": handled,
            }
            if not handled:
                raw_onclick = _find_onclick_for_type(ctype, onclick_patterns)
                entry["raw_onclick"] = raw_onclick
                suggestions.append(
                    f"Dynamic section {sec.section_index} ('{sec.section_title}') "
                    f"col {col_idx} ('{header}'): type='{ctype}' is unhandled. "
                    f"Raw onclick: '{raw_onclick}'. "
                    f"→ Add '{ctype}' to _col_type_to_widget() in extractor.py "
                    f"and a handler in handlers.py."
                )
            col_info.append(entry)

        section_coverage.append({
            "section_index": sec.section_index,
            "section_title": sec.section_title,
            "type": "dynamic",
            "column_count": len(sec.column_headers),
            "existing_rows": sec.existing_rows,
            "columns": col_info,
        })

    # ---- Fixed sections ----
    for fs in structure.get("fixed_sections", []):
        rows_info = []
        for row in fs.get("rows", []):
            cells_info = []
            for cell in row.cells:
                ctype = cell.get("type", "unknown")
                handled = ctype in _HANDLED_COLUMN_TYPES
                cell_entry: dict = {
                    "header": cell.get("header", ""),
                    "detected_type": ctype,
                    "handled": handled,
                }
                if not handled:
                    raw_onclick = _find_onclick_for_type(ctype, onclick_patterns)
                    cell_entry["raw_onclick"] = raw_onclick
                    suggestions.append(
                        f"Fixed section {row.section_index} ('{fs.get('title', '')}') "
                        f"row {row.row_index} ('{row.row_label}') "
                        f"col {cell.get('col_idx', '?')} ('{cell.get('header', '')}'): "
                        f"type='{ctype}' is unhandled. Raw onclick: '{raw_onclick}'."
                    )
                cells_info.append(cell_entry)
            rows_info.append({"row_label": row.row_label, "cells": cells_info})

        section_coverage.append({
            "section_index": fs.get("section_index", -1),
            "section_title": fs.get("title", ""),
            "type": "fixed",
            "rows": rows_info,
        })

    # ---- Unhandled onclick patterns (not ignorable) ----
    truly_unhandled = [
        p for p in onclick_patterns
        if not p["handled"] and not p["ignorable"]
    ]
    for p in truly_unhandled:
        # Check if we already made a suggestion for this onclick text
        already_covered = any(p["pattern"][:30] in s for s in suggestions)
        if not already_covered:
            suggestions.append(
                f"Unrecognized onclick ({p['count']}x): '{p['pattern'][:100]}' "
                f"on element '{p['example_selector']}'. "
                f"→ Check if this is a new TAS widget that needs a handler."
            )

    # ---- Standalone questions with unknown widget type ----
    for q in structure.get("questions", []):
        if q.widget_type.value == "unknown":
            suggestions.append(
                f"Standalone question '{q.prompt[:60]}' (id={q.question_id}) "
                f"has widget_type=unknown. "
                f"→ Inspect the form-group container and add a detector in "
                f"_detect_form_group_widget() in extractor.py."
            )

    return section_coverage, suggestions


def _find_onclick_for_type(ctype: str, patterns: list[dict]) -> str:
    """Find a raw onclick pattern that contains the type string."""
    for p in patterns:
        if ctype in p["pattern"]:
            return p["pattern"]
    return f"(no matching onclick found for type='{ctype}')"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a safe filename slug."""
    # Normalize unicode (e.g. full-width chars → ASCII where possible)
    text = unicodedata.normalize("NFKC", text)
    # Replace non-alphanumeric with underscores
    text = re.sub(r"[^\w\u4e00-\u9fff]", "_", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "form"
