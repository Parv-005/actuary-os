import pytest

from app.llm.client import FakeLLMClient, LLMError, load_prompt, prompt_hash
from app.llm.schemas import InsightFindings, KnowledgeSummary, ReportDraft

FINDING = {
    "finding": "Construction South drives deterioration",
    "severity": "high",
    "confidence": 0.9,
    "evidence_ids": ["m1", "m2"],
    "narrative": {
        "evidence": "LR 62.9% -> 78.1% (+15.2pp)",
        "hypothesis": "severity-driven deterioration coincides with large losses",
        "conclusion": "largest contributor to portfolio movement",
    },
    "possible_drivers": ["large claims"],
    "alternatives": ["large-loss volatility", "reporting timing"],
    "human_review_required": False,
    "decision_question": "Assumption review, pricing review, or monitoring?",
}


@pytest.mark.asyncio
async def test_fake_tool_loop():
    tools_called = []

    async def executor(name, args):
        tools_called.append(name)
        return {"value": 0.673}

    client = FakeLLMClient(
        script=[
            {"tool_calls": [{"name": "segment_breakdown", "args": {"dim": "segment"}}]},
            {"tool_calls": [{"name": "top_contributors", "args": {}}]},
            {"json": FINDING},
        ]
    )
    result = await client.run_tool_loop("sys", "user", [], executor, InsightFindings)
    assert tools_called == ["segment_breakdown", "top_contributors"]
    assert result.output.finding.startswith("Construction South")
    assert result.output.confidence == pytest.approx(0.9)
    assert len(result.tool_trace) == 2


@pytest.mark.asyncio
async def test_fake_script_exhausted_raises():
    client = FakeLLMClient(script=[])
    with pytest.raises(LLMError):
        await client.json_call("s", "u", KnowledgeSummary)


@pytest.mark.asyncio
async def test_fake_json_call_schemas():
    r1 = await FakeLLMClient([{"json": FINDING}]).json_call("s", "u", InsightFindings)
    assert isinstance(r1.output, InsightFindings)
    r2 = await FakeLLMClient(
        [{"json": {"summary": "v3.1 current", "no_relevant_document": False}}]
    ).json_call("s", "u", KnowledgeSummary)
    assert r2.output.summary == "v3.1 current"
    r3 = await FakeLLMClient(
        [{"json": {"executive_summary": "LR rose to 67.3%", "open_questions": ["q1"]}}]
    ).json_call("s", "u", ReportDraft)
    assert r3.output.open_questions == ["q1"]


def test_invalid_severity_rejected():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        InsightFindings.model_validate({**FINDING, "severity": "critical"})


def test_prompt_loader():
    text = load_prompt("insight_system")
    assert "NEVER recommend an assumption value" in text
    assert load_prompt("insight_system") is text  # cached


def test_prompt_hash_stable():
    assert prompt_hash("a", "b") == prompt_hash("a", "b")
    assert prompt_hash("a") != prompt_hash("a", "b")
