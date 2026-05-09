"""Extractor module — reads TAS form DOM to detect question sections and cells.

TAS form structure (from real HTML analysis):

1. **Dynamic tables** (sections like 一(一), 一(二), 二):
   - Each section: `<div><h4>title</h4></div>` + `<div class="table-responsive"><table ...>`
   - Header row: `thead > tr > th.titlehead` (first th has ⊕ add button)
   - Template row: `tbody > tr.tblstd.hidden` (seq=0, cloned by newTblrow())
   - Data rows: `tbody > tr.tblstd:not(.hidden)` (seq=1+)
   - Cells use onclick calls:
     - `inConetentDate2(this, 'date', qid, cid)` → date picker
     - `inContent2(this, 'checkbox', qid, cid)` → checkbox dialog
     - `inContent2(this, 'text', qid, cid)` → text input dialog
     - `inContent2(this, 'textarea', qid, cid)` → textarea dialog

2. **Fixed tables** (section 三 — case analysis):
   - No add-row button; first column is a fixed label (e.g. '醫學倫理與法律')
   - Cells use `inContent()` / `inConetentDate()` (note: different from the "2" variants)
   - Same dialog mechanism but with additional qtid parameter

3. **Standalone widgets** (radios, textareas, etc. outside tables).
"""

from __future__ import annotations

import logging
import re

from playwright.async_api import Page, ElementHandle

from .models import (
    DomHints,
    QuestionBlock,
    TasDynamicSection,
    TasFixedRow,
    WidgetType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_form_structure(page: Page) -> dict:
    """High-level extraction: return a dict describing the entire form.

    Returns:
        {
            "form_title": str,
            "sections": [TasDynamicSection, ...],
            "fixed_sections": [{"title": str, "rows": [TasFixedRow, ...], "section_index": int}, ...],
            "questions": [QuestionBlock, ...],  # standalone non-table questions
        }
    """
    form_title = ""
    title_el = await page.query_selector("div.intro h3")
    if title_el:
        form_title = (await title_el.inner_text()).strip()

    sections = await _extract_dynamic_sections(page)
    fixed = await _extract_fixed_sections(page)
    standalone = await _extract_standalone_questions(page)

    logger.info(
        "Extracted form: title='%s', %d dynamic sections, %d fixed sections, %d standalone Qs",
        form_title[:60], len(sections), len(fixed), len(standalone),
    )
    return {
        "form_title": form_title,
        "sections": sections,
        "fixed_sections": fixed,
        "questions": standalone,
    }


async def extract_questions(page: Page) -> list[QuestionBlock]:
    """Backward-compatible entry point: returns a flat list of QuestionBlock.

    For TAS dynamic tables, each column in each section becomes one QuestionBlock.
    """
    structure = await extract_form_structure(page)
    questions: list[QuestionBlock] = []

    for sec in structure["sections"]:
        for col_idx, (header, col_type) in enumerate(
            zip(sec.column_headers, sec.column_types)
        ):
            wt = _col_type_to_widget(col_type)
            options = []
            if wt == WidgetType.CHECKBOX:
                # Extract checkbox options from the template row
                options = await _extract_checkbox_options_from_section(page, sec.section_index, col_idx)

            questions.append(QuestionBlock(
                question_id=f"sec{sec.section_index}_col{col_idx}",
                prompt=f"{sec.section_title} → {header}",
                widget_type=wt,
                options=options,
                dom_hints=DomHints(
                    section_index=sec.section_index,
                    column_index=col_idx,
                    label_text=header,
                ),
            ))

    for fs in structure["fixed_sections"]:
        for row in fs["rows"]:
            for cell in row.cells:
                questions.append(QuestionBlock(
                    question_id=f"fixed_sec{row.section_index}_row{row.row_index}_{cell.get('col_idx', 0)}",
                    prompt=f"{fs['title']} → {row.row_label} → {cell.get('header', '')}",
                    widget_type=_col_type_to_widget(cell.get("type", "text")),
                    dom_hints=DomHints(
                        section_index=row.section_index,
                        column_index=cell.get("col_idx", 0),
                        label_text=cell.get("header", ""),
                    ),
                ))

    questions.extend(structure["questions"])
    logger.info("Total QuestionBlocks: %d", len(questions))
    return questions


# ---------------------------------------------------------------------------
# Dynamic table sections (with add-row button)
# ---------------------------------------------------------------------------


async def _extract_dynamic_sections(page: Page) -> list[TasDynamicSection]:
    """Find all table sections that have a newTblrow (⊕) add-row button."""
    sections: list[TasDynamicSection] = []

    # All table-responsive wrappers on the page
    wrappers = await page.query_selector_all("div.table-responsive")

    for sec_idx, wrapper in enumerate(wrappers):
        table = await wrapper.query_selector("table")
        if not table:
            continue

        # Check for add-row button (newTblrow)
        add_btn = await table.query_selector("a[onclick*='newTblrow']")
        is_dynamic = add_btn is not None

        # Section title: look for preceding h4
        title = await wrapper.evaluate("""el => {
            let prev = el.previousElementSibling;
            while (prev) {
                let h4 = prev.querySelector('h4');
                if (h4) return h4.textContent.trim();
                if (prev.tagName === 'H4') return prev.textContent.trim();
                prev = prev.previousElementSibling;
            }
            return '';
        }""")

        # Column headers from thead > th.titlehead (skip first th which is the ⊕ button)
        headers = []
        header_els = await table.query_selector_all("thead th.titlehead")
        for th in header_els:
            text = (await th.inner_text()).strip()
            if text and not text.startswith("新增"):  # Skip the ⊕ column
                # If it only contains the plus icon, skip
                has_plus = await th.query_selector("a[onclick*='newTblrow']")
                if not has_plus:
                    headers.append(text)

        # Detect column types from onclick handlers in the template row
        col_types = await _detect_column_types(table)

        # Count existing data rows: use JS to check actual visibility and presence
        # of a delete button (template row is hidden; cloned rows may or may not
        # retain the .tblstd class but always have a delTblrow delete button).
        existing = await table.evaluate("""table => {
            let count = 0;
            for (const row of table.querySelectorAll('tbody tr')) {
                const isHidden = row.classList.contains('hidden')
                    || row.style.display === 'none'
                    || getComputedStyle(row).display === 'none';
                const hasDelete = row.querySelector('a[onclick*="delTblrow"]');
                if (!isHidden && hasDelete) count++;
            }
            return count;
        }""")

        if is_dynamic:
            sections.append(TasDynamicSection(
                section_title=title or f"Section {sec_idx}",
                section_index=sec_idx,
                column_headers=headers,
                column_types=col_types,
                has_add_button=True,
                existing_rows=existing,
            ))

    return sections


async def _detect_column_types(table: ElementHandle) -> list[str]:
    """Detect widget types for each column from onclick attrs in the template row."""
    template_row = await table.query_selector("tbody tr.tblstd")
    if not template_row:
        return []

    cells = await template_row.query_selector_all("td")
    types: list[str] = []

    for cell in cells:
        # Skip the delete/seq column (first td with glyphicon-remove)
        has_remove = await cell.query_selector("a[onclick*='delTblrow']")
        if has_remove:
            continue

        # Check for date handler
        date_link = await cell.query_selector("a[onclick*='inConetentDate']")
        if date_link:
            types.append("date")
            continue

        # Check for content handler — extract type from onclick
        content_link = await cell.query_selector("a[onclick*='inContent']")
        if content_link:
            onclick = await content_link.get_attribute("onclick") or ""
            # Extract type: inContent2(this, 'checkbox', ...) or inContent(this, 'text', ...)
            m = re.search(r"inContent2?\(this,\s*'(\w+)'", onclick)
            if m:
                types.append(m.group(1))
                continue

        # Check for direct inputs
        textarea = await cell.query_selector("textarea")
        if textarea:
            types.append("textarea")
            continue

        text_input = await cell.query_selector("input[type='text']")
        if text_input:
            types.append("text")
            continue

        radio = await cell.query_selector("input[type='radio']")
        if radio:
            types.append("radio")
            continue

        types.append("unknown")

    return types


async def _extract_checkbox_options_from_section(
    page: Page, section_index: int, col_index: int
) -> list[str]:
    """Extract checkbox option labels from the template row's .hcontent span."""
    wrappers = await page.query_selector_all("div.table-responsive")
    if section_index >= len(wrappers):
        return []

    wrapper = wrappers[section_index]
    table = await wrapper.query_selector("table")
    if not table:
        return []

    template = await table.query_selector("tbody tr.tblstd")
    if not template:
        return []

    # Find the relevant td (skip the first delete/seq td)
    cells = await template.query_selector_all("td")
    data_cells = []
    for cell in cells:
        has_remove = await cell.query_selector("a[onclick*='delTblrow']")
        if not has_remove:
            data_cells.append(cell)

    if col_index >= len(data_cells):
        return []

    target_cell = data_cells[col_index]
    hcontent = await target_cell.query_selector("span.hcontent")
    if not hcontent:
        return []

    labels = await hcontent.query_selector_all("div.diagctx label")
    options = []
    for lbl in labels:
        text = (await lbl.inner_text()).strip()
        if text:
            options.append(text)
    return options


# ---------------------------------------------------------------------------
# Fixed table sections (no add-row, pre-defined rows)
# ---------------------------------------------------------------------------


async def _extract_fixed_sections(page: Page) -> list[dict]:
    """Find fixed table sections (like section 三 with predefined rows)."""
    fixed: list[dict] = []
    wrappers = await page.query_selector_all("div.table-responsive")

    for sec_idx, wrapper in enumerate(wrappers):
        table = await wrapper.query_selector("table")
        if not table:
            continue

        # Fixed sections do NOT have a newTblrow button
        add_btn = await table.query_selector("a[onclick*='newTblrow']")
        if add_btn:
            continue  # This is a dynamic section

        # But they do have rows with inContent/inConetentDate links
        content_links = await table.query_selector_all("a[onclick*='inContent']")
        if not content_links:
            continue

        title = await wrapper.evaluate("""el => {
            let prev = el.previousElementSibling;
            while (prev) {
                let h4 = prev.querySelector('h4');
                if (h4) return h4.textContent.trim();
                if (prev.tagName === 'H4') return prev.textContent.trim();
                prev = prev.previousElementSibling;
            }
            return '';
        }""")

        # Get column headers
        headers = []
        header_els = await table.query_selector_all("thead th.titlehead")
        for th in header_els:
            text = (await th.inner_text()).strip()
            if text:
                headers.append(text)

        # Parse each body row
        rows_data: list[TasFixedRow] = []
        body_rows = await table.query_selector_all("tbody tr")

        for row_idx, row in enumerate(body_rows):
            cells = await row.query_selector_all("td")
            if not cells:
                continue

            # First cell is usually the label
            label_cell = cells[0]
            label = (await label_cell.inner_text()).strip()

            cell_data = []
            for ci, cell in enumerate(cells[1:], start=1):
                ctype = "unknown"
                date_link = await cell.query_selector("a[onclick*='inConetentDate']")
                if date_link:
                    ctype = "date"
                else:
                    content_link = await cell.query_selector("a[onclick*='inContent']")
                    if content_link:
                        onclick = await content_link.get_attribute("onclick") or ""
                        m = re.search(r"inContent2?\(this,\s*'(\w+)'", onclick)
                        if m:
                            ctype = m.group(1)

                header = headers[ci] if ci < len(headers) else f"Column {ci}"
                cell_data.append({"col_idx": ci, "type": ctype, "header": header})

            rows_data.append(TasFixedRow(
                row_label=label,
                row_index=row_idx,
                section_index=sec_idx,
                cells=cell_data,
            ))

        if rows_data:
            fixed.append({
                "title": title or f"Fixed Section {sec_idx}",
                "section_index": sec_idx,
                "rows": rows_data,
            })

    return fixed


# ---------------------------------------------------------------------------
# Standalone / form-group questions (outside tables)
# ---------------------------------------------------------------------------


async def _extract_standalone_questions(page: Page) -> list[QuestionBlock]:
    """Extract form-group and standalone form widgets.

    TAS uses two form layouts:
    1. Dynamic/fixed tables (handled above)
    2. Formatted forms with div.form-group:
       - label.col-sm-2.control-label > h4 = question label
       - div.col-sm-9 = answer area with various widgets

    We also extract any leftover textareas/inputs not inside tables or form-groups.
    """
    questions: list[QuestionBlock] = []

    form = await page.query_selector("form.form-horizontal, form.qa-list")
    if not form:
        return questions

    # Strategy 1: div.form-group based questions
    form_groups = await form.query_selector_all("div.form-group")
    for idx, fg in enumerate(form_groups):
        q = await _parse_form_group(fg, idx)
        if q:
            questions.append(q)

    # Strategy 2: Direct textareas not in tables or form-groups
    if not form_groups:
        textareas = await form.query_selector_all("textarea")
        for idx, ta in enumerate(textareas):
            is_in_table = await ta.evaluate("el => !!el.closest('table')")
            is_in_fg = await ta.evaluate("el => !!el.closest('.form-group')")
            if is_in_table or is_in_fg:
                continue
            label = await ta.evaluate("""el => {
                let prev = el.previousElementSibling;
                while (prev) {
                    if (prev.tagName === 'LABEL' || prev.tagName === 'H4')
                        return prev.textContent.trim();
                    prev = prev.previousElementSibling;
                }
                return '';
            }""")
            questions.append(QuestionBlock(
                question_id=f"sa_textarea_{idx}",
                prompt=label or f"Textarea {idx}",
                widget_type=WidgetType.TEXTAREA,
                dom_hints=DomHints(css_selector=f"#sa_textarea_{idx}"),
            ))

    return questions


async def _parse_form_group(fg: ElementHandle, idx: int) -> QuestionBlock | None:
    """Parse a single div.form-group into a QuestionBlock."""

    # Get the label text
    label_el = await fg.query_selector("label.control-label h4, label.control-label")
    if not label_el:
        return None

    label_text = (await label_el.inner_text()).strip()
    # Remove required marker symbols
    label_text = label_text.replace("＊", "").strip()

    if not label_text:
        return None

    # Skip teacher-only fields (marked with 師 badge)
    teacher_badge = await fg.query_selector("span.label.label-success")
    if teacher_badge:
        badge_text = (await teacher_badge.inner_text()).strip()
        if "師" in badge_text:
            logger.debug("Skipping teacher-only field: %s", label_text)
            return None

    # Find the answer container
    container = await fg.query_selector("div.col-sm-9, div.col-sm-10, div[class*='col-sm']")
    if not container:
        return None

    # Detect widget type from the container content
    widget_type, options, css_sel, el_id = await _detect_form_group_widget(container)

    if widget_type == WidgetType.UNKNOWN:
        return None

    # Skip "toteacher" checkboxes (delegating to teacher)
    toteacher = await container.query_selector("input.toteacher")
    if toteacher:
        logger.debug("Skipping toteacher checkbox: %s", label_text)
        return None

    # Check for required marker
    required_marker = await fg.query_selector("span.text-danger")
    is_required = required_marker is not None

    return QuestionBlock(
        question_id=el_id or f"fg_{idx}",
        prompt=label_text,
        required=is_required,
        widget_type=widget_type,
        options=options,
        dom_hints=DomHints(
            css_selector=css_sel,
            label_text=label_text,
        ),
    )


async def _detect_form_group_widget(
    container: ElementHandle,
) -> tuple[WidgetType, list[str], str, str]:
    """Detect widget type inside a form-group's col-sm-9 container.

    Returns (widget_type, options, css_selector, element_id).
    """
    # 1. Date picker input (showDatePicker onclick)
    date_input = await container.query_selector("input[onclick*='showDatePicker']")
    if date_input:
        el_id = await date_input.get_attribute("id") or ""
        return WidgetType.DATE, [], f"#{el_id}" if el_id else "input[onclick*='showDatePicker']", el_id

    # 2. Select dropdown (updCombo onchange)
    select_el = await container.query_selector("select.form-control")
    if select_el:
        el_id = await select_el.get_attribute("id") or ""
        options = []
        option_els = await select_el.query_selector_all("option")
        for opt in option_els:
            text = (await opt.inner_text()).strip()
            value = await opt.get_attribute("value") or ""
            if value and text and "---" not in text:
                options.append(text)
        return WidgetType.SELECT, options, f"select.form-control", el_id

    # 3. Textarea (updAtext2 onblur)
    textarea = await container.query_selector("textarea")
    if textarea:
        el_id = await textarea.get_attribute("id") or ""
        return WidgetType.TEXTAREA, [], f"#{el_id}" if el_id else "textarea", el_id

    # 4. Radio buttons
    radios = await container.query_selector_all("input[type='radio']")
    if radios:
        options = []
        for radio in radios:
            label_el = await radio.evaluate_handle("el => el.closest('label')")
            if label_el:
                elem = label_el.as_element()
                if elem:
                    text = (await elem.inner_text()).strip()
                    if text:
                        options.append(text)
        el_name = await radios[0].get_attribute("name") or ""
        return WidgetType.RADIO, options, f"input[name='{el_name}']", el_name

    # 5. Checkboxes (but not toteacher)
    checkboxes = await container.query_selector_all("input[type='checkbox']:not(.toteacher)")
    if checkboxes:
        options = []
        for cb in checkboxes:
            label_el = await cb.evaluate_handle("el => el.closest('label')")
            if label_el:
                elem = label_el.as_element()
                if elem:
                    text = (await elem.inner_text()).strip()
                    if text:
                        options.append(text)
        return WidgetType.CHECKBOX, options, "input[type='checkbox']", ""

    # 6. Text input (updAtext onblur or regular text)
    text_input = await container.query_selector("input[type='text'], input.form-control:not([type])")
    if text_input:
        el_id = await text_input.get_attribute("id") or ""
        return WidgetType.TEXT, [], f"#{el_id}" if el_id else "input[type='text']", el_id

    return WidgetType.UNKNOWN, [], "", ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col_type_to_widget(col_type: str) -> WidgetType:
    """Map TAS column type strings to WidgetType enum."""
    mapping = {
        "date": WidgetType.DATE,
        "text": WidgetType.TEXT,
        "textarea": WidgetType.TEXTAREA,
        "checkbox": WidgetType.CHECKBOX,
        "teacheckbox": WidgetType.CHECKBOX,
        "radio": WidgetType.RADIO,
        "tearadio": WidgetType.RADIO,
        "teatext": WidgetType.TEXT,
        "teatextarea": WidgetType.TEXTAREA,
        "teadate": WidgetType.DATE,
    }
    return mapping.get(col_type, WidgetType.UNKNOWN)

