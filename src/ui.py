"""Web UI module — FastAPI-based dashboard for TAS Auto Completion Assistant."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import FormSummary, WidgetType

logger = logging.getLogger(__name__)

app = FastAPI(title="TAS Auto Completion Assistant", version="0.1.0")

# ---------------------------------------------------------------------------
# In-memory state (shared with background tasks)
# ---------------------------------------------------------------------------

_state = {
    "courses": [],        # list[dict]
    "forms": [],          # list[FormSummary]
    "status": "idle",     # idle | scanning | filling | done | error
    "progress": "",       # human-readable status text
    "selected_course": None,
    "fill_results": [],   # list of result messages
}


def get_state():
    return _state


# ---------------------------------------------------------------------------
# HTML template (single-page dashboard)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TAS Auto Completion Assistant</title>
    <style>
        :root {
            --bg-primary: #0f1117;
            --bg-secondary: #1a1d2e;
            --bg-card: #232740;
            --bg-hover: #2a2f4a;
            --accent: #6c63ff;
            --accent-hover: #847dff;
            --accent-glow: rgba(108, 99, 255, 0.25);
            --success: #2ecc71;
            --warning: #f1c40f;
            --danger: #e74c3c;
            --text-primary: #e8e8f0;
            --text-secondary: #9a9ab0;
            --text-muted: #6b6b80;
            --border: #2d3154;
            --radius: 12px;
            --shadow: 0 8px 32px rgba(0,0,0,0.4);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Segoe UI', 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }

        /* Header */
        .header {
            background: linear-gradient(135deg, var(--bg-secondary), var(--bg-card));
            border-bottom: 1px solid var(--border);
            padding: 20px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: var(--shadow);
        }
        .header h1 {
            font-size: 1.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--accent), #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }
        .header .subtitle {
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-top: 2px;
        }

        /* Status badge */
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 20px;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }
        .status-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            background: var(--text-muted);
        }
        .status-dot.active {
            background: var(--success);
            box-shadow: 0 0 8px rgba(46, 204, 113, 0.5);
            animation: pulse 2s infinite;
        }
        .status-dot.filling { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); animation: pulse 1s infinite; }
        .status-dot.error { background: var(--danger); }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Main layout */
        .main {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            padding: 24px 32px;
            max-width: 1400px;
            margin: 0 auto;
        }

        /* Cards */
        .card {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            overflow: hidden;
            box-shadow: var(--shadow);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .card:hover { transform: translateY(-2px); box-shadow: 0 12px 40px rgba(0,0,0,0.5); }

        .card-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-card);
        }
        .card-header h2 {
            font-size: 1rem;
            font-weight: 600;
            color: var(--text-primary);
        }
        .card-body { padding: 16px 20px; }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px 18px;
            border: none;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), #847dff);
            color: #fff;
            box-shadow: 0 4px 12px var(--accent-glow);
        }
        .btn-primary:hover { background: linear-gradient(135deg, var(--accent-hover), #9d97ff); transform: translateY(-1px); }
        .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

        .btn-sm { padding: 6px 12px; font-size: 0.8rem; }
        .btn-success { background: linear-gradient(135deg, #27ae60, #2ecc71); color: #fff; }
        .btn-outline {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-secondary);
        }
        .btn-outline:hover { border-color: var(--accent); color: var(--accent); }

        /* Tables */
        .data-table {
            width: 100%;
            border-collapse: collapse;
        }
        .data-table th {
            text-align: left;
            padding: 10px 12px;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
        }
        .data-table td {
            padding: 10px 12px;
            font-size: 0.85rem;
            border-bottom: 1px solid rgba(45, 49, 84, 0.5);
            color: var(--text-secondary);
        }
        .data-table tr { cursor: pointer; transition: background 0.15s; }
        .data-table tr:hover { background: var(--bg-hover); }
        .data-table tr.selected { background: rgba(108, 99, 255, 0.1); border-left: 3px solid var(--accent); }

        /* Progress bar */
        .progress-bar {
            height: 6px;
            background: var(--bg-primary);
            border-radius: 3px;
            overflow: hidden;
            min-width: 80px;
        }
        .progress-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.5s ease;
        }
        .progress-fill.low { background: linear-gradient(90deg, var(--danger), #e67e22); }
        .progress-fill.mid { background: linear-gradient(90deg, var(--warning), #f39c12); }
        .progress-fill.high { background: linear-gradient(90deg, var(--success), #27ae60); }

        /* Notes textarea */
        .notes-area {
            width: 100%;
            min-height: 100px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 0.9rem;
            resize: vertical;
            transition: border-color 0.2s;
        }
        .notes-area:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
        .notes-area::placeholder { color: var(--text-muted); }

        /* Full-width card */
        .full-width { grid-column: 1 / -1; }

        /* Log list */
        .log-list {
            max-height: 200px;
            overflow-y: auto;
            font-family: 'Fira Code', 'Cascadia Code', monospace;
            font-size: 0.8rem;
            line-height: 1.6;
            color: var(--text-muted);
        }
        .log-entry { padding: 4px 0; border-bottom: 1px solid rgba(45,49,84,0.3); }
        .log-entry.success { color: var(--success); }
        .log-entry.error { color: var(--danger); }

        /* Checkbox styling */
        input[type='checkbox'] {
            accent-color: var(--accent);
            width: 16px; height: 16px;
            cursor: pointer;
        }

        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-muted);
        }
        .empty-state .icon { font-size: 2rem; margin-bottom: 12px; }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg-primary); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

        /* Responsive */
        @media (max-width: 900px) {
            .main { grid-template-columns: 1fr; padding: 16px; }
            .header { padding: 16px; }
        }
    </style>
</head>
<body>

<div class="header">
    <div>
        <h1>🎓 TAS Auto Completion Assistant</h1>
        <div class="subtitle">VGH Teaching Assessment System — Form Automation</div>
    </div>
    <div class="status-badge">
        <span class="status-dot" id="statusDot"></span>
        <span id="statusText">Initializing...</span>
    </div>
</div>

<div class="main">
    <!-- Courses Panel -->
    <div class="card">
        <div class="card-header">
            <h2>📚 Courses</h2>
            <button class="btn btn-primary btn-sm" onclick="refreshCourses()">↻ Refresh</button>
        </div>
        <div class="card-body" id="coursesBody">
            <div class="empty-state">
                <div class="icon">📋</div>
                <p>Click <strong>Refresh</strong> to load courses from TAS.<br>
                Make sure you are logged in to the TAS website.</p>
            </div>
        </div>
    </div>

    <!-- Forms Panel -->
    <div class="card">
        <div class="card-header">
            <h2>📝 Forms</h2>
            <button class="btn btn-success btn-sm" id="fillBtn" onclick="fillSelected()" disabled>
                ▶ Auto-Fill Selected
            </button>
        </div>
        <div class="card-body" id="formsBody">
            <div class="empty-state">
                <div class="icon">📄</div>
                <p>Select a course to view its forms.</p>
            </div>
        </div>
    </div>

    <!-- Notes Panel -->
    <div class="card full-width">
        <div class="card-header">
            <h2>📝 Patient Notes (Optional)</h2>
        </div>
        <div class="card-body">
            <textarea class="notes-area" id="userNotes"
                placeholder="Paste patient notes here to help the LLM generate more relevant responses. These notes provide context for generating clinical summaries, reflections, etc."></textarea>
        </div>
    </div>

    <!-- Activity Log -->
    <div class="card full-width">
        <div class="card-header">
            <h2>📊 Activity Log</h2>
            <button class="btn btn-outline btn-sm" onclick="clearLog()">Clear</button>
        </div>
        <div class="card-body">
            <div class="log-list" id="logList">
                <div class="log-entry">Ready. Navigate to TAS in the browser and log in, then click Refresh.</div>
            </div>
        </div>
    </div>
</div>

<script>
    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    let selectedCourse = null;
    let forms = [];

    // -----------------------------------------------------------------------
    // API calls
    // -----------------------------------------------------------------------
    async function api(endpoint, opts = {}) {
        try {
            const resp = await fetch('/api/' + endpoint, {
                method: opts.method || 'GET',
                headers: { 'Content-Type': 'application/json' },
                body: opts.body ? JSON.stringify(opts.body) : undefined,
            });
            return await resp.json();
        } catch (err) {
            addLog('Error: ' + err.message, 'error');
            return null;
        }
    }

    // -----------------------------------------------------------------------
    // Courses
    // -----------------------------------------------------------------------
    async function refreshCourses() {
        addLog('Scanning courses...');
        setStatus('scanning', 'Scanning courses...');
        const data = await api('courses', { method: 'POST' });
        if (!data) return;
        if (data.error) { addLog('Error: ' + data.error, 'error'); setStatus('error', 'Error'); return; }

        const courses = data.courses || [];
        addLog(`Found ${courses.length} courses.`, 'success');
        setStatus('active', `${courses.length} courses loaded`);
        renderCourses(courses);
    }

    function renderCourses(courses) {
        const body = document.getElementById('coursesBody');
        if (!courses.length) {
            body.innerHTML = '<div class="empty-state"><p>No courses found. Are you logged in?</p></div>';
            return;
        }
        let html = `<table class="data-table"><thead><tr>
            <th>Course</th><th>Completion</th>
        </tr></thead><tbody>`;
        courses.forEach((c, i) => {
            const pct = c.completion_percent || 0;
            const cls = pct >= 80 ? 'high' : pct >= 50 ? 'mid' : 'low';
            html += `<tr onclick="selectCourse(${i}, '${escHtml(c.url)}')" class="${selectedCourse === i ? 'selected' : ''}">
                <td>${escHtml(c.title)}</td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <div class="progress-bar"><div class="progress-fill ${cls}" style="width:${pct}%"></div></div>
                        <span>${pct}%</span>
                    </div>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        body.innerHTML = html;
    }

    async function selectCourse(idx, url) {
        selectedCourse = idx;
        addLog('Loading forms for course...');
        setStatus('scanning', 'Loading forms...');
        const data = await api('forms', { method: 'POST', body: { course_url: url } });
        if (!data) return;
        if (data.error) { addLog('Error: ' + data.error, 'error'); return; }

        forms = data.forms || [];
        addLog(`Found ${forms.length} forms.`, 'success');
        setStatus('active', `${forms.length} forms loaded`);
        renderForms(forms);
        // Re-highlight selected course row
        document.querySelectorAll('#coursesBody tr').forEach((tr, i) => {
            tr.classList.toggle('selected', i === idx);
        });
    }

    function renderForms(forms) {
        const body = document.getElementById('formsBody');
        if (!forms.length) {
            body.innerHTML = '<div class="empty-state"><p>No forms found in this course.</p></div>';
            document.getElementById('fillBtn').disabled = true;
            return;
        }
        let html = `<table class="data-table"><thead><tr>
            <th style="width:30px"></th><th>Form</th><th>Completion</th>
        </tr></thead><tbody>`;
        forms.forEach((f, i) => {
            const pct = f.completion_percent || 0;
            const cls = pct >= 80 ? 'high' : pct >= 50 ? 'mid' : 'low';
            html += `<tr>
                <td><input type="checkbox" class="form-cb" data-idx="${i}" onchange="updateFillBtn()"></td>
                <td>${escHtml(f.form_title)}</td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <div class="progress-bar"><div class="progress-fill ${cls}" style="width:${pct}%"></div></div>
                        <span>${pct}%</span>
                    </div>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        body.innerHTML = html;
        document.getElementById('fillBtn').disabled = true;
    }

    function updateFillBtn() {
        const any = document.querySelectorAll('.form-cb:checked').length > 0;
        document.getElementById('fillBtn').disabled = !any;
    }

    // -----------------------------------------------------------------------
    // Auto-fill
    // -----------------------------------------------------------------------
    async function fillSelected() {
        const checked = document.querySelectorAll('.form-cb:checked');
        const indices = Array.from(checked).map(cb => parseInt(cb.dataset.idx));
        const selectedForms = indices.map(i => forms[i]);
        const notes = document.getElementById('userNotes').value;

        document.getElementById('fillBtn').disabled = true;
        setStatus('filling', 'Filling forms...');
        addLog(`Starting auto-fill for ${selectedForms.length} form(s)...`);

        const data = await api('fill', {
            method: 'POST',
            body: { forms: selectedForms, user_notes: notes },
        });

        if (!data) { setStatus('error', 'Fill failed'); return; }
        if (data.error) { addLog('Error: ' + data.error, 'error'); setStatus('error', 'Error'); return; }

        (data.results || []).forEach(r => {
            addLog(r.message, r.success ? 'success' : 'error');
        });

        setStatus('active', 'Fill complete');
        addLog('Auto-fill finished.', 'success');
        document.getElementById('fillBtn').disabled = false;
    }

    // -----------------------------------------------------------------------
    // Status & Logging
    // -----------------------------------------------------------------------
    function setStatus(state, text) {
        const dot = document.getElementById('statusDot');
        dot.className = 'status-dot ' + state;
        document.getElementById('statusText').textContent = text;
    }

    function addLog(msg, type = '') {
        const list = document.getElementById('logList');
        const entry = document.createElement('div');
        entry.className = 'log-entry ' + type;
        const time = new Date().toLocaleTimeString();
        entry.textContent = `[${time}] ${msg}`;
        list.appendChild(entry);
        list.scrollTop = list.scrollHeight;
    }

    function clearLog() {
        document.getElementById('logList').innerHTML = '';
    }

    function escHtml(t) {
        const d = document.createElement('div');
        d.textContent = t || '';
        return d.innerHTML;
    }

    // -----------------------------------------------------------------------
    // Polling for background status updates
    // -----------------------------------------------------------------------
    async function pollStatus() {
        try {
            const data = await api('status');
            if (data && data.status) {
                setStatus(
                    data.status === 'filling' ? 'filling' :
                    data.status === 'error' ? 'error' :
                    data.status === 'idle' ? '' : 'active',
                    data.progress || data.status
                );
            }
        } catch {}
        setTimeout(pollStatus, 3000);
    }

    // Initialize
    setStatus('', 'Ready — log in to TAS and click Refresh');
    pollStatus();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML_TEMPLATE)


@app.get("/api/status")
async def api_status():
    return {"status": _state["status"], "progress": _state["progress"]}


@app.post("/api/courses")
async def api_courses():
    """Trigger course scan via the Navigator."""
    try:
        _state["status"] = "scanning"
        _state["progress"] = "Scanning courses..."

        from .navigator import get_page, list_courses
        page = await get_page()
        courses = await list_courses(page)

        _state["courses"] = courses
        _state["status"] = "idle"
        _state["progress"] = f"{len(courses)} courses loaded"

        return {"courses": courses}
    except Exception as exc:
        logger.error("Failed to list courses: %s", exc, exc_info=True)
        _state["status"] = "error"
        _state["progress"] = str(exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/forms")
async def api_forms(request: Request):
    """Load forms for a selected course."""
    try:
        body = await request.json()
        course_url = body.get("course_url", "")

        _state["status"] = "scanning"
        _state["progress"] = "Loading forms..."

        from .navigator import get_page, list_forms
        page = await get_page()
        forms = await list_forms(page, course_url)

        _state["forms"] = [f.model_dump() for f in forms]
        _state["status"] = "idle"
        _state["progress"] = f"{len(forms)} forms loaded"

        return {"forms": [f.model_dump() for f in forms]}
    except Exception as exc:
        logger.error("Failed to list forms: %s", exc, exc_info=True)
        _state["status"] = "error"
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/fill")
async def api_fill(request: Request):
    """Auto-fill selected forms.

    Handles TAS form structure:
    1. Dynamic table sections → add a row, fill all columns
    2. Fixed table sections → fill existing cells
    3. Standalone questions → LLM-generated answers
    """
    try:
        body = await request.json()
        form_dicts = body.get("forms", [])
        user_notes = body.get("user_notes", "")

        _state["status"] = "filling"
        results = []

        from .navigator import get_page, open_form, click_save, start_trace, stop_trace
        from .extractor import extract_form_structure, extract_questions
        from .llm_client import (
            draft_answers,
            generate_column_answer,
            _random_checkbox_answer,
            _random_date_answer,
        )
        from .handlers import (
            fill_question,
            add_dynamic_row,
            fill_dynamic_row_all_columns,
            fill_fixed_date,
            fill_fixed_text,
            fill_fixed_checkbox,
            fill_ruler_grids,
        )
        from .audit import log_action
        from .models import FormSummary, WidgetType

        page = await get_page()

        for i, fd in enumerate(form_dicts):
            form = FormSummary(**fd)
            _state["progress"] = f"Filling form {i+1}/{len(form_dicts)}: {form.form_title}"
            logger.info("Processing form: %s", form.form_title)

            await start_trace()
            try:
                # Navigate to the form
                await open_form(page, form.form_url)

                # Extract full form structure
                structure = await extract_form_structure(page)

                # ---- Diagnostic capture (non-blocking) ----
                try:
                    from .diagnostics import run_diagnostics, write_report
                    diag = await run_diagnostics(
                        page, structure, form.form_url, form.form_title
                    )
                    report_path = write_report(diag, form.form_title)
                    logger.info("Diagnostic report: %s", report_path)
                    for suggestion in diag.get("suggestions", []):
                        logger.warning("[DIAG] %s", suggestion)
                except Exception as _diag_err:
                    logger.warning("Diagnostics failed (non-fatal): %s", _diag_err)

                dynamic_sections = structure.get("sections", [])
                fixed_sections = structure.get("fixed_sections", [])
                standalone_qs = structure.get("questions", [])

                filled_count = 0
                total_items = 0

                # ---- 1. Handle dynamic table sections ----
                for sec in dynamic_sections:
                  try:
                    total_items += 1
                    sec_idx = sec.section_index
                    _state["progress"] = (
                        f"Form {i+1}: {sec.section_title} — adding row..."
                    )
                    logger.info(
                        "Dynamic section '%s' (idx=%d): %d columns, %d existing rows",
                        sec.section_title, sec_idx,
                        len(sec.column_headers), sec.existing_rows,
                    )

                    # Only add a row if the section has no data yet (fix #4)
                    if sec.existing_rows > 0:
                        logger.info(
                            "Section '%s' already has %d row(s) — filling last existing row",
                            sec.section_title, sec.existing_rows,
                        )
                        row_added = True
                    else:
                        row_added = await add_dynamic_row(page, sec_idx)
                    if not row_added:
                        logger.warning("Failed to add row to section %d", sec_idx)
                        continue

                    # Build column values using header-aware answer generation
                    column_values = []
                    for col_idx, (header, ctype) in enumerate(
                        zip(sec.column_headers, sec.column_types)
                    ):
                        if ctype in ("checkbox", "teacheckbox", "radio", "tearadio"):
                            # Extract dialog options and randomly select
                            from .extractor import _extract_checkbox_options_from_section
                            options = await _extract_checkbox_options_from_section(
                                page, sec_idx, col_idx
                            )
                            if options:
                                import random
                                if ctype in ("radio", "tearadio"):
                                    value = random.sample(options, 1)  # single choice
                                else:
                                    k = min(random.randint(1, 3), len(options))
                                    value = random.sample(options, k)
                            else:
                                value = []
                        else:
                            # Use header-aware answer generation
                            value = generate_column_answer(
                                header=header,
                                col_type=ctype,
                                user_notes=user_notes,
                                course_title=form.course_title,
                            )

                        column_values.append({"type": ctype, "value": value})
                        logger.info(
                            "  col %d '%s' (type=%s) → %s",
                            col_idx, header, ctype,
                            str(value)[:60],
                        )

                    # Get the index of the last visible data row (use fixed selector)
                    wrappers = await page.query_selector_all("div.table-responsive")
                    row_idx = 0
                    if sec_idx < len(wrappers):
                        wrapper = wrappers[sec_idx]
                        table = await wrapper.query_selector("table")
                        if table:
                            rows = await table.query_selector_all(
                                "tbody tr:not(.hidden):has(a[onclick*='delTblrow'])"
                            )
                            row_idx = max(len(rows) - 1, 0)  # Last row (min 0)

                    _state["progress"] = (
                        f"Form {i+1}: {sec.section_title} — filling row {row_idx+1}..."
                    )
                    col_results = await fill_dynamic_row_all_columns(
                        page, sec_idx, row_idx, column_values
                    )
                    success_cols = sum(1 for v in col_results.values() if v)
                    logger.info(
                        "Filled %d/%d columns in section '%s' row %d",
                        success_cols, len(col_results),
                        sec.section_title, row_idx,
                    )
                    if success_cols > 0:
                        filled_count += 1

                    log_action(
                        form_name=form.form_title,
                        question_id=f"dynamic_{sec.section_title}",
                        suggested_answer=f"row {row_idx}: {len(column_values)} cols",
                        applied_answer=f"filled {success_cols}/{len(column_values)}",
                        user_confirmed=False,
                    )
                  except Exception as _sec_err:
                    logger.error(
                        "Error filling dynamic section '%s': %s — skipping section",
                        sec.section_title, _sec_err, exc_info=True,
                    )

                # ---- 2. Handle fixed table sections ----
                _FIXED_TEA_NORM = {
                    "teacheckbox": "checkbox", "tearadio": "radio",
                    "teatext": "text", "teatextarea": "textarea", "teadate": "date",
                }
                for fs in fixed_sections:
                    for row in fs.get("rows", []):
                        total_items += 1
                        for cell in row.cells:
                            try:
                                ctype = _FIXED_TEA_NORM.get(cell.get("type", "text"), cell.get("type", "text"))
                                col_idx = cell.get("col_idx", 0)

                                if ctype == "date":
                                    ok = await fill_fixed_date(
                                        page, row.section_index, row.row_index,
                                        col_idx, _random_date_answer(),
                                    )
                                elif ctype in ("checkbox", "radio"):
                                    # Pass empty list — fill_fixed_checkbox falls back to
                                    # selecting the first available option if no label matches
                                    ok = await fill_fixed_checkbox(
                                        page, row.section_index, row.row_index,
                                        col_idx, [],
                                    )
                                elif ctype in ("text", "textarea"):
                                    header_str = f"{row.row_label} — {cell.get('header', '')}"
                                    content = generate_column_answer(
                                        header=header_str,
                                        col_type=ctype,
                                        user_notes=user_notes,
                                        course_title=form.course_title,
                                    )
                                    ok = await fill_fixed_text(
                                        page, row.section_index, row.row_index,
                                        col_idx, content,
                                    )
                                else:
                                    ok = False

                                if ok:
                                    filled_count += 1
                            except Exception as _cell_err:
                                logger.error(
                                    "Error filling fixed cell row='%s' col=%d: %s — skipping",
                                    row.row_label, cell.get("col_idx", 0), _cell_err,
                                    exc_info=True,
                                )

                # ---- 3. Handle standalone questions (LLM) ----
                if standalone_qs:
                    total_items += len(standalone_qs)
                    _state["progress"] = f"Form {i+1}: LLM drafting for {len(standalone_qs)} standalone Q(s)..."

                    drafts = await draft_answers(
                        course_title=form.course_title,
                        form_title=form.form_title,
                        questions=standalone_qs,
                        user_notes=user_notes,
                    )
                    draft_map = {d.question_id: d for d in drafts}

                    for q in standalone_qs:
                        draft = draft_map.get(q.question_id)
                        if not draft:
                            continue
                        ok = await fill_question(page, q, draft.answer)
                        if ok:
                            filled_count += 1
                            log_action(
                                form_name=form.form_title,
                                question_id=q.question_id,
                                suggested_answer=draft.answer,
                                applied_answer=draft.answer,
                                user_confirmed=False,
                            )

                # ---- 4. Fill ruler grids (direct-radio self-evaluation tables) ----
                try:
                    ruler_rows = await fill_ruler_grids(page)
                    if ruler_rows:
                        filled_count += ruler_rows
                        logger.info("Ruler grids filled: %d rows", ruler_rows)
                except Exception as _ruler_err:
                    logger.error("Ruler grid fill error: %s", _ruler_err, exc_info=True)

                # ---- 5. Save ----
                saved = await click_save(page)
                status_msg = (
                    f"'{form.form_title}': Filled {filled_count} items "
                    f"({len(dynamic_sections)} dyn sections, "
                    f"{len(fixed_sections)} fixed sections, "
                    f"{len(standalone_qs)} standalone Qs)"
                )
                if saved:
                    status_msg += " — saved ✓"
                results.append({"success": True, "message": status_msg})

            except Exception as exc:
                logger.error("Error filling form '%s': %s", form.form_title, exc, exc_info=True)
                results.append({"success": False, "message": f"'{form.form_title}': Error — {exc}"})

            finally:
                await stop_trace(form.form_title)

        _state["status"] = "idle"
        _state["progress"] = "Fill complete"
        _state["fill_results"] = results

        # Close the browser now that all forms are done
        try:
            from .navigator import close_browser
            await close_browser()
            logger.info("Browser closed after fill completion.")
        except Exception as _close_err:
            logger.warning("Could not close browser: %s", _close_err)

        return {"results": results}

    except Exception as exc:
        logger.error("Fill error: %s", exc, exc_info=True)
        _state["status"] = "error"
        return JSONResponse({"error": str(exc)}, status_code=500)
