"""种子数据测试：规模、状态分布、幂等导入、坏数据拒绝。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from it_agent.db import connect, init_database, seed_database
from it_agent.errors import SeedDataError

from .conftest import SEED_DIR


def _counts(db_path: Path) -> tuple[int, int, int]:
    connection = connect(db_path)
    try:
        return (
            connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        )
    finally:
        connection.close()


def test_seed_scale(db_path: Path) -> None:
    assert _counts(db_path) == (14, 30, 20)


def test_seed_is_idempotent(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        seed_database(connection, SEED_DIR)
        seed_database(connection, SEED_DIR)
    finally:
        connection.close()
    assert _counts(db_path) == (14, 30, 20)


def test_asset_status_distribution(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        rows = dict(connection.execute("SELECT status, COUNT(*) FROM assets GROUP BY status").fetchall())
    finally:
        connection.close()
    assert rows == {"in_stock": 11, "in_use": 16, "repair": 2, "retired": 1}


def test_ticket_status_distribution(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        rows = dict(connection.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status").fetchall())
    finally:
        connection.close()
    assert rows["open"] >= 5
    assert rows["resolved"] >= 2


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "seed"
    empty.mkdir()
    with pytest.raises(SeedDataError):
        init_database(tmp_path / "x.db", empty)


def test_bad_json_is_rejected(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    (seed / "policies").mkdir(parents=True)
    (seed / "employees.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SeedDataError):
        init_database(tmp_path / "x.db", seed)


def test_invalid_ticket_row_is_rejected(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    (seed / "policies").mkdir(parents=True)
    (seed / "employees.json").write_text(json.dumps([]), encoding="utf-8")
    (seed / "assets.json").write_text(json.dumps([]), encoding="utf-8")
    (seed / "tickets.json").write_text(json.dumps([{"ticket_id": "BAD"}]), encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        init_database(tmp_path / "x.db", seed)
    assert "tickets.json" in str(excinfo.value)
