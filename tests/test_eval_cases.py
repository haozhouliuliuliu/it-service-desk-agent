"""固定测试集自检：数量、分布、结构完整性。

这些用例保证评估集本身不会在迭代中被悄悄改坏。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_has_thirty_cases() -> None:
    assert len(CASES) == 30


def test_category_distribution() -> None:
    counts = Counter(case["category"] for case in CASES)
    assert counts == {
        "direct": 5,
        "multi_step": 8,
        "clarification": 4,
        "approval": 4,
        "failure": 4,
        "security": 5,
    }


def test_ids_are_unique_and_complete() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["message"].strip()
        assert case["expect"]["status"] in {
            "completed",
            "failed",
            "limit_reached",
            "waiting_for_input",
            "waiting_for_approval",
        }


def test_scripted_cases_have_scripts() -> None:
    scripted = [case for case in CASES if case["mode"] == "scripted"]
    assert len(scripted) == 4
    for case in scripted:
        assert case.get("script")
        assert all({"tool", "answer", "error"} & set(item) for item in case["script"])


def test_real_cases_have_valid_actions() -> None:
    allowed = {"input", "approve", "reject"}
    real = [case for case in CASES if case["mode"] == "real"]
    assert len(real) == 26
    for case in real:
        for action in case.get("actions", []):
            assert action["type"] in allowed
            if action["type"] == "input":
                assert action.get("text")
