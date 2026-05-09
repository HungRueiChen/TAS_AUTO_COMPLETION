"""LLM client — multi-provider wrapper (Gemini / OpenAI / Anthropic) for drafting answers.

Checkbox and date questions are handled locally (random selection / placeholder
dates) rather than sent to the LLM, because the structured constraint-based
responses are hard for the LLM to get right reliably.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import get_settings
from .models import DraftResponse, QuestionBlock, WidgetType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (from spec §8.1)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior medical education assistant at Taipei Veterans General Hospital.
Your task is to automatically fill in the forms to test the Teaching Assessment System (TAS).

Instructions:
1) Use English or Traditional Chinese (Taiwan) with standard medical English abbreviations (e.g., DM, HTN, s/p).
2) Maintain a professional, reflective tone.
3) Ensure text answers are around 300 characters (for textarea / reflection fields).
4) Synthesize reasonable clinical details based on the provided specialty and user notes (if any) to create a complete case summary.
5) End reflections with a section on "Core Competencies" (核心能力) gained.
6) For radio/likert questions, choose the most reasonable option.
7) Return ONLY valid JSON — no markdown fences, no commentary.
"""

# Widget types that should be handled locally (not sent to LLM)
_LOCAL_WIDGET_TYPES = {
    WidgetType.CHECKBOX,
    WidgetType.DATE,
    WidgetType.FILE_UPLOAD,
    WidgetType.DYNAMIC_LIST,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def draft_answers(
    course_title: str,
    form_title: str,
    questions: list[QuestionBlock],
    user_notes: str = "",
) -> list[DraftResponse]:
    """Send questions to the LLM and get structured draft answers.

    Returns a list of DraftResponse objects, one per answerable question.

    Checkbox, date, and file_upload questions are handled locally —
    only text, textarea, radio, and likert go to the LLM.
    """
    if not questions:
        return []

    # Split into LLM-eligible and locally-handled questions
    llm_questions = [q for q in questions if q.widget_type not in _LOCAL_WIDGET_TYPES]
    local_questions = [q for q in questions if q.widget_type in _LOCAL_WIDGET_TYPES]

    # Generate local answers for checkbox, date, etc.
    local_drafts = _generate_local_answers(local_questions)

    # Call LLM for text/textarea/radio/likert if available
    settings = get_settings()
    if llm_questions and settings.llm_api_key:
        prompt = _build_prompt(course_title, form_title, llm_questions, user_notes)
        try:
            raw = await _call_llm(prompt)
            llm_drafts = _parse_response(raw, llm_questions)
        except Exception as exc:
            logger.error("LLM call failed: %s — using placeholders", exc)
            llm_drafts = _placeholder_answers(llm_questions)
    elif llm_questions:
        logger.warning("No LLM API key — generating placeholder answers")
        llm_drafts = _placeholder_answers(llm_questions)
    else:
        llm_drafts = []

    all_drafts = llm_drafts + local_drafts
    logger.info(
        "Draft answers: %d from LLM, %d local (checkbox/date), %d total",
        len(llm_drafts), len(local_drafts), len(all_drafts),
    )
    return all_drafts


# ---------------------------------------------------------------------------
# Local answer generation (checkbox, date, etc.)
# ---------------------------------------------------------------------------


def _generate_local_answers(questions: list[QuestionBlock]) -> list[DraftResponse]:
    """Generate answers locally for checkbox, date, file_upload questions."""
    drafts: list[DraftResponse] = []

    for q in questions:
        if q.widget_type == WidgetType.FILE_UPLOAD:
            continue  # Intentionally skip

        if q.widget_type == WidgetType.CHECKBOX:
            answer = _random_checkbox_answer(q)
        elif q.widget_type == WidgetType.DATE:
            answer = _random_date_answer()
        elif q.widget_type == WidgetType.DYNAMIC_LIST:
            answer = "已完成"
        else:
            answer = "已完成"

        drafts.append(DraftResponse(
            question_id=q.question_id,
            answer=answer,
            confidence=0.6,
        ))

    return drafts


def _random_checkbox_answer(q: QuestionBlock) -> str:
    """Randomly select 1–3 options from a checkbox question's options list."""
    if not q.options:
        return "N/A"

    # Pick between 1 and min(3, len(options)) random options
    k = min(random.randint(1, 3), len(q.options))
    selected = random.sample(q.options, k)
    return ",".join(selected)


def _random_date_answer() -> str:
    """Generate a random recent date string in YYYY/MM/DD format."""
    # Pick a date within the last 30 days
    days_ago = random.randint(1, 30)
    d = datetime.now() - timedelta(days=days_ago)
    return d.strftime("%Y/%m/%d")


def generate_column_answer(
    header: str,
    col_type: str,
    user_notes: str = "",
    course_title: str = "",
) -> str | list[str]:
    """Generate a context-aware answer for a dynamic table column.

    Uses the column header text to produce clinically relevant content
    instead of generic placeholders. Column types like 'checkbox' return
    lists; others return strings.

    Args:
        header: Column header text (e.g. '病歷號', '診斷', '個案摘要')
        col_type: Column type string (e.g. 'date', 'checkbox', 'text', 'textarea')
        user_notes: User notes for additional context
        course_title: Course title for specialty context
    """
    h = header.strip()

    # Date columns
    if col_type == "date":
        return _random_date_answer()

    # Checkbox columns — return list
    if col_type in ("checkbox", "teacheckbox"):
        return []  # Caller should extract options and randomly select

    # ---- Text / Textarea columns: header-based answer generation ----

    # Chart / medical record ID (病歷號)
    if _header_matches(h, ["病歷號", "chart", "histno", "病歷"]):
        chart_id = random.randint(10000000, 51999999)
        return str(chart_id)

    # Diagnosis (診斷)
    if _header_matches(h, ["診斷", "diagnosis", "Dx"]):
        diagnoses = [
            "Type 2 DM with diabetic nephropathy, stage 3 CKD",
            "Colon cancer, stage IIIB (T3N1M0), s/p right hemicolectomy",
            "Essential HTN with grade II diastolic dysfunction",
            "Community-acquired pneumonia, severe (CURB-65 score 3)",
            "Acute appendicitis with perforation and localized peritonitis",
            "ESRD on regular HD, s/p AVF creation, secondary hyperparathyroidism",
            "HCC, BCLC stage B, s/p TACE x2",
            "Acute ST-elevation MI (LAD territory), s/p primary PCI with DES",
            "SLE with class IV lupus nephritis, steroid-dependent",
            "Gastric ulcer, Forrest IIa, H. pylori positive, s/p endoscopic hemostasis",
        ]
        return random.choice(diagnoses)

    # Case summary / description (個案摘要)
    if _header_matches(h, ["個案摘要", "摘要", "case", "summary", "病例", "簡述"]):
        summaries = [
            "68歲男性，有高血壓、糖尿病及慢性腎病史，因呼吸困難及下肢水腫入院。檢查發現心衰竭急性惡化(NYHA IV)，BNP 2340 pg/mL。予以利尿劑、ACE inhibitor調整，經治療後症狀改善出院。",
            "54歲女性，主訴右上腹痛2天伴噁心嘔吐。超音波顯示膽囊結石伴急性膽囊炎。WBC 14,500，CRP 12.5。經抗生素治療後行腹腔鏡膽囊切除術，術後恢復良好。",
            "72歲男性，大腸鏡發現升結腸3cm腫瘤，切片證實為adenocarcinoma。CT顯示局部淋巴結腫大，無遠端轉移。安排行腹腔鏡右半結腸切除術。",
            "45歲男性，因突發胸痛及冒冷汗送急診。ECG顯示V1-V4 ST elevation，Troponin-I 15.6 ng/mL。緊急行冠狀動脈攝影顯示LAD complete occlusion，予以primary PCI及DES置放。",
        ]
        answer = random.choice(summaries)
        if user_notes:
            answer += f"\n備註：{user_notes[:100]}"
        return answer

    # Discussion content (討論內容)
    if _header_matches(h, ["討論", "discussion", "討論內容"]):
        discussions = [
            "針對此病例，討論了鑑別診斷的思路、影像判讀要點及治療計畫的擬定。學習如何整合臨床症狀與實驗室數據做出診斷，並了解治療的優先順序與追蹤計畫。",
            "與主治醫師討論病人的治療策略，包括藥物選擇、手術時機及術後照護。學習到 evidence-based medicine 在臨床決策中的重要性。",
            "討論了此個案的病理生理機轉、診斷流程及最新治療指引。透過文獻回顧加深了對疾病的認識。",
        ]
        return random.choice(discussions)

    # Reflection / learning (心得, 反思)
    if _header_matches(h, ["心得", "反思", "reflection", "學習", "learning", "自我反思"]):
        reflections = [
            "透過此次臨床經驗，深刻體會到病史詢問與身體檢查的重要性。在主治醫師的指導下，學習了如何有系統地分析病人問題並提出合理的診療計畫。核心能力：病人照護、醫學知識、從工作中學習及成長。",
            "本次學習讓我了解到團隊合作在病人照護中的關鍵角色。從病房到手術室，每個環節都需要良好的溝通與協調。同時加深了對術前評估和術後照護的認識。核心能力：人際關係及溝通技巧、專業素養。",
            "此次輪訓中，學習了多種檢查的適應症與判讀方法，並在臨床教師指導下親自操作。體會到持續學習與反覆練習對臨床技能提升的重要性。核心能力：醫學知識、制度下之臨床工作、從工作中學習及成長。",
        ]
        return random.choice(reflections)

    # Location / place (地點)
    if _header_matches(h, ["地點", "location", "place", "場所"]):
        locations = ["一般外科門診", "內科病房12B", "急診醫學部", "心臟內科門診", "外科加護病房"]
        return random.choice(locations)

    # Department / specialty (科別)
    if _header_matches(h, ["科別", "科", "department", "specialty", "section"]):
        if course_title:
            # Try to extract specialty from course title
            for dept in ["內科", "外科", "骨科", "神經科", "心臟", "急診", "兒科", "婦產科", "眼科", "皮膚科", "耳鼻喉科", "泌尿科", "精神科"]:
                if dept in course_title:
                    return dept
        depts = ["一般外科", "內科部", "心臟內科", "胸腔外科", "急診醫學科", "神經內科"]
        return random.choice(depts)

    # Doctor / teacher name (醫師)
    if _header_matches(h, ["醫師", "doctor", "teacher", "教師", "指導"]):
        return "王大明"

    # Number / count fields (次數, 數量)
    if _header_matches(h, ["次數", "數量", "count", "number", "人次"]):
        return str(random.randint(1, 5))

    # EDU code (EDU課程代碼)
    if _header_matches(h, ["EDU", "課程代碼", "代碼"]):
        d = datetime.now() - timedelta(days=random.randint(1, 30))
        return d.strftime("%Y%m%d")

    # Age (年齡)
    if _header_matches(h, ["年齡", "age"]):
        return str(random.randint(30, 85))

    # Gender / sex (性別)
    if _header_matches(h, ["性別", "sex", "gender"]):
        return random.choice(["男", "女"])

    # Time / hours (時間, 小時)
    if _header_matches(h, ["時間", "小時", "hour", "time"]):
        return str(random.choice([1, 1.5, 2, 2.5, 3]))

    # Fallback for textarea-type columns
    if col_type in ("textarea", "teatextarea"):
        return (
            "本次學習經驗豐富，在臨床指導下學習了病史詢問、"
            f"身體檢查及診療計畫。{'（' + user_notes[:50] + '）' if user_notes else ''}"
            "核心能力：病人照護、醫學知識、從工作中學習及成長。"
        )

    # Fallback for text columns
    return "已完成"


def _header_matches(header: str, keywords: list[str]) -> bool:
    """Check if any keyword is contained in the header text."""
    h = header.lower()
    return any(kw.lower() in h for kw in keywords)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(
    course_title: str,
    form_title: str,
    questions: list[QuestionBlock],
    user_notes: str,
) -> str:
    """Construct the user prompt sent to the LLM."""
    q_list = []
    for q in questions:
        entry: dict[str, Any] = {
            "question_id": q.question_id,
            "prompt": q.prompt,
            "widget_type": q.widget_type.value,
        }
        if q.options:
            entry["options"] = q.options
        q_list.append(entry)

    payload = {
        "course_title": course_title,
        "form_title": form_title,
        "questions": q_list,
    }
    if user_notes:
        payload["user_notes"] = user_notes

    return (
        f"Please generate answers for the following TAS form questions.\n"
        f"Return a JSON array of objects with keys: question_id, answer, confidence.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


# ---------------------------------------------------------------------------
# LLM provider dispatch
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
)
async def _call_llm(prompt: str) -> str:
    """Call the configured LLM provider and return the raw text response."""
    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "gemini":
        return await _call_gemini(prompt)
    elif provider == "openai":
        return await _call_openai(prompt)
    elif provider == "anthropic":
        return await _call_anthropic(prompt)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


async def _call_gemini(prompt: str) -> str:
    """Call Google Gemini API."""
    settings = get_settings()
    model = settings.llm_model or "gemini-2.0-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={settings.llm_api_key}"
    )

    body = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    # Extract text from Gemini response
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected Gemini response structure: %s", data)
        raise ValueError("Failed to parse Gemini response") from exc


async def _call_openai(prompt: str) -> str:
    """Call OpenAI-compatible API."""
    settings = get_settings()
    url = "https://api.openai.com/v1/chat/completions"

    body = {
        "model": settings.llm_model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


async def _call_anthropic(prompt: str) -> str:
    """Call Anthropic Claude API."""
    settings = get_settings()
    url = "https://api.anthropic.com/v1/messages"

    body = {
        "model": settings.llm_model or "claude-3-haiku-20240307",
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=body,
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_response(raw: str, questions: list[QuestionBlock]) -> list[DraftResponse]:
    """Parse LLM JSON response into DraftResponse objects.

    Handles multiple response formats and does fuzzy question_id matching
    to cope with LLM returning slightly different IDs.
    """
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.error("LLM returned non-JSON response: %s", text[:500])
        return _placeholder_answers(questions)

    # Handle both array and nested object formats
    items = parsed
    if isinstance(parsed, dict):
        # Try common wrapper keys
        for key in ("answers", "responses", "questions", "results", "data"):
            if key in parsed and isinstance(parsed[key], list):
                items = parsed[key]
                break
        else:
            # Might be a single item
            if "question_id" in parsed:
                items = [parsed]
            else:
                logger.error("LLM response has unrecognized structure: %s", list(parsed.keys()))
                return _placeholder_answers(questions)

    if not isinstance(items, list):
        logger.error("LLM response is not a list: %s", type(items))
        return _placeholder_answers(questions)

    # Build a lookup of expected question IDs
    expected_ids = {q.question_id for q in questions}

    # Try to match each item to a question
    drafts: list[DraftResponse] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        qid = item.get("question_id", "")
        answer = item.get("answer", "")
        confidence = item.get("confidence", 0.7)

        if not answer:
            continue

        # Exact match first
        if qid in expected_ids:
            drafts.append(DraftResponse(
                question_id=qid,
                answer=str(answer),
                confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.7,
            ))
            continue

        # Fuzzy: if the LLM returned a different ID format, try to match
        # by position or by containment
        for eid in expected_ids:
            if eid in qid or qid in eid:
                drafts.append(DraftResponse(
                    question_id=eid,
                    answer=str(answer),
                    confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.5,
                ))
                break

    # If we got some matches, use them. Otherwise fall back to placeholders.
    if drafts:
        logger.info("Parsed %d/%d LLM draft answers", len(drafts), len(questions))
        return drafts

    # Fallback: if no IDs matched at all, assign by position
    logger.warning(
        "No question IDs matched — assigning LLM answers by position "
        "(got %d answers for %d questions)", len(items), len(questions)
    )
    for q, item in zip(questions, items):
        if not isinstance(item, dict):
            continue
        answer = item.get("answer", "")
        if answer:
            drafts.append(DraftResponse(
                question_id=q.question_id,
                answer=str(answer),
                confidence=0.4,
            ))

    if drafts:
        return drafts

    return _placeholder_answers(questions)


def _placeholder_answers(questions: list[QuestionBlock]) -> list[DraftResponse]:
    """Generate simple placeholder answers when no LLM is available."""
    drafts: list[DraftResponse] = []
    for q in questions:
        if q.widget_type == WidgetType.FILE_UPLOAD:
            continue
        if q.widget_type in (WidgetType.RADIO, WidgetType.CHECKBOX):
            answer = q.options[0] if q.options else "N/A"
        elif q.widget_type == WidgetType.LIKERT_GRID:
            answer = "4"  # Default to high-ish rating
        elif q.widget_type in (WidgetType.TEXTAREA, WidgetType.TABLE_POPUP):
            answer = (
                "本次學習經驗豐富，在臨床指導下學習了病史詢問、身體檢查及診療計畫之擬定。"
                "透過與病患的互動，提升了溝通技巧與同理心，並加深了對常見疾病的認識。"
                "核心能力：病人照護、醫學知識、從工作中學習及成長。"
            )
        elif q.widget_type == WidgetType.DATE:
            answer = _random_date_answer()
        else:
            answer = "已完成"
        drafts.append(DraftResponse(
            question_id=q.question_id,
            answer=answer,
            confidence=0.5,
        ))
    return drafts
