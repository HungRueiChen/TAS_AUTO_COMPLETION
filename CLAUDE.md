# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

```bash
# Python interpreter (Anaconda, no venv)
/c/Users/ray00/anaconda3/python.exe -m src.main

# Install / update dependencies
/c/Users/ray00/anaconda3/python.exe -m pip install -r requirements.txt

# Install Playwright browser (first time only)
/c/Users/ray00/anaconda3/python.exe -m playwright install chromium

# Verify imports after editing
/c/Users/ray00/anaconda3/python.exe -c "from src.<module> import <symbol>; print('OK')"
```

The web dashboard opens automatically at **http://localhost:8550**.
Playwright launches a **non-headless Chromium** window for the user to log in manually; automation starts from the authenticated state.

## Architecture

The app is a FastAPI + Playwright automation tool. The request flow for a fill run is:

```
User (browser dashboard)
  → POST /api/fill  (ui.py)
      → open_form()           navigator.py  — navigate Playwright to the form URL
      → extract_form_structure()  extractor.py  — parse DOM → structure dict
      → run_diagnostics()     diagnostics.py — capture onclick patterns, write JSON report
      → [dynamic sections]    ui.py loop
          → add_dynamic_row() handlers.py   — click ⊕ button (skipped if rows exist)
          → fill_dynamic_row_all_columns()  handlers.py
      → [fixed sections]      ui.py loop
          → fill_fixed_*()    handlers.py
      → [standalone Qs]       ui.py loop
          → draft_answers()   llm_client.py — Gemini/OpenAI/Anthropic API
          → fill_question()   handlers.py
      → click_save()          navigator.py
      → close_browser()       navigator.py  — auto-close after all forms done
```

### Key design points

**TAS widget interaction model** — almost nothing is a plain `<input>`. Most cells show a "請輸入" link that triggers a JS function; clicking it opens a jQuery UI dialog (`#diag-frm`) or datepicker. Always interact via the onclick trigger, not by directly setting field values.

**onclick function taxonomy:**
| Function | Where | Handler |
|---|---|---|
| `newTblrow(this)` | dynamic table ⊕ btn | `add_dynamic_row()` |
| `inContent2(this, type, qid, cid)` | dynamic table cell | `fill_*_cell()` |
| `inConetentDate2(this, ...)` | dynamic table date | `fill_date_cell()` |
| `inContent(this, type, qid, qtid, cid)` | fixed table cell | `fill_fixed_*()` |
| `inConetentDate(this, ...)` | fixed table date | `fill_fixed_date()` |
| `showDatePicker(this, qaid)` | standalone date input | `_fill_date()` — click first, then `datepicker('setDate')` |
| `updAradio(this, qaid)` | standalone radio | `_fill_radio()` — click triggers AJAX automatically |
| `tmpSaveRuler` / `tmpSaveRulerGroup` | evaluation form save | `click_save()` selector list |
| `putTeacher` / `putTeacher2` | notify-teacher btn | blocked by `safety.py` JS injector |

**Section indexing** — `section_index` in `TasDynamicSection` and `TasFixedRow` is the **0-based index among ALL `.table-responsive` wrappers on the page**, shared between dynamic and fixed sections. Both `extractor.py` and `handlers.py` must use the same wrapper list to stay in sync.

**Existing-row detection** — `tr.tblstd:not(.hidden)` is unreliable because TAS may not retain the `tblstd` class on cloned rows. Always detect visible data rows via:
- `extractor.py`: JS `evaluate()` checking `classList.contains('hidden')` + `getComputedStyle` + presence of `delTblrow` delete button
- `handlers.py` (`_get_data_cell`): CSS `:has()` — `"tbody tr:not(.hidden):has(a[onclick*='delTblrow'])"`

**Date filling** — for `showDatePicker` standalone inputs, click the input first to activate the jQuery datepicker instance, then call `datepicker('setDate', date)` + `datepicker('hide')`. Calling `updAdate()` directly without the datepicker being active causes a TypeError.

**LLM drafting** — `generate_column_answer()` in `llm_client.py` handles date, checkbox, and common text columns locally with keyword matching on the column header (病歷號, 診斷, 個案摘要, 心得, etc.) without an API call. Only unrecognised text/textarea/radio/likert questions go to the LLM.

**Diagnostics** — every fill run writes `diagnostics/<timestamp>_<form>.json`. Unhandled onclick patterns surface as `[DIAG]` warnings in the console log. Update `_HANDLED_PATTERNS` / `_IGNORABLE_PATTERNS` in `diagnostics.py` when new TAS widget types are identified.

**Safety** — `safety.py` injects a MutationObserver that disables teacher-notification buttons by both text content and `onclick` attribute (`putTeacher`, `putTeacher2`). `is_safe_to_click()` is called before every programmatic button click.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `openai` \| `anthropic` |
| `LLM_MODEL` | `gemini-2.5-flash` | Model name for the provider |
| `LLM_API_KEY` | — | Provider API key |
| `DRY_RUN` | `0` | Set `1` to log actions without filling |
| `BROWSER_DATA_DIR` | `./browser_data` | Persistent Chromium profile (login session) |
| `UI_PORT` | `8550` | FastAPI dashboard port |
