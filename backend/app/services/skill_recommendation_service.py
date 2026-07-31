from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.skill_catalog import SkillCatalog


class SkillRecommendationService:
    """Conservative, explainable Skill recommender.

    Recommendations are hints only. This service never creates an installation,
    changes an enable flag, or expands a Tool allowlist. The user must select
    the returned Skill and the normal backend resolver verifies it again.
    """

    HINTS: dict[str, tuple[str, ...]] = {
        "research.web-brief": ("最新", "当前", "新闻", "政策", "版本", "调研", "来源", "核查", "事实", "网页"),
        "workspace.document-review": ("工作区", "项目文件", "文档", "资料", "审阅", "核对", "读取", "文件", "实现"),
        "travel.route-planner": ("路线", "怎么去", "驾车", "公交", "地铁", "天气", "距离", "附近", "出行"),
        "agent.artifact-review": ("运行产物", "artifact", "工具运行", "复盘", "run", "step", "失败点"),
    }

    def recommend(
        self,
        *,
        db: Any,
        user_id: str,
        project_id: str | None,
        query: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return []
        catalog = SkillCatalog()
        ready = [item for item in catalog.list_for_user(db=db, user_id=user_id, project_id=project_id) if item["is_ready"]]
        scored: list[dict[str, Any]] = []
        for item in ready:
            key = item["skill_key"]
            score = 0.0
            reasons: list[str] = []
            for hint in self.HINTS.get(key, ()):  # explicit domain hints are auditable
                if hint.lower() in normalized_query:
                    score += 0.28 if len(hint) > 1 else 0.12
                    reasons.append(f"query_hint:{hint}")
            searchable = " ".join(
                [
                    str(item.get("display_name") or ""),
                    str(item.get("description") or ""),
                    " ".join(item.get("activation_examples") or []),
                ]
            ).lower()
            ascii_terms = [term for term in re.findall(r"[a-z0-9][a-z0-9._-]{1,}", normalized_query)]
            hits = sum(1 for term in ascii_terms if term in searchable)
            if hits:
                score += min(0.18 * hits, 0.36)
                reasons.append(f"metadata_hits:{hits}")
            if score >= 0.28:
                scored.append(
                    {
                        "skill_key": key,
                        "display_name": item["display_name"],
                        "description": item["description"],
                        "score": round(min(score, 0.99), 3),
                        "reasons": reasons,
                        "requires_confirmation": True,
                        "is_ready": True,
                    }
                )
        scored.sort(key=lambda item: (-item["score"], item["skill_key"]))
        return scored[: max(1, min(int(limit), 5))]


class SkillGoldSetEvaluator:
    """Evaluates recommendation and submitted ToolPlan behavior offline.

    The gold set deliberately separates planner selection/parameter correctness
    from provider latency. A live runner can attach execution status and elapsed
    milliseconds without changing the scoring contract.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[1] / "skill_manifests" / "skill_tool_gold_set.json"
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Skill Gold Set 无法读取。") from exc
        if not isinstance(raw, list) or not raw:
            raise ValueError("Skill Gold Set 必须是非空数组。")
        self.cases = {str(item["case_id"]): item for item in raw if isinstance(item, dict) and item.get("case_id")}

    def list_cases(self) -> list[dict[str, Any]]:
        return list(self.cases.values())

    def assess_case(
        self,
        *,
        db: Any,
        user_id: str,
        project_id: str | None,
        case_id: str,
        selected_skill_key: str | None = None,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        case = self.cases.get(case_id)
        if not case:
            raise ValueError("Gold Set case 不存在。")
        expected_skill = str(case["expected_skill_key"])
        recommendations = SkillRecommendationService().recommend(
            db=db,
            user_id=user_id,
            project_id=project_id,
            query=str(case["query"]),
            limit=3,
        )
        recommended_keys = [item["skill_key"] for item in recommendations]
        expected_tools = set(case.get("expected_tool_keys") or [])
        calls = (plan or {}).get("calls") if isinstance(plan, dict) else []
        calls = calls if isinstance(calls, list) else []
        observed_tools = {str(item.get("tool_key")) for item in calls if isinstance(item, dict)}
        tool_recall = len(expected_tools.intersection(observed_tools)) / len(expected_tools) if expected_tools else 1.0
        parameter_checks: list[bool] = []
        for expected in case.get("expected_arguments") or []:
            matched = False
            for call in calls:
                if not isinstance(call, dict) or call.get("tool_key") != expected.get("tool_key"):
                    continue
                arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                matched = all(arguments.get(key) == value for key, value in (expected.get("arguments") or {}).items())
                if matched:
                    break
            parameter_checks.append(matched)
        parameter_accuracy = sum(parameter_checks) / len(parameter_checks) if parameter_checks else 1.0
        selected = selected_skill_key or (plan or {}).get("skill_key")
        execution_status = str((plan or {}).get("execution_status") or "not_run")
        return {
            "case_id": case_id,
            "expected_skill_key": expected_skill,
            "recommended_skill_keys": recommended_keys,
            "skill_recall_at_3": int(expected_skill in recommended_keys),
            "selected_skill_correct": int(selected == expected_skill),
            "expected_tool_keys": sorted(expected_tools),
            "observed_tool_keys": sorted(observed_tools),
            "tool_recall": round(tool_recall, 4),
            "parameter_accuracy": round(parameter_accuracy, 4),
            "task_success": int(execution_status == "success" and tool_recall == 1 and parameter_accuracy == 1),
            "latency_ms": (plan or {}).get("latency_ms"),
        }

    def empty_report(self) -> dict[str, Any]:
        return {
            "gold_set_version": "skill-tool-gold-v1",
            "case_count": len(self.cases),
            "metrics": {
                "skill_recommendation_recall_at_3": None,
                "tool_recall": None,
                "planner_selection_accuracy": None,
                "parameter_accuracy": None,
                "task_success_rate": None,
                "latency_ms_p50": None,
            },
            "note": "尚未提交带 execution_status 的观测计划；评测器已就绪，不会把未运行样本伪计为成功。",
        }

    def assess_batch(
        self,
        *,
        db: Any,
        user_id: str,
        project_id: str | None,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate recorded plans without silently issuing provider calls."""
        results: list[dict[str, Any]] = []
        for observation in observations:
            case_id = str(observation.get("case_id") or "") if isinstance(observation, dict) else ""
            if not case_id:
                continue
            plan = observation.get("plan") if isinstance(observation.get("plan"), dict) else {}
            results.append(
                self.assess_case(
                    db=db,
                    user_id=user_id,
                    project_id=project_id,
                    case_id=case_id,
                    selected_skill_key=(
                        str(observation["selected_skill_key"])
                        if observation.get("selected_skill_key") is not None
                        else None
                    ),
                    plan=plan,
                )
            )
        return {"results": results, "summary": self.aggregate(results)}

    @staticmethod
    def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
        if not results:
            return {
                "case_count": 0,
                "metrics": {
                    "skill_recommendation_recall_at_3": None,
                    "tool_recall": None,
                    "planner_selection_accuracy": None,
                    "parameter_accuracy": None,
                    "task_success_rate": None,
                    "latency_ms_p50": None,
                },
            }
        values = lambda key: [float(item[key]) for item in results if isinstance(item.get(key), (int, float))]
        latencies = sorted(values("latency_ms"))
        p50 = latencies[(len(latencies) - 1) // 2] if latencies else None
        return {
            "case_count": len(results),
            "metrics": {
                "skill_recommendation_recall_at_3": sum(values("skill_recall_at_3")) / len(results),
                "tool_recall": sum(values("tool_recall")) / len(results),
                "planner_selection_accuracy": sum(values("selected_skill_correct")) / len(results),
                "parameter_accuracy": sum(values("parameter_accuracy")) / len(results),
                "task_success_rate": sum(values("task_success")) / len(results),
                "latency_ms_p50": p50,
            },
        }
