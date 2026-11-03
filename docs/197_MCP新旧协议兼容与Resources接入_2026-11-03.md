# MCP新旧协议兼容与Resources接入

2026-11-03。原客户端仅实现2025-03-26初始化、tools/list和tools/call；本次增加2026-07-28逐请求协议兼容及用户显式选择的文本Resources问答。Prompts不在本轮范围，不宣称完整实现所有MCP特性。

## 一、协议如何选择

每次公共操作先用无副作用的server/discover探测。新版每个请求携带_meta中的协议版本、客户端信息和能力，HTTP头同步MCP-Protocol-Version、Mcp-Method及需要时的Mcp-Name；不初始化、不发送或保存协议Session。非ASCII、控制字符和Base64标记歧义按规范编码请求头。

只有探测阶段收到明确的旧版兼容响应，才进入initialize → 校验返回版本 → notifications/initialized → 业务请求。支持2025-03-26、2025-06-18、2025-11-25；通常以原2025-03-26发起旧握手，服务器返回值必须在支持集合。旧Session是可选的，只在旧路径回传，不能把它当用户会话或Durable状态。

新版专用协议错误不盲目降级；UnsupportedProtocolVersion只选择明确共同支持的版本，矛盾声明或无交集直接失败。401/403、429、超时、5xx不会触发协议降级或重放工具。实际tools/call不透明重试，不因业务失败换协议再执行。

分页有页数/数量/游标循环限制，JSON/SSE均限制总响应大小。SSE跳过进度通知、匹配请求ID、拼接同事件多行data后读取最终响应；整个RPC有截止时间。input_required及未支持resultType明确失败，不把中间结果当正常事实，不自动执行MRTR交互。新版工具Schema的x-mcp-header按静态properties路径提取，验证类型/唯一性/编码；非法声明不进入发现结果。旧isError和outputSchema/structuredContent检查保留。

当前仍是Streamable HTTP客户端，不支持stdio或2024-11-05独立GET/SSE传输。请求级SSE响应与旧GET/SSE不是同一件事，设置页提示已纠正。每个客户端操作重新发现协议，有额外发现开销；未引入跨用户的协议或授权缓存。

官方依据：[版本兼容](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)、[发现](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)、[HTTP绑定](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)。

## 二、Resources能怎样使用

用户在设置中心配置一个确实声明Resources能力的MCP服务，启用后到问答台打开“MCP资料”，选择服务、浏览列表、预览文本，再勾选最多5份资料发送问题。仅提供Tools的服务会显示不支持Resources，不自动生成资源或安装服务。

服务端重新校验用户、项目作用域、服务启用和凭据，确认URI仍在当前调用者可见的resources/list中，再通过原MCP端点执行resources/read。资源URI是远端标识：file://、resource://或https://都不转成宿主路径，不直接发任意HTTP请求。未提供通用资源写入、删除或本机文件访问能力。资源预览不是持久授权，发送/重生成都会重新读取；远端内容变化后保留本次实际输入，不承诺一直与远端最新内容相同。

首版读取文本类型，拒绝二进制、空正文、超过120000字符、返回额外URI等情况；每次RPC和每份资源有截止时间，重复选择去重。网络失败可保留其他成功资料并明确通知，越权/撤销/配置变化拒绝继续。连接及凭据资料先在短Session取快照，网络等待不持有该Session；读取后及最终Prompt前再次检查配置，降低等待期间撤销仍注入的风险。列表中的图标/远端instructions不会自动加载或成为系统指令。

成功资源作为source_type=mcp_resource合入外部EvidenceUnit/Section，分配T标签，走196的同一份预算、final_source_map、引用检查及消息来源持久化。正文只作参考，不变成Skill、系统权限或模型自动执行计划。前端引用可跳到卡片，卡片显示服务、URI与本轮实际正文。用户消息保存mcp_resources_json（仅服务ID/URI），刷新可查看选择，重生成/编辑重答使用原选择重新授权读取；新问题选择发送后清空，不静默沿用。

Resources属于数据接入，不等于增加可执行工具；本轮没有把资源自动交给Planner选择，没有实现资源订阅、二进制渲染或参数化模板选择器。客户端提供resources/templates/list方法，当前网页仅选择已列出的具体URI。

API：GET /api/tools/mcp-resources/servers；GET /api/tools/mcp-resources/servers/{server_id}?project_id=...；POST /api/tools/mcp-resources/read?project_id=...。Chat新增mcp_resources数组，重生成从原user消息读取选择，无须信任客户端提交正文。

## 三、实现文件

| 文件 | 作用 |
| --- | --- |
| backend/app/services/tools/mcp_client.py、mcp_protocol.py | 新旧探测、握手、逐请求元数据、请求头、分页和SSE |
| backend/app/services/tools/adapters.py | 传入工具Schema供新版参数请求头使用 |
| backend/app/services/mcp_resource_service.py | 用户/项目/凭据检查、可见URI、文本读取、撤销复核与来源 |
| backend/app/api/routes/mcp_resources.py、tools.py | 资源浏览/预览API，复用JWT入口 |
| backend/app/schemas/mcp_resource.py、message.py | 有界选择输入及消息返回 |
| backend/app/models/message.py、迁移20261103_02 | 可空资源选择字段，旧消息不伪造选择 |
| chat_turn_bootstrapper.py、message_service.py、chat_context_assembly_service.py | 同事务保存选择、重生成复用ID、重新读取后合入统一证据 |
| frontend/src/components/mcp-resource-picker.tsx、chat-composer.tsx、chat-thread.tsx | 显式列表/预览/选择，项目切换隔离，发送后清空 |
| frontend/src/app/api/chat/route.ts、chat-message-list.tsx、external-source-card.tsx | 转发选择、历史显示和来源正文 |
| backend/tests/test_mcp_protocol_compatibility.py、test_mcp_resource_service.py及原回归 | 两代协议、失败不降级、作用域、撤销、大小、URI、迁移 |
| scripts/mcp_resources_real_e2e.py、public_credentials_real_smoke.py | 隔离真实HTTP/模型/浏览器验收与第三方工具复测 |

## 四、验证记录

最终后端927项含真实PostgreSQL/pgvector回归通过，无跳过；前端lint/build、Python compileall、git diff --check通过。此前旧单测固定假定首请求是initialize、无版本的初始化夹具不符合新校验，已补齐真实协议字段并按method核对新调用序列，未删除原业务校验。首轮完整浏览器在正文与调试JSON同时匹配时出现定位器歧义，已限定正文面板后重跑，而非隐藏业务失败。

真实Exa/Context7连接API各200，4个生产Executor工具调用均success/valid，凭据来源user、环境共享仍关闭；各工具来源2/1/5/5。脚本旧32017端口改为默认32007并可通过AIWS_API_BASE_URL指定。此结果不证明第三方已支持新版或Resources，仅验证升级后已有工具可用。

Resources全链路使用真实HTTP的受控新版/旧版MCP服务、隔离FastAPI和Next.js进程、固定账号已有在线模型及真实数据库。浏览器选资源、预览、发送后回答37天且引用通过，选择与来源落库/刷新可见；改变远端正文后重生成回答41天，证实重新读取。禁用服务后请求在网络前404；旧版Session读取也通过。桌面/手机无溢出、console/page错误0。临时项目、MCP配置、对话及测试进程清理。受控服务不冒充真实第三方Resources；仅隔离测试进程允许私网，日常服务设置不变，不启动Ollama。

数据库迁移前暂停本项目写入者并备份数据库/上传/索引到仓库外ai_web_studio_runtime/backups/mcp-resources-20261103-v1，再升级20261103_02；旧消息的新列保持NULL。最后恢复同一套Supervisor服务，禁止把凭据、备份或临时日志提交Git。

测试日志留在仓库外/tmp/aiws-mcp-resources-final-regression.log、/tmp/aiws-mcp-public-real.log、/tmp/aiws-mcp-resources-real-final.log；截图/tmp/aiws-mcp-resources-desktop.png、mobile.png。RAG百题指标未重跑，Prompts和第二层线上语义支持判断仍未接入。

## 五、文档与运行交付

当前方案、需求、架构、进展、159—162和README已更新；仓库外Day12、Day28及知识归档索引增加当前MCP口径，原作答保留。飞书已定点更新并回读确认Resources段落：当前项目入口ZyS5dt57nobU51xysZfcJjrYnwg（revision 77）、技术架构AeCyd8W8loPWSLxp0F2crhsUnwg（35）、上下文WYvyd0HiGoKZ1Bxn3HZcnMnvnie（17）、产品XxPrdVtg5o5bUcxj2iYcc3F4nWc（6）、Day12 PIl4dS6IYoGIryxujz3cnwipnvb（9）、Day28 GCH2ds6zCoNC98xnidRc4Zx8ntf（10）。同步使用lark-doc流程。

最终同一套Supervisor前后端与三类Worker运行，前端32008返回200，API 32007的health/ready和health/workers均ready；PostgreSQL仍为35433。未新增本项目Screen，其他项目服务不动。数据库、凭据和运行文件不纳入代码提交。
