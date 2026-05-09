# TAS Auto Completion Assistant

A Python tool that automates filling in teaching assessment forms on the **VGH TAS website** (https://tas.vghtpe.gov.tw/tas/).

## Features

- 📚 **Course & Form Navigator** — Lists courses and forms with completion percentages
- 🤖 **LLM-Powered Drafting** — Generates form answers using Gemini (free tier), OpenAI, or Anthropic
- 🎯 **Multi-Widget Support** — Handles radio, checkbox, textarea, Likert grids, table popups, dynamic lists
- 🛡️ **Safety Blocker** — Automatically disables "notify teacher" buttons to prevent accidental submissions
- 📊 **Audit Log** — Records every action for review
- 🌐 **Web Dashboard** — Modern dark-theme UI for selecting courses and triggering auto-fill

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your settings:

```bash
copy .env.example .env
```

Key settings:
- `LLM_API_KEY` — Your Gemini API key (get one free at https://aistudio.google.com/apikey)
- `DRY_RUN=1` — Set to test without actually filling forms

### 3. Run

```bash
python -m src.main
```

This will:
1. Open a Chromium browser window → **log in to TAS manually**
2. Open the web dashboard at http://localhost:8550
3. Click **Refresh** in the dashboard to list your courses

### 4. Auto-fill forms

1. Click a course to see its forms
2. Check the forms you want to fill
3. (Optional) Paste patient notes in the text area
4. Click **▶ Auto-Fill Selected**

## Project Structure

```
src/
├── main.py          # Entry point
├── config.py        # Settings from .env
├── models.py        # Pydantic data models
├── navigator.py     # Playwright browser automation
├── extractor.py     # DOM → question extraction
├── handlers.py      # Widget-type fill handlers
├── llm_client.py    # Multi-provider LLM API
├── safety.py        # Notify-teacher blocker
├── audit.py         # JSON-lines audit log
└── ui.py            # FastAPI web dashboard
```

## Safety

- **Notify-teacher buttons** are automatically disabled via DOM injection
- All actions are logged to `audit_log.jsonl`
- Use `DRY_RUN=1` to preview actions without applying them
