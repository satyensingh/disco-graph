import json
from typing import Any

from .config import settings
from .metrics import LLMCallMetrics, response_metrics
from .models import AgentDecision, GraphQueryDecision
from .prompts import SYSTEM_PROMPT, build_turn


GRAPH_QUERY_SUMMARY_PROMPT = """
You turn repository graph retrieval JSON into a plain-English answer.
Use only the supplied JSON evidence. Do not invent code behavior.
Call out the important symbols, source files, and relationships.
If diagnostics say the graph evidence is insufficient, say what is known and mention the recommended next depth.
Keep the answer concise and useful for an engineer.
""".strip()


GRAPH_QUERY_DECISION_PROMPT = """
You control bounded repository graph retrieval for answering an engineer's question.
You receive previous graph retrieval attempts with diagnostics, nodes, and edges.
Choose exactly one action:
- answer: when the evidence is enough to answer from the supplied graph data.
- query: when another graph retrieval is needed.

When querying again, prefer the smallest useful change:
- increase depth when diagnostics recommend it or relationships look incomplete.
- reformulate the seed query when there were no lexical matches or seed nodes look unrelated.
- adjust relations/confidences only when the current filters are clearly hiding useful evidence.

Never exceed the supplied max depth or max iterations.
Do not ask to read files or run tools; this endpoint only controls graph retrieval.
Keep reasoning_summary short and user-safe.
""".strip()


GRAPH_QUERY_MULTI_SUMMARY_PROMPT = """
You answer repository graph questions in plain English from bounded graph retrieval attempts.
Use only the supplied attempts as evidence. Do not invent behavior or files.
Prefer the latest/broadest successful attempt, but include useful evidence from earlier reformulations.
Name the important symbols, source files, and relationships.
If the evidence remains weak, say that directly and state the most useful next graph depth or query.
Keep the answer concise and useful for an engineer.
""".strip()


class LLM:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is not installed. Run pip install -r requirements.txt") from exc
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.measurements: list[LLMCallMetrics] = []

    def decide(self, task: str, history: str) -> AgentDecision:
        kwargs = {
            "model": settings.openai_model,
            "instructions": SYSTEM_PROMPT,
            "input": build_turn(task, history),
            "text_format": AgentDecision,
        }
        # Newer reasoning models accept this knob. Keeping it configurable lets users
        # trade latency/cost for harder repository tasks without changing code.
        if settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = self.client.responses.parse(**kwargs)
        self._record("agent_decide", response, settings.openai_model)
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("Model returned no structured decision")
        return parsed

    def summarize_graph_query(self, query: str, graph_result: dict[str, Any]) -> str:
        kwargs = {
            "model": settings.openai_model,
            "instructions": GRAPH_QUERY_SUMMARY_PROMPT,
            "input": (
                f"User question:\n{query}\n\n"
                "Graph retrieval JSON:\n"
                f"{json.dumps(graph_result, indent=2, ensure_ascii=False)}"
            ),
        }
        if settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = self.client.responses.create(**kwargs)
        self._record("graph_query_summary", response, settings.openai_model)
        text = getattr(response, "output_text", None)
        if not text:
            text = self._extract_response_text(response)
        if not text.strip():
            raise RuntimeError("Model returned no answer text")
        return text.strip()

    def decide_graph_query_next(
        self,
        question: str,
        attempts: list[dict[str, Any]],
        max_depth: int,
        max_iterations: int,
    ) -> GraphQueryDecision:
        kwargs = {
            "model": settings.openai_model,
            "instructions": GRAPH_QUERY_DECISION_PROMPT,
            "input": json.dumps(
                {
                    "question": question,
                    "max_depth": max_depth,
                    "max_iterations": max_iterations,
                    "attempts": attempts,
                },
                indent=2,
                ensure_ascii=False,
            ),
            "text_format": GraphQueryDecision,
        }
        if settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = self.client.responses.parse(**kwargs)
        self._record("graph_query_decide", response, settings.openai_model)
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("Model returned no graph query decision")
        return parsed

    def summarize_graph_query_attempts(self, query: str, attempts: list[dict[str, Any]]) -> str:
        kwargs = {
            "model": settings.openai_model,
            "instructions": GRAPH_QUERY_MULTI_SUMMARY_PROMPT,
            "input": json.dumps(
                {
                    "question": query,
                    "attempts": attempts,
                },
                indent=2,
                ensure_ascii=False,
            ),
        }
        if settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = self.client.responses.create(**kwargs)
        self._record("graph_query_attempt_summary", response, settings.openai_model)
        text = getattr(response, "output_text", None)
        if not text:
            text = self._extract_response_text(response)
        if not text.strip():
            raise RuntimeError("Model returned no answer text")
        return text.strip()

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts)

    def consume_measurements(self) -> list[LLMCallMetrics]:
        measurements = list(self.measurements)
        self.measurements.clear()
        return measurements

    def _record(self, purpose: str, response: Any, model: str) -> None:
        self.measurements.append(
            response_metrics(
                response,
                purpose=purpose,
                model=model,
                input_cost_per_million=settings.openai_input_cost_per_million,
                output_cost_per_million=settings.openai_output_cost_per_million,
            )
        )
