#!/usr/bin/env python3
"""离线校验低风险只读 MCP Tool 的接入合同与脱敏 fixture。

该脚本不初始化数据库、不读取用户凭据、不发送网络请求。fixture body 只在当前
进程中交给受限 Mapper 和质量门；标准输出只含 digest、case 状态和受控原因。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.tools.onboarding import (  # noqa: E402
    ToolOnboardingContractError,
    evaluate_tool_onboarding_fixture_bundle,
    validate_tool_onboarding_contract,
    validate_tool_onboarding_fixture_bundle,
)


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolOnboardingContractError(f"无法读取{label}文件。") from exc
    except json.JSONDecodeError as exc:
        raise ToolOnboardingContractError(f"{label}文件不是合法 JSON。") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="离线校验 MCP Tool onboarding contract 与 fixture")
    parser.add_argument("--contract", required=True, type=Path, help="接入合同 JSON 文件")
    parser.add_argument("--fixture", required=True, type=Path, help="脱敏 fixture bundle JSON 文件")
    args = parser.parse_args()

    try:
        contract = validate_tool_onboarding_contract(_read_json(args.contract, label="合同"))
        bundle = validate_tool_onboarding_fixture_bundle(
            _read_json(args.fixture, label="fixture"),
            contract=contract,
        )
        report = evaluate_tool_onboarding_fixture_bundle(contract=contract, bundle=bundle)
    except ToolOnboardingContractError as exc:
        # 合同错误中没有注入 fixture/endpoint/凭据内容；仍不原样打印底层异常。
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(report.to_public_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
