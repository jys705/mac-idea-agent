"""Feature 1 (c): 애매 구간 LangGraph interrupt/resume 통합 검증.

LLM 없이 작은 그래프로 검사관 Tool을 감싸 실제 interrupt 일시정지/재개를 검증한다.
"""
import math
from typing import TypedDict

import pytest
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import importlib

from src.tools import embeddings
from src.tools.app_existence_checker import app_existence_checker

# 패키지 __init__이 동명의 tool을 re-export하므로 진짜 모듈은 importlib로 얻는다.
aec = importlib.import_module("src.tools.app_existence_checker")


class _S(TypedDict, total=False):
    result: dict


def _build_graph():
    def node(state):
        res = app_existence_checker.invoke({
            "concept": "MCPurr",
            "description": "Dock에 고양이를 띄우는 메뉴바 앱",
            "core_feature": "서버 상태 표시",
        })
        return {"result": res}

    g = StateGraph(_S)
    g.add_node("check", node)
    g.add_edge(START, "check")
    g.add_edge("check", END)
    return g.compile(checkpointer=MemorySaver())


@pytest.fixture
def patch_ambiguous(monkeypatch):
    """검색 결과 + 임베더를 애매 구간(0.74)으로 고정."""
    def fake_appstore(query):
        return {"items": [], "data_source": "real_api", "endpoint": "itunes"}

    def fake_github(query):
        return {"items": [{"name": "Catdock", "description": "a cat in your dock",
                           "stars": 80, "source": "github", "url": "https://gh/catdock"}],
                "data_source": "real_api", "endpoint": "github"}

    monkeypatch.setattr(aec, "_search_appstore", fake_appstore)
    monkeypatch.setattr(aec, "_search_github", fake_github)

    def fake_embed(texts):
        out = [[1.0, 0.0]]
        for t in texts[1:]:
            s = 0.74 if "Catdock" in t else 0.0
            out.append([s, math.sqrt(1 - s * s)])
        return out

    embeddings.set_embed_fn(fake_embed)
    yield
    embeddings.set_embed_fn(None)


def _run_until_interrupt(graph, cfg):
    payload = {}
    interrupt_value = None
    for chunk in graph.stream(payload, config=cfg, stream_mode="values"):
        if isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_value = chunk["__interrupt__"][0].value
    return interrupt_value


def test_interrupt_fires_with_evidence(patch_ambiguous):
    graph = _build_graph()
    cfg = {"configurable": {"thread_id": "t-evidence"}}
    iv = _run_until_interrupt(graph, cfg)
    assert iv is not None, "애매 구간에서 interrupt가 발생해야 한다"
    assert iv["similarity_score"] == 0.74
    assert iv["concept"] == "MCPurr"
    # 근거(evidence): 앱명 + 유사도 + 겹치는 점
    assert iv["similar_apps"][0]["name"] == "Catdock"
    assert "options" in iv


def test_resume_research_triggers_loopback(patch_ambiguous):
    graph = _build_graph()
    cfg = {"configurable": {"thread_id": "t-research"}}
    _run_until_interrupt(graph, cfg)
    for _ in graph.stream(Command(resume="재탐색"), config=cfg, stream_mode="values"):
        pass
    data = graph.get_state(cfg).values["result"]["data"]
    assert data["similar_app_found"] is True
    assert data["human_decision"] == "research"
    assert data["decision_band"] == "human_confirmed"


def test_resume_proceed_continues(patch_ambiguous):
    graph = _build_graph()
    cfg = {"configurable": {"thread_id": "t-proceed"}}
    _run_until_interrupt(graph, cfg)
    for _ in graph.stream(Command(resume="그대로 진행"), config=cfg, stream_mode="values"):
        pass
    data = graph.get_state(cfg).values["result"]["data"]
    assert data["similar_app_found"] is False
    assert data["human_decision"] == "proceed"
