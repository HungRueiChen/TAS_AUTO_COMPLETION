"""Audit log module — records all assistant actions as JSON-lines."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .models import AuditEntry

logger = logging.getLogger(__name__)


def log_action(
    form_name: str,
    question_id: str,
    suggested_answer: str,
    applied_answer: str,
    user_confirmed: bool = False,
) -> None:
    """Append one audit entry to the JSON-lines log file."""
    settings = get_settings()
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        form_name=form_name,
        question_id=question_id,
        suggested_answer=suggested_answer[:500],
        applied_answer=applied_answer[:500],
        user_confirmed=user_confirmed,
    )

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")

    logger.debug("Audit: %s → %s", question_id, applied_answer[:60])


def read_log() -> list[AuditEntry]:
    """Read all audit entries from the log file."""
    settings = get_settings()
    path = Path(settings.audit_log_path)
    if not path.exists():
        return []

    entries: list[AuditEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(AuditEntry.model_validate_json(line))
            except Exception:
                pass
    return entries
