# 阶段 2.3B/C：声明式 Canonical Mapper 与离线 Fixture 验证

日期：2026-09-15
范围：让常见 object/collection 形态的新 MCP Tool 可复用受限 JSON 映射；在不联网、不读取用户凭据的条件下，以脱敏 fixture 验证 Mapper 和质量合同。

## 1. 声明式 Mapper 的真实工作方式

生产 MCP Adapter 和离线 fixture 不走两套代码：

```text
MCP result.structuredContent / content JSON
  -> extract_mcp_payload()
  -> declared_canonical mapper
  -> 最小 ExternalSource
  -> ResultQualityContract / semantic profile
```

具体实现：

- `backend/app/services/tools/result_mappers.py`
  - `map_declared_mcp_result()` 供离线 fixture 复用；
  - `map_mcp_result(mapper="declared_canonical")` 供真实 `ToolAdapterRunner` 使用；
  - 只读取已校验 JSON Pointer，不执行 Python/JS、模板或网络调用；
  - collection 最多映射声明的 `max_items`，文本做脱敏和 `max_chars` 截断；
  - `metadata.raw` 只保留 `canonical_fields` 中的非容器标量，绝不保存完整 Provider 响应；URL 仅接受 `http/https`。
- `backend/app/services/tools/adapters.py`
  - 动态 MCP 只有 Catalog 注入了已审核的 `canonical_mapper` 后，才调用声明式 Mapper。

缺根路径、item 不是对象、显示文本缺失、Mapper 损坏都会返回空 Source；不会退回为“任意 JSON 非空就是证据”。随后质量门将其判为 `invalid`。

## 2. Fixture bundle

fixture bundle 仅在本机文件或一次 API 请求内使用，格式为：

```json
{
  "format": "tool_onboarding_fixture_v1",
  "cases": [
    {
      "id": "success",
      "response": { "result": { "structuredContent": { "items": [] } } },
      "expected": {
        "quality_status": "valid",
        "source_count": { "min": 1, "max": 8 }
      },
      "request_context": { "query": "可选的已规范化请求字段" }
    },
    {
      "id": "malformed",
      "response": { "result": { "structuredContent": {} } },
      "expected": {
        "quality_status": "invalid",
        "source_count": { "min": 0, "max": 0 }
      }
    }
  ]
}
```

规则：

- bundle 的 case ID 必须和合同 `fixture_manifest.case_ids` 完全一致，canonical JSON SHA-256 必须与 `bundle_digest` 一致；
- `success` 必须期待 `valid`，`malformed`/`empty`/`request_mismatch` 必须期待 `invalid`；
- `response` 可以包含脱敏后的模拟 MCP 数据，但它不会持久化到 `McpTool`、Trace、Artifact 或审核报告；
- 报告只输出 case ID、期望/实际质量状态、Source 数、受控 quality reason、合同摘要和 fixture 摘要，不回显正文、URL、请求上下文或响应体。

## 3. 离线脚本

`backend/scripts/validate_tool_onboarding.py` 是可复现入口：

```bash
cd /disk2/gengnan/ai_web_studio
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python \
  backend/scripts/validate_tool_onboarding.py \
  --contract /path/to/contract.json \
  --fixture /path/to/fixture.json
```

退出码：`0` 表示所有样本符合预期；`1` 表示 Mapper/质量实际结果与预期不一致；`2` 表示合同、摘要、fixture 结构或路径不合法。脚本不初始化数据库、不读凭据、不发 HTTP 请求。

## 4. 已验证边界

`backend/tests/test_tool_onboarding.py` 覆盖 collection/object、JSON Pointer `~1` 转义、缺字段失败关闭、超量/敏感正文脱敏、bundle 摘要漂移、缺 case、请求不一致及 CLI 输出不含 fixture secret。`backend/tests/test_result_mappers.py` 保持内置 Tavily/高德专用 Mapper 的真实形态回归。

这不是将 LLM 作为质量门，也不是让所有 Tool 都完全无代码接入。复杂跨字段语义（例如路线、审批草案、版本化文件写入）继续保留专用 Mapper/Provider；声明式 Mapper 覆盖的是同类、低风险、只读结构化输出的高频接入路径。
