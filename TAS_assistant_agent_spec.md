# TAS Auto Completion (VGH) 

## 1) Project Goal

Build a **TAS Auto Completion Assistant** for https://tas.vghtpe.gov.tw/tas/UserPassport.action (after login) that:

1) Lists courses/forms and highlights low completion (“完成度”).
2) Opens a selected form and **extracts** question structure (including special widgets).
3) Provides:
   - “Filling”: choose options, paste answers, add rows.
   - “Drafting”: LLM creates open-text responses based on typical patient presentation.
4) Saves changes automatically by clicking save button, but **never** notify teachers formally.

---

## 2) Non-Goals (Hard Boundaries)
- No scraping of hospital clinical systems (HIS) / patient data in this version.
- Skip file upload questions in this version.

---

## 3) Recommended Stack

### Browser automation (DOM-first)
- **Playwright (Python)** for robust automation and tracing.

### Backend service (local)
- **FastAPI** (optional) to coordinate:
  - question extraction,
  - LLM calls,
  - audit logs.

### Data models / validation
- `pydantic` for strict schemas.
- `tenacity` for retries.
- `python-dotenv` for config.

---

## 4) High-Level Architecture

### Modules
1) **Navigator**
   - Finds course list, forms list, completion percentages.
   - Opens target form.
2) **Extractor**
   - Reads DOM to detect question blocks and widget types.
3) **UI Window**
   - Lists courses and their completion percentages for user to select. 
   - Lists forms inside selected courses and their completion percentages for user to select.
   - Users may fill in patient notes to help complete open text forms.
4) **LLM Drafting**
   - Sends only minimal context (question text + course title + form title + optional user notes).
   - Returns structured JSON drafts.
5) **Audit Log**
   - Records: timestamp, form id/name, question ids, what was suggested, what was applied, user confirmations.

---

## 5) Widget Types to Support (from TAS manuals)

TAS includes special question formats such as:
- **Likert/scale grids (量尺題)**: click empty cells on a scale row.
- **Table input (表格題)**: click “請輸入” → a popup appears → fill fields.
- **Dynamic row add (“+”)**: user must click “+” to add items.
- **Reflections (反思題)**: click “請輸入” → a popup appears → fill fields. Text area with maxlength=300.
- **File upload**: skip in this version.


> Implementation strategy: DOM selectors + role/label inference; avoid “vision AI” unless the page is non-semantic.

---

## 6) Core Data Schemas

### 6.1 FormSummary
```json
{
  "course_title": "string",
  "form_title": "string",
  "form_url": "string",
  "completion_percent": 0,
  "required_instances": 1,
  "completed_instances": 0
}
```

### 6.2 QuestionBlock
```json
{
  "question_id": "string",
  "prompt": "string",
  "required": true,
  "widget_type": "text|textarea|radio|checkbox|likert_grid|table_popup|file_upload|dynamic_list|unknown",
  "options": ["string"],
  "current_value": "any",
  "dom_hints": {
    "css_selector": "string",
    "label_text": "string",
    "aria": "string"
  }
}
```

### 6.3 DraftResponse (LLM output)
```json
{
  "question_id": "string",
  "answer": "string",
  "confidence": 0.0,
}
```

---

## 7) Playwright Implementation Notes

### 7.1 Login
- **Do not automate credential entry** unless the user explicitly triggers it.
- Prefer:
  - user logs in manually (Playwright launches a persistent profile),
  - then automation starts from the authenticated state.

Use Playwright persistent context:
- Chromium user data dir stored locally.
- Avoid storing passwords in code.

### 7.2 Selector strategy
- Prefer stable attributes:
  - `data-*`, `id`, `name`, `aria-label`, label text.
- Fallback:
  - semantic roles `get_by_role()` and text matching.
- Avoid brittle `nth-child` selectors.

### 7.3 “No notify teachers” enforcement
Implement a denylist of button texts/roles and block clicks:
- Text contains: `通知臨床教師`, `通知導師`, etc.

---

## 8) LLM (Online API) Integration

### 8.1 What to send
**Allowed context:**
- course/form title,
- question prompt,
- allowed rubric text (if any),
- user-provided notes.

**Avoid sending:**
- credentials,
- internal IDs that might be sensitive,
- any patient-identifiable info (even though this version won’t use HIS).

**Suggested prompt:**
You are a senior medical education assistant at Taipei Veterans General Hospital. Your task is to automatically fill in the forms to test the Teaching Assessment System (TAS).

#### Instructions:

1) Use English or Traditional Chinese (Taiwan) with standard medical English abbreviations (e.g., DM, HTN, s/p).
2) Maintain a professional, reflective tone.
3) Ensure the total length is around 300 characters.
4) Synthesize reasonable clinical details based on the provided specialty and user notes (if any) to create a complete case summary.
5) End the reflection with a section on "Core Competencies" (核心能力) gained.

### 8.2 Response format
Require **JSON** output with `question_id`, `answer`, `confidence`.
Reject non-JSON outputs.

### 8.3 Guardrails
- The LLM must:
  - create tentative answers based on typical patients seen at OPD or ward according to course specialties.
  - refer to user input notes if any.

---

## 9) UX Workflow (Recommended)

1) User opens TAS and logs in.
2) Assistant lists courses and forms and their completion percentages in its UI.
3) User selects forms for auto completion.
4) For each form, assistant extracts questions and fill in the answers.
5) Assistant saves the changes and fill the next selected form.
6) Assistant informs the user if all selected forms are completed.
7) Assistant never notifys instructors automatically.

---

## 10) Development Milestones

### Milestone A: Read-only navigator
- List courses/forms/completion.
- Open a chosen form.
- Screenshot/trace capture for debugging.

### Milestone B: Extractor
- Extract question blocks and widget types (80% coverage).
- Export JSON to disk for regression tests.

### Milestone C: Widget handlers
- radio / checkbox / textarea
- likert grid
- table popup (“請輸入”)
- dynamic add-row (“+”)

### Milestone D: LLM drafting
- Minimal API client
- JSON schema validation

### Milestone E: Audit & safety
- “No notify-instructors” blocker
- action preview (diff)
- logs

---

## 11) Testing Plan

- **Golden pages**: save a few representative HTML snapshots (if allowed) or Playwright traces.
- Unit tests:
  - question extraction on saved DOM snapshots,
  - schema validation,
- End-to-end:
  - dry-run mode verifying navigation and highlighting.

---

## 12) Configuration

Environment variables (example):
- `TAS_BASE_URL=https://tas.vghtpe.gov.tw/tas/`
- `LLM_PROVIDER=gemini|anthropic|openai`
- `LLM_API_KEY=...`
- `LLM_MODEL=...`
- `DRY_RUN=1`

---
