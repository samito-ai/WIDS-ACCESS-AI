"""Live-Fund: Kostenbremse 0,85 $ um 3 Cent überschritten, weil nur „bisher < Budget“ VOR der Anfrage geprüft wurde.
Jetzt wird die Antwortlänge der nächsten Anfrage auf das Restbudget begrenzt – reicht es nicht, wird nichts gesendet."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.base import ChatMessage
from app.core.errors import CostLimitError
from tests.helpers import build_harness

MODEL = "claude-opus-5-5"


def _harness(tmp_path: Path, spent: float, limit: float = 1.0):  # type: ignore[no-untyped-def]
    h = build_harness(None, ["Antwort", "Antwort"], tmp_path, ai__task_cost_limit_usd=limit)
    task_id = h.storage.create_task(raw_text="t") if hasattr(h.storage, "create_task") else 1
    h.ai.task_id = task_id
    h.storage.record_usage(provider="anthropic", model=MODEL, purpose="analyse", input_tokens=1, output_tokens=1,
                           cost_usd=spent, task_id=task_id)
    return h


def _ask(h) -> None:  # type: ignore[no-untyped-def]
    h.ai.complete(messages=[ChatMessage(role="user", content="x" * 3000)], model=MODEL, purpose="analyse")


def test_answer_length_is_capped_to_the_remaining_budget(tmp_path: Path) -> None:
    h = _harness(tmp_path, spent=0.90)
    try:
        _ask(h)
        sent = h.provider.requests[-1].max_tokens
        worst = h.ai.estimate_cost(MODEL, h.ai._input_estimate(h.provider.requests[-1]), sent)
        assert sent < h.settings.ai.max_output_tokens and worst is not None and 0.90 + worst <= 1.0 + 1e-9, (sent, worst)
    finally:
        h.close()


def test_nothing_is_sent_when_the_rest_cannot_pay_for_a_useful_answer(tmp_path: Path) -> None:
    h = _harness(tmp_path, spent=0.995)
    try:
        with pytest.raises(CostLimitError, match="Restbudget"):
            _ask(h)
        assert h.provider.requests == [], "keine Anfrage gesendet"
    finally:
        h.close()


def test_without_limit_the_answer_length_stays(tmp_path: Path) -> None:
    h = _harness(tmp_path, spent=5.0, limit=0)
    try:
        _ask(h)
        assert h.provider.requests[-1].max_tokens == h.settings.ai.max_output_tokens
    finally:
        h.close()
