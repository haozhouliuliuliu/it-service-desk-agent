"""制度检索测试：命中、引用、排序、边界。"""

from __future__ import annotations

from it_agent.policies import PolicyIndex


def test_search_finds_laptop_policy(policy_index: PolicyIndex) -> None:
    results = policy_index.search("电脑领用制度")
    assert results
    assert results[0]["title"] == "电脑领用制度"
    assert results[0]["doc_id"].startswith("POL-IT-")


def test_search_returns_citation(policy_index: PolicyIndex) -> None:
    result = policy_index.search("新员工 电脑 领用")[0]
    assert result["citation"] == f"{result['title']} · {result['section']}"
    assert "…" not in result["citation"]


def test_search_finds_fault_sla(policy_index: PolicyIndex) -> None:
    results = policy_index.search("P1 响应时间 15 分钟")
    assert results
    text = results[0]["snippet"]
    assert "15 分钟" in text or "响应" in text
    assert results[0]["title"] == "IT 故障处理规定"


def test_search_finds_approval_flow(policy_index: PolicyIndex) -> None:
    results = policy_index.search("设备申请 审批层级")
    titles = [result["title"] for result in results]
    assert "设备申请与审批流程" in titles


def test_search_limit(policy_index: PolicyIndex) -> None:
    assert len(policy_index.search("设备", limit=2)) <= 2


def test_search_no_match(policy_index: PolicyIndex) -> None:
    assert policy_index.search("火星殖民") == []


def test_search_empty_query(policy_index: PolicyIndex) -> None:
    assert policy_index.search("   ") == []


def test_snippet_is_bounded(policy_index: PolicyIndex) -> None:
    result = policy_index.search("库存不足")[0]
    assert len(result["snippet"]) <= 160
