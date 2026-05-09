"""Widget handlers — fill TAS form fields using TAS-specific interaction patterns.

TAS forms use a unique UI pattern where most inputs are NOT directly editable.
Instead, users click a "請輸入" link that triggers JavaScript functions:

- `newTblrow(this)` — clone the hidden template row to add a new data row
- `inConetentDate2(this, 'date', qid, cid)` — show jQuery datepicker
- `inContent2(this, 'checkbox', qid, cid)` — open dialog with checkboxes
- `inContent2(this, 'text', qid, cid)` — open dialog with text input
- `inContent(this, 'text', qid, qtid, qtcid)` — same but for fixed-row tables
- `inConetentDate(this, 'date', qid, qtid, qtcid)` — date picker for fixed rows

The dialog is `#diag-frm` (a jQuery UI dialog).  We need to:
1. Click the "請輸入" link to trigger the JS function
2. Wait for the dialog to open
3. Fill in / select values in the dialog
4. Click the "確定" (OK) button to save via AJAX

For dates, the datepicker opens inline — we set the hidden input's value
and trigger the onClose callback.
"""

from __future__ import annotations

import logging
from datetime import date

from playwright.async_api import Page, ElementHandle

from .config import get_settings
from .models import QuestionBlock, WidgetType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fill_question(page: Page, question: QuestionBlock, answer: str) -> bool:
    """Dispatch to the correct handler based on widget type.

    Returns True if the answer was applied, False otherwise.
    """
    settings = get_settings()
    handler = _HANDLERS.get(question.widget_type)

    if handler is None:
        logger.warning(
            "No handler for widget_type=%s (question '%s')",
            question.widget_type,
            question.prompt[:60],
        )
        return False

    if settings.dry_run:
        logger.info(
            "[DRY RUN] Would fill question '%s' (type=%s) with answer='%s'",
            question.prompt[:60],
            question.widget_type,
            answer[:80],
        )
        return True

    try:
        result = await handler(page, question, answer)
        if result is False:
            logger.warning(
                "Handler reported failure for '%s' (type=%s)",
                question.prompt[:60],
                question.widget_type,
            )
            return False
        logger.info(
            "Filled question '%s' (type=%s)",
            question.prompt[:60],
            question.widget_type,
        )
        return True
    except Exception as exc:
        logger.error(
            "Failed to fill question '%s': %s",
            question.prompt[:60],
            exc,
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Dynamic table: add row
# ---------------------------------------------------------------------------


async def add_dynamic_row(page: Page, section_index: int) -> bool:
    """Click the ⊕ button to add a new row to a dynamic table section.

    Args:
        section_index: 0-based index of the .table-responsive wrapper on the page.

    Returns True if a row was added.
    """
    wrappers = await page.query_selector_all("div.table-responsive")
    if section_index >= len(wrappers):
        logger.warning("Section index %d out of range (%d sections)", section_index, len(wrappers))
        return False

    wrapper = wrappers[section_index]
    add_btn = await wrapper.query_selector("a[onclick*='newTblrow']")
    if not add_btn:
        logger.warning("No newTblrow button found in section %d", section_index)
        return False

    await add_btn.click()
    await page.wait_for_timeout(500)
    logger.info("Added new row to section %d", section_index)
    return True


# ---------------------------------------------------------------------------
# Locate cell in a dynamic table row
# ---------------------------------------------------------------------------


async def _get_data_cell(
    page: Page, section_index: int, row_index: int, col_index: int
) -> ElementHandle | None:
    """Get a specific data cell from a dynamic table.

    Args:
        section_index: which .table-responsive wrapper
        row_index: 0-based among visible (non-hidden) data rows
        col_index: 0-based among data columns (skipping the delete/seq column)
    """
    wrappers = await page.query_selector_all("div.table-responsive")
    if section_index >= len(wrappers):
        return None

    wrapper = wrappers[section_index]
    table = await wrapper.query_selector("table")
    if not table:
        return None

    # Get visible data rows: use :has() to require a delete button (excludes template/header
    # rows) and :not(.hidden) to exclude the hidden template row.
    rows = await table.query_selector_all(
        "tbody tr:not(.hidden):has(a[onclick*='delTblrow'])"
    )
    if row_index >= len(rows):
        logger.warning("Row %d out of range (%d visible rows)", row_index, len(rows))
        return None

    row = rows[row_index]
    cells = await row.query_selector_all("td")

    # Skip the first td (delete icon + seq)
    data_cells = []
    for cell in cells:
        has_remove = await cell.query_selector("a[onclick*='delTblrow']")
        if not has_remove:
            data_cells.append(cell)

    if col_index >= len(data_cells):
        logger.warning("Column %d out of range (%d data columns)", col_index, len(data_cells))
        return None

    return data_cells[col_index]


# ---------------------------------------------------------------------------
# Date handler (uses jQuery datepicker)
# ---------------------------------------------------------------------------


async def fill_date_cell(
    page: Page, section_index: int, row_index: int, col_index: int,
    date_str: str
) -> bool:
    """Fill a date cell in a dynamic table row.

    date_str should be in YYYY/MM/DD format (e.g. '2025/11/27').

    TAS mechanism:
    1. Click the "請輸入" link → triggers inConetentDate2()
    2. This shows a jQuery UI datepicker on the hidden <input> sibling
    3. We set the date via JavaScript and trigger the onClose callback
    """
    cell = await _get_data_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    # Find the date trigger link
    date_link = await cell.query_selector("a[onclick*='inConetentDate']")
    if not date_link:
        logger.warning("No date link found in cell sec=%d row=%d col=%d",
                        section_index, row_index, col_index)
        return False

    # Click to activate the datepicker
    await date_link.click()
    await page.wait_for_timeout(500)

    # Find the hidden input that now has the datepicker attached
    date_input = await cell.query_selector("input[type='text']")
    if not date_input:
        logger.warning("No date input found in cell")
        return False

    # Set the date via jQuery datepicker API and trigger close.
    # Guard with data('datepicker') check — if the datepicker is not attached
    # (e.g. on an already-filled row that wasn't re-initialized), calling
    # datepicker('setDate') throws a TypeError that aborts the whole form.
    await page.evaluate("""([inputEl, dateStr]) => {
        const $input = $(inputEl);
        if ($input.data('datepicker')) {
            $input.datepicker('setDate', dateStr);
            $input.datepicker('hide');
        } else {
            inputEl.value = dateStr;
            inputEl.dispatchEvent(new Event('change'));
            inputEl.dispatchEvent(new Event('blur'));
        }
    }""", [date_input, date_str])

    await page.wait_for_timeout(1000)  # Wait for AJAX save

    # Verify the date input was set
    val = await date_input.input_value()
    if not val.strip():
        logger.warning(
            "fill_date_cell: date input empty after set — datepicker may not have triggered "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False
    logger.info("Set date '%s' in sec=%d row=%d col=%d",
                date_str, section_index, row_index, col_index)
    return True


# ---------------------------------------------------------------------------
# Checkbox dialog handler
# ---------------------------------------------------------------------------


async def fill_checkbox_cell(
    page: Page, section_index: int, row_index: int, col_index: int,
    selections: list[str]
) -> bool:
    """Fill a checkbox cell by opening the dialog and checking options.

    selections: list of option label texts to check (e.g. ['皮膚腫塊', '乳房腫塊']).

    TAS mechanism:
    1. Click "請輸入" link → triggers inContent2(this, 'checkbox', qid, cid)
    2. Dialog #diag-frm opens, populated with checkbox options from .hcontent
    3. Check the desired boxes
    4. Click "確定" to save via AJAX
    """
    cell = await _get_data_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    # Find the content trigger link
    content_link = await cell.query_selector("a[onclick*='inContent']")
    if not content_link:
        logger.warning("No content link found in cell sec=%d row=%d col=%d",
                        section_index, row_index, col_index)
        return False

    # Click to open the dialog
    await content_link.click()
    await page.wait_for_timeout(800)

    # The dialog should now be open: #diag-frm or .ui-dialog
    dialog = await page.query_selector("#diag-frm")
    if not dialog:
        dialog = await page.query_selector(".ui-dialog .ui-dialog-content")
    if not dialog:
        logger.warning("Dialog not found after clicking content link")
        return False

    # Find checkboxes OR radio buttons (TAS uses both depending on question type)
    inputs = await dialog.query_selector_all(
        "input[type='checkbox'], input[type='radio']"
    )
    matched = 0
    for inp in inputs:
        label_el = await inp.evaluate_handle("el => el.closest('label')")
        label_text = ""
        if label_el:
            elem = label_el.as_element()
            if elem:
                label_text = (await elem.inner_text()).strip()

        for sel in selections:
            if sel.strip() and sel.strip() in label_text:
                is_checked = await inp.is_checked()
                if not is_checked:
                    await inp.click()
                matched += 1
                break

    # Fallback: if nothing matched, select the first available option
    if matched == 0 and inputs:
        await inputs[0].click()
        matched = 1
        logger.info("Checkbox cell: no label match for %s, selected first option", selections)

    logger.info("Checked %d/%d requested options in dialog", matched, len(selections))

    # Click 確定 to confirm
    ok_btn = await page.query_selector(
        ".ui-dialog-buttonpane button:has-text('確定')"
    )
    if ok_btn:
        await ok_btn.click()
        await page.wait_for_timeout(1000)  # Wait for AJAX save
    else:
        logger.warning("Could not find 確定 button in dialog")

    if matched == 0:
        return False

    # Verify the cell was updated by the AJAX save
    await page.wait_for_timeout(300)
    cell_text_after = (await cell.inner_text()).strip()
    if not cell_text_after or cell_text_after == "請輸入":
        logger.warning(
            "fill_checkbox_cell: cell still '請輸入' after confirm — AJAX may not have saved "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Text dialog handler
# ---------------------------------------------------------------------------


async def fill_text_cell(
    page: Page, section_index: int, row_index: int, col_index: int,
    text: str
) -> bool:
    """Fill a text cell by opening the dialog and typing.

    TAS mechanism:
    1. Click "請輸入" → opens dialog with text input
    2. Type text
    3. Click 確定
    """
    cell = await _get_data_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    content_link = await cell.query_selector("a[onclick*='inContent']")
    if not content_link:
        return False

    await content_link.click()
    await page.wait_for_timeout(800)

    dialog = await page.query_selector("#diag-frm")
    if not dialog:
        dialog = await page.query_selector(".ui-dialog .ui-dialog-content")
    if not dialog:
        logger.warning("Dialog not found")
        return False

    # Fill text input or textarea
    text_input = await dialog.query_selector("input[type='text']")
    if text_input:
        await text_input.fill(text)
    else:
        textarea = await dialog.query_selector("textarea")
        if textarea:
            await textarea.fill(text)
        else:
            logger.warning("No text input found in dialog")
            return False

    # Click 確定
    ok_btn = await page.query_selector(
        ".ui-dialog-buttonpane button:has-text('確定')"
    )
    if ok_btn:
        await ok_btn.click()
        await page.wait_for_timeout(1000)
    else:
        logger.warning("Could not find 確定 button")

    # Verify the cell was updated by the AJAX save
    await page.wait_for_timeout(300)
    cell_text_after = (await cell.inner_text()).strip()
    if not cell_text_after or cell_text_after == "請輸入":
        logger.warning(
            "fill_text_cell: cell still '請輸入' after confirm — AJAX may not have saved "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Textarea dialog handler
# ---------------------------------------------------------------------------


async def fill_textarea_cell(
    page: Page, section_index: int, row_index: int, col_index: int,
    text: str
) -> bool:
    """Fill a textarea cell by opening the dialog and typing."""
    return await fill_text_cell(page, section_index, row_index, col_index, text)


# ---------------------------------------------------------------------------
# Fixed-row table handlers (section 三 style)
# ---------------------------------------------------------------------------


async def _get_fixed_cell(
    page: Page, section_index: int, row_index: int, col_index: int
) -> ElementHandle | None:
    """Get a cell from a fixed (non-dynamic) table section."""
    wrappers = await page.query_selector_all("div.table-responsive")
    if section_index >= len(wrappers):
        return None

    wrapper = wrappers[section_index]
    table = await wrapper.query_selector("table")
    if not table:
        return None

    rows = await table.query_selector_all("tbody tr")
    if row_index >= len(rows):
        return None

    row = rows[row_index]
    cells = await row.query_selector_all("td")
    if col_index >= len(cells):
        return None

    return cells[col_index]


async def fill_fixed_date(
    page: Page, section_index: int, row_index: int, col_index: int,
    date_str: str
) -> bool:
    """Fill a date cell in a fixed-row table."""
    cell = await _get_fixed_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    cell_text = (await cell.inner_text()).strip()
    if cell_text and cell_text != "請輸入":
        logger.info(
            "Fixed date cell already filled — skipping (sec=%d row=%d col=%d)",
            section_index, row_index, col_index
        )
        return True

    date_link = await cell.query_selector("a[onclick*='inConetentDate']")
    if not date_link:
        return False

    await date_link.click()
    await page.wait_for_timeout(500)

    date_input = await cell.query_selector("input[type='text']")
    if not date_input:
        return False

    await page.evaluate("""([inputEl, dateStr]) => {
        const $input = $(inputEl);
        if ($input.data('datepicker')) {
            $input.datepicker('setDate', dateStr);
            $input.datepicker('hide');
        } else {
            inputEl.value = dateStr;
            inputEl.dispatchEvent(new Event('change'));
            inputEl.dispatchEvent(new Event('blur'));
        }
    }""", [date_input, date_str])

    await page.wait_for_timeout(1000)

    val = await date_input.input_value()
    if not val.strip():
        logger.warning(
            "fill_fixed_date: date input empty after set (sec=%d row=%d col=%d)",
            section_index, row_index, col_index
        )
        return False
    return True


async def fill_fixed_text(
    page: Page, section_index: int, row_index: int, col_index: int,
    text: str
) -> bool:
    """Fill a text /textarea cell in a fixed-row table."""
    cell = await _get_fixed_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    cell_text = (await cell.inner_text()).strip()
    if cell_text and cell_text != "請輸入":
        logger.info(
            "Fixed text cell already filled — skipping (sec=%d row=%d col=%d)",
            section_index, row_index, col_index
        )
        return True

    content_link = await cell.query_selector("a[onclick*='inContent']")
    if not content_link:
        return False

    await content_link.click()
    await page.wait_for_timeout(800)

    dialog = await page.query_selector("#diag-frm")
    if not dialog:
        return False

    text_input = await dialog.query_selector("input[type='text']")
    if text_input:
        await text_input.fill(text)
    else:
        textarea = await dialog.query_selector("textarea")
        if textarea:
            await textarea.fill(text)
        else:
            return False

    ok_btn = await page.query_selector(
        ".ui-dialog-buttonpane button:has-text('確定')"
    )
    if ok_btn:
        await ok_btn.click()
        await page.wait_for_timeout(1000)

    # Verify the cell was updated
    await page.wait_for_timeout(300)
    cell_text_after = (await cell.inner_text()).strip()
    if not cell_text_after or cell_text_after == "請輸入":
        logger.warning(
            "fill_fixed_text: cell still '請輸入' after confirm — AJAX may not have saved "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False
    return True


async def fill_fixed_checkbox(
    page: Page, section_index: int, row_index: int, col_index: int,
    selections: list[str]
) -> bool:
    """Fill a checkbox/radio cell in a fixed-row table."""
    cell = await _get_fixed_cell(page, section_index, row_index, col_index)
    if not cell:
        return False

    # Skip cells that already have a value — TAS shows "請輸入" when empty
    cell_text = (await cell.inner_text()).strip()
    if cell_text and cell_text != "請輸入":
        logger.info(
            "Fixed checkbox cell already filled ('%s') — skipping "
            "(sec=%d row=%d col=%d)", cell_text[:30], section_index, row_index, col_index
        )
        return True

    content_link = await cell.query_selector("a[onclick*='inContent']")
    if not content_link:
        return False

    await content_link.click()
    await page.wait_for_timeout(1000)  # radio dialogs can be slower to open

    # Try multiple dialog selectors (TAS uses different containers depending on form type)
    dialog = await page.query_selector("#diag-frm")
    if not dialog:
        dialog = await page.query_selector(".ui-dialog .ui-dialog-content")
    if not dialog:
        logger.warning(
            "Fixed checkbox: dialog not found after clicking link "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False

    # Collect standard inputs first
    inputs = await dialog.query_selector_all(
        "input[type='checkbox'], input[type='radio']"
    )

    matched = 0
    if selections:
        for inp in inputs:
            label_el = await inp.evaluate_handle("el => el.closest('label')")
            label_text = ""
            if label_el:
                elem = label_el.as_element()
                if elem:
                    label_text = (await elem.inner_text()).strip()

            for sel in selections:
                if sel.strip() and sel.strip() in label_text:
                    is_checked = await inp.is_checked()
                    if not is_checked:
                        await inp.click()
                    matched += 1
                    break

    # Fallback: select first available standard input
    if matched == 0 and inputs:
        is_checked = await inputs[0].is_checked()
        if not is_checked:
            await inputs[0].click()
        matched = 1
        logger.info(
            "Fixed checkbox cell: selected first standard input "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )

    # Second fallback: Bootstrap button-group style radio labels (label.btn > input)
    if matched == 0:
        btn_labels = await dialog.query_selector_all("label.btn")
        if btn_labels:
            # Click the first btn label (index 0 is usually the lowest/best score)
            await btn_labels[0].click()
            matched = 1
            logger.info(
                "Fixed checkbox cell: clicked first Bootstrap btn label "
                "(sec=%d row=%d col=%d)", section_index, row_index, col_index
            )

    # Confirm dialog — try common TAS button text variants
    for btn_text in ("確定", "確認", "保存", "OK"):
        ok_btn = await page.query_selector(
            f".ui-dialog-buttonpane button:has-text('{btn_text}')"
        )
        if ok_btn:
            await ok_btn.click()
            await page.wait_for_timeout(1000)
            break

    if matched == 0:
        return False

    # Verify the cell was updated
    await page.wait_for_timeout(300)
    cell_text_after = (await cell.inner_text()).strip()
    if not cell_text_after or cell_text_after == "請輸入":
        logger.warning(
            "fill_fixed_checkbox: cell still '請輸入' after confirm — AJAX may not have saved "
            "(sec=%d row=%d col=%d)", section_index, row_index, col_index
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Generic widget handlers (backwards compatible)
# ---------------------------------------------------------------------------


async def _fill_radio(page: Page, q: QuestionBlock, answer: str) -> bool:
    """Click the radio option whose label matches `answer`."""
    container = await _get_container(page, q)
    if not container:
        return False

    radios = await container.query_selector_all("input[type='radio']")
    for radio in radios:
        label_text = await _get_input_label(container, radio)
        if _text_matches(label_text, answer):
            await radio.click()
            if not await radio.is_checked():
                logger.warning("Radio not checked after click for '%s'", q.prompt[:60])
                return False
            return True

    for radio in radios:
        value = await radio.get_attribute("value") or ""
        if _text_matches(value, answer):
            await radio.click()
            if not await radio.is_checked():
                logger.warning("Radio not checked after click for '%s'", q.prompt[:60])
                return False
            return True

    logger.warning("Radio option not found for answer '%s'", answer[:60])
    return False


async def _fill_checkbox(page: Page, q: QuestionBlock, answer: str) -> None:
    """Open the TAS dialog and check matching options."""
    sec_idx = q.dom_hints.section_index
    col_idx = q.dom_hints.column_index

    if sec_idx >= 0 and col_idx >= 0:
        # Use TAS dialog-based checkbox handler
        selections = [s.strip() for s in answer.split(",") if s.strip()]
        # We need to find the latest visible row
        wrappers = await page.query_selector_all("div.table-responsive")
        if sec_idx < len(wrappers):
            wrapper = wrappers[sec_idx]
            table = await wrapper.query_selector("table")
            if table:
                rows = await table.query_selector_all("tbody tr.tblstd:not(.hidden)")
                if rows:
                    # Fill the last visible row
                    await fill_checkbox_cell(page, sec_idx, len(rows) - 1, col_idx, selections)
                    return

    # Fallback: generic checkbox handling
    container = await _get_container(page, q)
    if not container:
        return

    targets = [a.strip() for a in answer.split(",")]
    checkboxes = await container.query_selector_all("input[type='checkbox']")
    for cb in checkboxes:
        label_text = await _get_input_label(container, cb)
        for target in targets:
            if _text_matches(label_text, target):
                is_checked = await cb.is_checked()
                if not is_checked:
                    await cb.click()
                break


async def _fill_textarea(page: Page, q: QuestionBlock, answer: str) -> bool:
    """Type into a textarea, clearing previous content first.

    Handles two cases:
    1. Form-group: direct textarea with element ID (e.g. #qs10)
    2. Generic: find textarea in container
    """
    # Try direct element by ID first (form-group style)
    if q.question_id and not q.question_id.startswith(("q_", "sec", "fixed_", "sa_", "fg_")):
        el = await page.query_selector(f"#{q.question_id}")
        if el:
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "textarea":
                await el.click()
                await el.fill(answer)
                # Trigger onblur to save via AJAX
                await el.evaluate("el => el.blur()")
                await page.wait_for_timeout(500)
                return bool((await el.input_value()).strip())

    container = await _get_container(page, q)
    if not container:
        return False

    textarea = await container.query_selector("textarea")
    if textarea:
        await textarea.click()
        await textarea.fill(answer)
        await textarea.evaluate("el => el.blur()")
        await page.wait_for_timeout(500)
        return bool((await textarea.input_value()).strip())

    logger.warning("No textarea found for '%s'", q.prompt[:60])
    return False


async def _fill_text(page: Page, q: QuestionBlock, answer: str) -> bool:
    """Fill a text field — either directly or via TAS dialog.

    Handles three cases:
    1. Form-group: direct input with element ID (e.g. #qs2)
    2. Dynamic table: via TAS dialog
    3. Generic: find input in container
    """
    # Try direct element by ID first (form-group style)
    if q.question_id and not q.question_id.startswith(("q_", "sec", "fixed_", "sa_", "fg_")):
        el = await page.query_selector(f"#{q.question_id}")
        if el:
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "input":
                await el.click()
                await el.fill(answer)
                # Trigger onblur to save via AJAX
                await el.evaluate("el => el.blur()")
                await page.wait_for_timeout(500)
                return bool((await el.input_value()).strip())

    sec_idx = q.dom_hints.section_index
    col_idx = q.dom_hints.column_index

    if sec_idx >= 0 and col_idx >= 0:
        wrappers = await page.query_selector_all("div.table-responsive")
        if sec_idx < len(wrappers):
            wrapper = wrappers[sec_idx]
            table = await wrapper.query_selector("table")
            if table:
                rows = await table.query_selector_all("tbody tr.tblstd:not(.hidden)")
                if rows:
                    return await fill_text_cell(page, sec_idx, len(rows) - 1, col_idx, answer)

    container = await _get_container(page, q)
    if not container:
        return False

    inp = await container.query_selector("input[type='text'], input:not([type])")
    if inp:
        await inp.click()
        await inp.fill(answer)
        await inp.evaluate("el => el.blur()")
        await page.wait_for_timeout(500)
        return bool((await inp.input_value()).strip())

    return False


async def _fill_select(page: Page, q: QuestionBlock, answer: str) -> None:
    """Select a dropdown option by matching label text.

    Works with TAS `<select>` elements using `updCombo(this, id)` onchange.
    """
    # Try by element ID first
    select_el = None
    if q.question_id and not q.question_id.startswith(("q_", "sec", "fixed_", "sa_", "fg_")):
        select_el = await page.query_selector(f"#{q.question_id}")

    if not select_el:
        container = await _get_container(page, q)
        if container:
            select_el = await container.query_selector("select")

    if not select_el:
        logger.warning("No select element found for '%s'", q.prompt[:60])
        return

    # Find the option whose text matches
    option_els = await select_el.query_selector_all("option")
    for opt in option_els:
        text = (await opt.inner_text()).strip()
        value = await opt.get_attribute("value") or ""
        if _text_matches(text, answer) or _text_matches(value, answer):
            await select_el.select_option(value=value)
            # Trigger onchange to save via AJAX
            await select_el.evaluate("el => { var e = new Event('change'); el.dispatchEvent(e); }")
            await page.wait_for_timeout(500)
            return

    # If no exact match, try the first non-empty option
    for opt in option_els:
        value = await opt.get_attribute("value") or ""
        text = (await opt.inner_text()).strip()
        if value and "---" not in text:
            await select_el.select_option(value=value)
            await select_el.evaluate("el => { var e = new Event('change'); el.dispatchEvent(e); }")
            await page.wait_for_timeout(500)
            logger.info("Select: no exact match for '%s', picked first option '%s'", answer[:40], text)
            return


async def _fill_date(page: Page, q: QuestionBlock, answer: str) -> bool:
    """Fill a date field via TAS datepicker.

    Handles two cases:
    1. Form-group: direct input with showDatePicker onclick
    2. Dynamic table: via inConetentDate2 dialog
    """
    # Try direct element by ID first (form-group showDatePicker)
    if q.question_id and not q.question_id.startswith(("q_", "sec", "fixed_", "sa_", "fg_")):
        el = await page.query_selector(f"#{q.question_id}")
        if el:
            onclick = await el.get_attribute("onclick") or ""
            if "showDatePicker" in onclick:
                # Click to activate the jQuery datepicker, then set date via its API.
                # Calling updAdate() directly without the datepicker being active causes
                # a TypeError because the internal jQuery reference is undefined.
                await el.click()
                await page.wait_for_timeout(300)
                await page.evaluate(
                    """([sel, dateStr]) => {
                        const inp = document.querySelector(sel);
                        if (inp && $(inp).data('datepicker')) {
                            $(inp).datepicker('setDate', dateStr);
                            $(inp).datepicker('hide');
                        } else if (inp) {
                            inp.value = dateStr;
                            inp.blur();
                        }
                    }""",
                    [f"#{q.question_id}", answer],
                )
                await page.wait_for_timeout(800)
                val = await el.input_value()
                return bool(val.strip())

    sec_idx = q.dom_hints.section_index
    col_idx = q.dom_hints.column_index

    if sec_idx >= 0 and col_idx >= 0:
        wrappers = await page.query_selector_all("div.table-responsive")
        if sec_idx < len(wrappers):
            wrapper = wrappers[sec_idx]
            table = await wrapper.query_selector("table")
            if table:
                rows = await table.query_selector_all("tbody tr.tblstd:not(.hidden)")
                if rows:
                    return await fill_date_cell(page, sec_idx, len(rows) - 1, col_idx, answer)

    logger.warning("Cannot fill date without valid section/column index for '%s'", q.prompt[:60])
    return False


async def _fill_likert(page: Page, q: QuestionBlock, answer: str) -> bool:
    """Click the correct cell in a Likert-scale grid row."""
    container = await _get_container(page, q)
    if not container:
        return False

    radios = await container.query_selector_all("td input[type='radio']")
    if not radios:
        radios = await container.query_selector_all("input[type='radio']")

    try:
        idx = int(answer) - 1
        if 0 <= idx < len(radios):
            await radios[idx].click()
            return await radios[idx].is_checked()
    except ValueError:
        pass

    for radio in radios:
        value = await radio.get_attribute("value") or ""
        if _text_matches(value, answer):
            await radio.click()
            return await radio.is_checked()

    if radios:
        await radios[-1].click()
        logger.info("Likert: defaulted to last option for '%s'", q.prompt[:60])
        return await radios[-1].is_checked()

    return False


# ---------------------------------------------------------------------------
# All-in-one row filler
# ---------------------------------------------------------------------------


async def fill_dynamic_row_all_columns(
    page: Page, section_index: int, row_index: int,
    column_values: list[dict],
) -> dict:
    """Fill all columns in a single dynamic table row.

    Args:
        column_values: list of dicts, one per column (in order, skipping delete col):
            {
                "type": "date" | "checkbox" | "text" | "textarea",
                "value": str | list[str],
            }

    Returns dict of {col_index: success_bool}.
    """
    # Normalize TAS "tea*" teacher-evaluation type variants to their base types
    _TEA_NORM = {
        "teacheckbox": "checkbox", "tearadio": "radio",
        "teatext": "text", "teatextarea": "textarea", "teadate": "date",
    }

    results = {}
    for col_idx, col_val in enumerate(column_values):
        ctype = _TEA_NORM.get(col_val.get("type", "text"), col_val.get("type", "text"))
        value = col_val.get("value", "")

        if ctype == "date":
            results[col_idx] = await fill_date_cell(
                page, section_index, row_index, col_idx, value
            )
        elif ctype in ("checkbox", "radio"):
            # Both checkbox and radio open a TAS dialog — fill_checkbox_cell handles both
            selections = value if isinstance(value, list) else [s.strip() for s in value.split(",")]
            results[col_idx] = await fill_checkbox_cell(
                page, section_index, row_index, col_idx, selections
            )
        elif ctype in ("text", "textarea"):
            results[col_idx] = await fill_text_cell(
                page, section_index, row_index, col_idx, value
            )
        else:
            logger.warning("Unknown column type '%s' for col %d", ctype, col_idx)
            results[col_idx] = False

    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _get_container(page: Page, q: QuestionBlock) -> object | None:
    """Locate the DOM container for a question using its hints."""
    # Try element by ID (for form-group questions with real IDs like qs0, qs1)
    if q.question_id and not q.question_id.startswith(("q_", "sec", "fixed_", "sa_", "fg_")):
        el = await page.query_selector(f"#{q.question_id}")
        if el:
            # Navigate up to form-group or tr for context
            parent = await el.evaluate_handle("el => el.closest('.form-group') || el.closest('tr') || el.parentElement")
            if parent:
                return parent.as_element()
            return el

    if q.dom_hints.css_selector:
        el = await page.query_selector(q.dom_hints.css_selector)
        if el:
            parent = await el.evaluate_handle("el => el.closest('.form-group') || el.closest('tr') || el.parentElement")
            if parent:
                return parent.as_element()
            return el

    if q.dom_hints.label_text:
        el = await page.query_selector(f"text={q.dom_hints.label_text[:50]}")
        if el:
            parent = await el.evaluate_handle("el => el.closest('.form-group') || el.closest('tr') || el.parentElement")
            if parent:
                return parent.as_element()

    logger.warning("Could not locate container for question '%s'", q.prompt[:60])
    return None


async def _get_input_label(container, input_el) -> str:
    """Get the label text associated with an input/radio/checkbox."""
    # TAS uses <label> wrapping the input: <label><input ...> text</label>
    label_el = await input_el.evaluate_handle("el => el.closest('label')")
    if label_el:
        elem = label_el.as_element()
        if elem:
            return (await elem.inner_text()).strip()

    inp_id = await input_el.get_attribute("id")
    if inp_id:
        label = await container.query_selector(f"label[for='{inp_id}']")
        if label:
            return (await label.inner_text()).strip()

    text = await input_el.evaluate(
        """el => {
            let sib = el.nextSibling;
            while (sib && sib.nodeType === 3 && !sib.textContent.trim()) sib = sib.nextSibling;
            return sib ? sib.textContent.trim() : el.value || '';
        }"""
    )
    return text or ""


def _text_matches(label: str, answer: str) -> bool:
    """Case-insensitive fuzzy match."""
    a = answer.strip().lower()
    b = label.strip().lower()
    return a == b or a in b or b in a


# ---------------------------------------------------------------------------
# Ruler-grid filler (二、自我評估 style A/B/C/D/E tables)
# ---------------------------------------------------------------------------


async def fill_ruler_grids(page: Page) -> int:
    """Fill self-evaluation ruler grid tables that use direct radio inputs.

    These grids (e.g. 二、自我評估【病態或疾病處理、臨床技能】核心能力) are NOT inside
    a .table-responsive wrapper and use <input type="radio"> directly in tbody
    cells rather than inContent dialogs.  They are saved via a tmpSaveRulerGroup
    button (already handled by click_save).

    Strategy: for each data row, group radios by their `name` attribute and
    click value='B' (大部分達到) for 訓練後 groups, value='C' for 訓練前 groups.
    Falls back to clicking the second radio if no B/C value is found.

    Returns: count of rows filled.
    """
    tables = await page.query_selector_all("table")
    filled_rows = 0

    for table in tables:
        # Skip tables inside .table-responsive (handled by the fixed/dynamic section flow)
        in_responsive = await table.evaluate("el => !!el.closest('.table-responsive')")
        if in_responsive:
            continue

        # Only process tables that have radio inputs directly in tbody cells
        radios_in_body = await table.query_selector_all("tbody td input[type='radio']")
        if not radios_in_body:
            continue

        data_rows = await table.query_selector_all("tbody tr")
        for row in data_rows:
            row_radios = await row.query_selector_all("td input[type='radio']")
            if not row_radios:
                continue

            # Group radios by name attribute
            groups: dict[str, list] = {}
            for radio in row_radios:
                name = await radio.get_attribute("name") or ""
                if name:
                    groups.setdefault(name, []).append(radio)

            if not groups:
                continue

            clicked_any = False
            for name, group_radios in groups.items():
                # Determine preferred value: 'B' for 訓練後 groups, 'C' for 訓練前
                preferred = "B"
                if "pre" in name.lower() or "before" in name.lower():
                    preferred = "C"

                clicked = False
                # Try to click the preferred value first
                for radio in group_radios:
                    val = await radio.get_attribute("value") or ""
                    if val == preferred:
                        is_checked = await radio.is_checked()
                        if not is_checked:
                            await radio.click()
                            await page.wait_for_timeout(200)
                        clicked = True
                        clicked_any = True
                        break

                # Fallback: click second radio (index 1), or first if only one
                if not clicked and group_radios:
                    idx = 1 if len(group_radios) > 1 else 0
                    is_checked = await group_radios[idx].is_checked()
                    if not is_checked:
                        await group_radios[idx].click()
                        await page.wait_for_timeout(200)
                    clicked_any = True

            if clicked_any:
                filled_rows += 1

    if filled_rows:
        logger.info("Ruler grids: filled %d rows across all self-evaluation tables", filled_rows)
    return filled_rows


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS = {
    WidgetType.RADIO: _fill_radio,
    WidgetType.CHECKBOX: _fill_checkbox,
    WidgetType.TEXTAREA: _fill_textarea,
    WidgetType.TEXT: _fill_text,
    WidgetType.DATE: _fill_date,
    WidgetType.SELECT: _fill_select,
    WidgetType.LIKERT_GRID: _fill_likert,
    # TABLE_POPUP, FILE_UPLOAD, DYNAMIC_LIST, UNKNOWN are not directly handled
    # Use add_dynamic_row + fill_dynamic_row_all_columns for dynamic tables
}
