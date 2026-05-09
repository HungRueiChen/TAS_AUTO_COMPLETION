# TAS Auto Completion Assistant — Coding Agent Implementation Guide

This guide is intended for coding agents working on the TAS Auto Completion Assistant repository. It assumes the agent has already read and can access the existing project documents:

- `CLAUDE.md`
- `README.md`
- `TAS_assistant_agent_spec.md`

Do not duplicate those files. Use this document as an operational guide for how to improve the project safely and effectively.

---

## 1. Core Principle

The TAS website is **not a normal HTML form**.

Most TAS fields are JavaScript-driven widgets. Many visible fields are links such as `請輸入`, which trigger TAS-specific JavaScript functions, jQuery UI dialogs, dynamic table rows, or datepickers. Therefore, the agent must **not** treat TAS as a generic form-filling problem.

The correct approach is:

> Keep the runtime deterministic. Use Playwright handlers for known TAS widgets. Use the coding agent only to diagnose, patch, and test specific failures.

The agent should not attempt broad rewrites unless explicitly requested.

---

## 2. Recommended Architecture

The best architecture is a deterministic Playwright-based TAS engine with agent-assisted debugging.

```text
TAS Assistant
│
├─ Playwright deterministic runtime
│  ├─ navigator.py
│  ├─ extractor.py
│  ├─ handlers.py
│  ├─ diagnostics.py
│  ├─ safety.py
│  └─ audit.py
│
├─ TAS widget registry
│  ├─ newTblrow
│  ├─ inContent
│  ├─ inContent2
│  ├─ inConetentDate
│  ├─ inConetentDate2
│  ├─ showDatePicker
│  ├─ updAradio
│  └─ unknown onclick detector
│
├─ Test and diagnostic layer
│  ├─ diagnostics/*.json
│  ├─ snapshots/*.html
│  ├─ traces/*.zip
│  └─ tests/
│
├─ Optional Chrome extension companion
│  ├─ widget overlay
│  ├─ DOM inspector
│  ├─ notify-teacher blocker
│  └─ widget map exporter
│
└─ Claude Code / MCP debugging workflow
   ├─ inspect live TAS page
   ├─ inspect console/network/dialog state
   ├─ identify failed widget pattern
   ├─ patch specific handler
   └─ add regression test
```

---

## 3. What the Agent Should Prioritize

### 3.1 Improve the Existing Playwright App First

Do not start by replacing the project with a Chrome extension or a generic form filler.

The existing Python + FastAPI + Playwright architecture is appropriate because it supports:

- persistent login session
- non-headless browser interaction
- reproducible navigation
- Playwright tracing
- DOM snapshots
- deterministic widget handlers
- local dashboard
- audit logging
- safety blocking

The agent should harden the current implementation before considering additional tools.

---

### 3.2 Treat Every TAS Widget as a State Machine

Each widget handler should follow this lifecycle:

```text
detect → open/activate → fill → save/confirm → verify → log
```

The agent should avoid direct value injection unless a handler has proven that direct injection correctly triggers TAS state updates.

Example:

```text
Wrong:
- locate input
- set value directly

Right:
- click original TAS onclick trigger
- wait for dialog or datepicker
- fill within the official TAS UI
- click TAS confirm/save control
- verify displayed value or persisted state
```

---

### 3.3 Build a Widget Registry

Refactor scattered widget logic into a registry if this has not already been done.

Suggested interface:

```python
class TasWidgetHandler:
    def matches(self, onclick: str, context: WidgetContext) -> bool:
        ...

    async def extract(self, page, element) -> WidgetSpec:
        ...

    async def fill(self, page, element, answer) -> FillResult:
        ...

    async def verify(self, page, element, answer) -> VerifyResult:
        ...
```

Suggested registry:

```python
TAS_WIDGET_REGISTRY = {
    "newTblrow": AddDynamicRowHandler,
    "inContent2": DynamicTableCellHandler,
    "inConetentDate2": DynamicDateCellHandler,
    "inContent": FixedTableCellHandler,
    "inConetentDate": FixedDateCellHandler,
    "showDatePicker": StandaloneDateHandler,
    "updAradio": StandaloneRadioHandler,
}
```

Unknown widgets should be skipped safely and logged.

---

## 4. What the Agent Should Avoid

### 4.1 Do Not Use Generic Form-Filler Logic as the Core

Do not rely on generic autofill repositories as the main engine. They are usually designed for:

- ordinary `<input>` fields
- labels
- placeholders
- `name` attributes
- Faker-style test data
- contact/address/payment forms

TAS requires special JavaScript interaction patterns, dialog handling, dynamic row management, and state verification. Generic repos may provide UI inspiration, but not the core filling logic.

---

### 4.2 Do Not Let the LLM Make Runtime Click Decisions

Avoid this pattern:

```text
Agent looks at browser → decides what to click → clicks → guesses next step
```

Use this instead:

```text
Extractor detects widget → deterministic handler fills widget → verifier checks result
```

LLMs are useful for generating open-text draft answers and debugging code, not for uncontrolled runtime browser operation.

---

### 4.3 Do Not Automate Dangerous Actions

Never click buttons or links related to formal teacher notification.

Block or skip actions containing patterns such as:

```text
putTeacher
putTeacher2
通知臨床教師
通知導師
通知教師
```

Every programmatic click should pass through a safety check.

---

### 4.4 Do Not Send Sensitive Data to LLM APIs

The current version should not scrape HIS or send patient-identifiable information.

Allowed context for LLM drafting:

- course title
- form title
- question prompt
- user-provided non-identifiable notes
- allowed rubric text

Avoid:

- credentials
- patient identifiers
- chart numbers
- names
- dates of birth
- national IDs
- full clinical records copied from HIS

---

## 5. Debugging Workflow for Coding Agents

When a bug is reported, do not start with broad code rewriting.

Follow this sequence:

```text
1. Reproduce or inspect the latest failed run.
2. Read the latest diagnostics JSON.
3. Identify the first failed or unhandled widget.
4. Inspect its onclick pattern.
5. Compare it with known handled patterns.
6. Open the relevant handler code.
7. Patch only the minimal handler logic.
8. Add or update a regression test.
9. Run import checks and tests.
10. Summarize the exact failure and fix.
```

---

## 6. Required Failure Artifacts

For every failed fill run, the application should save enough information for the coding agent to debug without guessing.

Recommended files:

```text
diagnostics/
  2026-05-08_form-name.json

snapshots/
  form-name_before.html
  form-name_after_click.html
  form-name_dialog_open.html
  form-name_after_save.html

traces/
  form-name_failed_trace.zip

logs/
  audit_log.jsonl
```

A good bug report should contain:

```json
{
  "form_title": "string",
  "form_url": "string",
  "question_text": "string",
  "section_index": 0,
  "onclick": "inContent2(this, ...)",
  "expected_behavior": "dialog opens and textarea is filled",
  "actual_behavior": "dialog opens but value is not saved",
  "screenshot_path": "string",
  "html_snapshot_path": "string",
  "trace_path": "string"
}
```

---

## 7. Testing Strategy

### 7.1 Golden DOM Snapshots

Whenever a new TAS widget type is discovered, save a representative HTML snapshot.

Test extraction against this snapshot.

Example tests:

```text
tests/
  test_extract_dynamic_table.py
  test_extract_fixed_table.py
  test_extract_standalone_radio.py
  test_extract_datepicker.py
  test_extract_unknown_onclick.py
```

---

### 7.2 Handler-Level Tests

Each widget handler should have tests for:

- detection
- extraction
- fill flow
- failure behavior
- verification
- safety skip behavior

The agent should prefer fixing bugs by adding or updating handler-specific tests.

---

### 7.3 Dry-Run Mode

`DRY_RUN=1` should never mutate TAS data.

In dry-run mode, the app should:

- navigate
- extract
- identify handlers
- draft answers
- log intended actions
- avoid clicking final save/confirm controls when mutation would occur

---

## 8. Verification Rules

A fill operation should not be considered successful immediately after a click.

Use at least one verification method:

```text
- displayed value changed
- hidden input changed
- dialog closed after confirm
- row count increased
- TAS AJAX request completed
- completion percentage changed after reload
- audit log confirms action
```

For dynamic sections, verify row existence using robust selectors rather than unreliable cloned-row classes.

Recommended principle:

```text
Do not trust class names alone. Confirm visible rows by actual structure, visible state, and delete-row controls.
```

---

## 9. Recommended MCP Usage

Claude Code, Playwright MCP, and Chrome DevTools MCP should be used for debugging and inspection, not as the production filling runtime.

### 9.1 Playwright MCP

Use Playwright MCP to:

- inspect page structure
- interact with visible TAS pages
- reproduce bugs
- inspect accessibility snapshots
- confirm visible behavior

Useful when the issue is about element selection or page state.

---

### 9.2 Chrome DevTools MCP

Use Chrome DevTools MCP to:

- inspect console errors
- inspect network requests
- inspect JavaScript errors
- inspect DOM mutations
- inspect dialog state
- capture screenshots
- debug event behavior

Useful when the issue is about TAS JavaScript, AJAX, jQuery dialogs, or datepickers.

---

### 9.3 Suggested Agent Prompt for MCP Debugging

```text
You are debugging the TAS Auto Completion Assistant.

Do not rewrite the architecture.

Steps:
1. Read CLAUDE.md, README.md, TAS_assistant_agent_spec.md, and this guide.
2. Read the latest diagnostics JSON.
3. Identify the first failed or unhandled onclick pattern.
4. Use Playwright MCP or Chrome DevTools MCP to inspect the live page state if available.
5. Patch only the relevant extractor or handler.
6. Add a regression test using the saved snapshot or mocked widget structure.
7. Run the project import check.
8. Run the relevant tests.
9. Report the exact failure, exact patch, and remaining risks.
```

---

## 10. Optional Chrome Extension Companion

A Chrome extension can be useful, but should be treated as a companion tool rather than the main automation engine.

Recommended extension features:

```text
TAS Widget Inspector
- Detect all clickable TAS widgets on the current page.
- Extract onclick function names and parameters.
- Show supported vs unsupported widget types.
- Highlight unknown widgets.
- Disable teacher-notification controls.
- Export widget map as JSON.
- Optionally send widget map to http://localhost:8550.
```

Avoid building full automation into the extension unless there is a strong reason. Playwright is better for long-running, traceable, testable automation.

---

## 11. Suggested Development Milestones

### Milestone 1 — Failure Reproducibility

- Add Playwright tracing per form.
- Save DOM snapshots at key states.
- Make diagnostics JSON comprehensive.
- Ensure every failed action has a useful error message.

### Milestone 2 — Widget Registry

- Centralize known onclick handlers.
- Add handler interface.
- Add unknown-widget logging.
- Add safe skip behavior.

### Milestone 3 — Verification

- Add post-fill verification to each handler.
- Mark partial success separately from full success.
- Reload form after save when necessary to verify persistence.

### Milestone 4 — Regression Tests

- Add tests for existing representative forms.
- Add tests for every newly discovered widget pattern.
- Prevent fixed bugs from recurring.

### Milestone 5 — Agent Debugging Skill

- Add a `tas-debugging-skill.md` or equivalent agent instruction file.
- Include the debugging workflow from this guide.
- Require minimal patches and test-backed fixes.

### Milestone 6 — Optional Chrome Extension

- Add only if visual inspection remains painful.
- Keep it small.
- Use it for inspection, overlay, safety, and JSON export.

---

## 12. Coding Agent Patch Rules

When modifying this repository, the agent should follow these rules:

```text
1. Prefer minimal targeted patches.
2. Do not rewrite working handlers.
3. Do not remove safety checks.
4. Do not automate credential entry.
5. Do not add HIS scraping.
6. Do not send patient-identifiable data to LLMs.
7. Do not click teacher-notification buttons.
8. Do not treat unknown widgets as text inputs.
9. Add diagnostics for every new failure mode.
10. Add tests for every fixed bug.
```

---

## 13. Practical Bug-Fix Template

Use this template when reporting a completed fix.

```text
Bug fixed:
- Form:
- Widget:
- onclick:
- Failure:
- Root cause:
- Files changed:
- Tests added:
- Verification:
- Remaining risk:
```

Example:

```text
Bug fixed:
- Form: Mini-CEX
- Widget: dynamic table textarea cell
- onclick: inContent2(this, type, qid, cid)
- Failure: Dialog opened but value was not saved
- Root cause: Handler filled textarea but clicked the wrong confirm button
- Files changed: src/handlers.py, tests/test_dynamic_table_dialog.py
- Tests added: test_fill_dynamic_textarea_dialog_confirms_value
- Verification: Test passes and displayed cell text updates after dialog closes
- Remaining risk: Similar dialogs with multiple confirm buttons may need more specific selectors
```

---

## 14. Bottom Line

The agent should not try to make TAS work through generic autofill or uncontrolled browser agents.

The correct strategy is:

> Deterministic Playwright automation for TAS-specific widgets, comprehensive diagnostics, regression tests, and agent-assisted debugging through Claude Code / MCP.

Prioritize reliability, auditability, and safety over broad autonomy.
