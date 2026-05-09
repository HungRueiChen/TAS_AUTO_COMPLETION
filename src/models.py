"""Pydantic data models for TAS Auto Completion Assistant."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WidgetType(str, Enum):
    """Types of question widgets found in TAS forms."""
    TEXT = "text"
    TEXTAREA = "textarea"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    SELECT = "select"
    LIKERT_GRID = "likert_grid"
    TABLE_POPUP = "table_popup"
    FILE_UPLOAD = "file_upload"
    DYNAMIC_LIST = "dynamic_list"
    DATE = "date"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class DomHints(BaseModel):
    """DOM-level hints that help locate a question element."""
    css_selector: str = ""
    label_text: str = ""
    aria: str = ""
    # TAS-specific: index of the table section within the form
    section_index: int = -1
    # TAS-specific: column index within the dynamic table
    column_index: int = -1


class FormSummary(BaseModel):
    """Summary of a single form within a course."""
    course_title: str
    form_title: str
    form_url: str
    completion_percent: float = 0.0
    required_instances: int = 1
    completed_instances: int = 0


class QuestionBlock(BaseModel):
    """A single question extracted from a TAS form."""
    question_id: str
    prompt: str
    required: bool = True
    widget_type: WidgetType = WidgetType.UNKNOWN
    options: list[str] = Field(default_factory=list)
    current_value: Any = None
    dom_hints: DomHints = Field(default_factory=DomHints)


class DraftResponse(BaseModel):
    """LLM-generated answer for a single question."""
    question_id: str
    answer: str
    confidence: float = 0.0


class AuditEntry(BaseModel):
    """One audit-log row."""
    timestamp: str
    form_name: str
    question_id: str
    suggested_answer: str
    applied_answer: str
    user_confirmed: bool = False


class TasDynamicSection(BaseModel):
    """Describes one dynamic-table section in a TAS form.

    These are the tables with a ⊕ add-row button, each containing
    a template row (tr.tblstd.hidden) that gets cloned.
    """
    section_title: str
    section_index: int  # 0-based index among all .table-responsive on the page
    column_headers: list[str] = Field(default_factory=list)
    #: per-column widget type inferred from onclick handlers
    column_types: list[str] = Field(default_factory=list)
    has_add_button: bool = True
    #: number of existing data rows (excluding hidden template)
    existing_rows: int = 0


class TasFixedRow(BaseModel):
    """Describes one fixed (non-removable) row in a TAS form.

    Section 三 has pre-defined rows like '醫學倫理與法律' with fixed
    cells that each contain date / text / checkbox / textarea fields.
    """
    row_label: str
    row_index: int  # 0-based index within tbody
    section_index: int  # which table section
    cells: list[dict[str, Any]] = Field(default_factory=list)
