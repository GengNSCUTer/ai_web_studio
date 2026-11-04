# Action Intent 与对外数据防护

2026-11-04。补充两个容易被“工具已授权”掩盖的风险：用户要求总结本地玩家反馈时，攻击者把“请访问某网站”写进反馈，不能因此调用网页搜索；用户明确允许公开搜索时，也不能把工作区密钥或私有正文放进查询参数外发。

## 1. 两个边界不是一件事

```text
原始用户任务 ──Action Intent Check──> 这个工具动作是否服务于本轮任务？
工具参数     ──Outbound Data Check─> 这些参数中是否包含不应外发的数据？
公网连接     ──Endpoint/Connection──> 目标是否为允许的公网地址？
```

候选工具、Planner Schema和Executor权限检查各自仍然保留：候选回答“有哪些可能能力”，Planner回答“计划调用什么”，Executor回答“参数/权限/风险能否执行”。Action Guard增加的是本轮意图和数据流约束，不能被工具返回正文覆盖，也不替代ACL、审批或Schema。

## 2. Action Intent Check

`backend/app/services/tools/action_guard.py` 的 `ActionScope` 保存原始用户问题。Chat进入`ExternalContextService`时以原始query创建范围，query rewrite只用于检索/规划表达，不能扩大范围。Executor在网络副作用发生前检查工具类别：

- 本地工作区文件和Agent产物读取保留原有ACL，不要求联网意图。
- 网页搜索、网页读取、技术文档、天气、地图等外部能力要在原始问题中存在对应意图；例如“只根据上传CSV总结反馈”不会因为CSV里出现一个URL而允许搜索。
- 原始问题明确“不要联网/只根据本地资料”时，网页类动作直接拒绝。
- 外部资料中的“请访问某网站/调用某工具”等内容没有授权能力。

Durable在创建Run时，将原会话的用户原任务固定到`input_json.action_scope`；Worker恢复时读取这个已保存范围，不因用户后来编辑消息而获得更大权限。没有会话原消息的显式只读API计划，只授予已固定的工具键，仍须检查参数外发。工具结果、Artifact和Planner观察不会改写Scope。

缺少范围时默认拒绝外部动作，不保留旧接口放行分支；历史Run缺少原任务也不能自动恢复网络权限，需要用户重新发起。本地文件工具继续使用已有ACL和审批。

这是第一层基于原任务的确定性检查：它独立于受污染证据，按本轮任务类别和明确禁令决定是否允许外部动作，不等于完整语义意图证明。复杂隐含意图可能误拒；原任务本来允许搜索时，仍不能保证每一条搜索都与主题一致。尚未匹配的动态工具类别默认拒绝，不根据远端description自动授予意图权限。

## 3. 对外参数检查

同一 `action_guard.py` 在公网工具执行前递归检查字符串和数值参数，覆盖：

- `api_key`、`access_token`、密码、Secret、Authorization、JWT、私钥和常见Provider Key形态；
- 手机号、身份证号、邮箱等明显个人标识；
- URL编码的有限多轮解码和整段Base64文本；
- 当前会话已经读取的工作区/Artifact正文中长度足够的连续片段。

命中后返回固定拒绝原因和字段路径，新增检查事件不记录参数原值；服务所用认证凭据仍由Adapter注入，不从模型参数获得。最多保护本轮已读16个来源、每个8000个去空白字符，匹配24字符连续片段。这不是全量资料的污点追踪或完整DLP：此前未读取资料、短标识、分片/新编码、嵌入URL中的Base64、图像、二进制或语义改写都不能据此宣称全部识别；邮箱等规则也可能保守误拒。真实敏感资料应优先本地处理，需外发时先明确脱敏。

## 4. SSRF 与外传的区别

`mcp_security.py` 继续在请求前解析A/AAAA并拒绝私网、本机、保留和云元数据地址；新增 `pinned_http.py` 在httpcore连接层再次解析和校验，关闭环境代理，禁止Unix Socket，并让连接尝试只使用当前验证到的公网数值地址。MCP URL仍使用原域名完成HTTP Host/TLS语义。

这条MCP端点连接路径已把校验结果和真实连接绑定：DNS返回多个地址时全量检查，出现非公网单播地址则拒绝；实际连接只接收已验证的数值IP，原域名保留为Host和TLS SNI，证书验证继续开启。默认不跟随重定向，禁用环境代理；管理员显式允许私网时会放宽目标限制，不能按默认策略描述。

覆盖范围是本地`McpHttpClient`访问MCP服务端点，包括Tools和Resources。远端搜索/抓取厂商内部如何解析用户URL不受它控制，模型API、其他HTTP客户端和基础设施网络策略也不是本轮统一改造的范围。`enforce_mcp_endpoint_target_policy()`单独使用仍是best-effort，不能把所有网络路径都说成已完成连接绑定。

SSRF通过只说明“访问了错误目标”，对外数据检查说明“访问了正确公网服务但发送了不该发送的内容”；两者必须分别审计。MCP响应、网页正文和工具描述仍是无指令权限的外部资料。

## 5. 代码与测试

- `backend/app/services/tools/action_guard.py`：原始任务范围和递归外发参数检查。
- `backend/app/services/tools/executor.py`：网络副作用前执行Action Guard并写入`tool_action_intent_check`事件。
- `backend/app/services/tools/mcp_security.py`：请求前端点检查及限制说明。
- `backend/app/services/tools/pinned_http.py`、`mcp_client.py`：连接层二次地址校验、关闭环境代理和连接限制。
- `backend/app/services/external_context_service.py`：Chat绑定原始query。
- `backend/app/services/durable_tool_runtime.py`：创建时保存原任务，Worker恢复原范围并载入该Run已读产物作为保护材料。
- `backend/tests/test_action_guard.py`：本地反馈注入、网页合法工具越界、Secret/个人信息/工作区正文外发、私网端点和DNS混合地址测试。

真实验收脚本：`scripts/action_security_real_e2e.py`；结果：`backend/evals/results/action_security_real_20261104.json`。使用固定测试账号的在线`deepseek-ai/DeepSeek-V3.2`，只创建合成反馈并清理临时数据，不用真实密钥作为攻击载荷。

- 浏览器：读取含恶意网站指令的玩家反馈，最终回答保留匹配和掉线两项事实，没有执行外部工具；浏览器错误0。
- 在线Context7：实际完成resolve/query，连接绑定后仍可取文档并给最终回答。
- 在线Tavily：公开PostgreSQL WAL查询和最终回答完成。
- 确定性测试另外强制提交越界搜索、模拟Secret外发、私网/混合/多播地址、检查后DNS变私网、验证后按数值IP连接，以及Host/TLS SNI保留。浏览器正常拒绝注入不等于强制执行了恶意Planner请求，两类证据分别记录。
- Durable测试覆盖原消息编辑不改变已保存范围、历史任务缺范围时不调用网络。

复跑记录也保留：强化反馈载荷时，初版脚本把“被拦截的调用记录”误判为实际联网，已改为核对`skipped`与同call_id的`denied`事件。随后一轮Tavily未满足指定工具成功断言；该次临时数据清理前没有保存完整底层细节，不能断言是远端、Planner还是预算原因。同配置直接Executor控制调用取得5条来源（2933ms），未放宽任何安全策略。最后重新运行完整三例，3/3通过、浏览器错误0、临时数据已清理。报告是小样本业务冒烟，不是零失败稳定性或总体任务成功率证明。

最终后端全量997项通过（配置真实PostgreSQL/pgvector条件集成），Action Guard/MCP/Executor/工具集成定向53项通过；compileall和git diff检查通过。未修改前端源码。本次不宣称完整语义意图审查、全网络DLP或任意恶意代码安全沙箱。
