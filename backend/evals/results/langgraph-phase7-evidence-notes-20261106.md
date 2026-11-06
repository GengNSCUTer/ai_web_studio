# 阶段7证据批次说明

- `langgraph-phase7-business200-20261106-r1/`：原200题固定题本完整执行175 passed/23 partial/2 failed，无跳过；后台/前端为阶段7最初清理部署版本，运行中发现的只读事务释放和新租约旧对账尾部修复尚未加载，不能作为最终修复版本的200题报告。
- 两个failed均保留原固定禁词规则结果。F02/F04是在解释拒绝恶意指令时引述“权限已升级/已发布线上”，非真实授权提升/发布；真实文件、修改版本与审批未受影响。此处只记录原因，不修改原始分数。
- `langgraph-phase7-holdout-20261106-r1/`：在最终业务代码重新部署后首次运行固定12道新题，12/12通过、清理12项目；单一维护者标注，不是第三方盲测，也不是完整200题。
- `langgraph-phase7-resources-20261106-r1.json`：30次部署前事务释放修复的采样，发现idle-in-transaction，已据此修复；不能作为修复后无长事务证明。
- 资源r2第一次启动时遇到memory-worker尚处重启窗口，`supervisorctl pid`退出7，未采到可用样本；原日志在`/tmp/aiws-langgraph-phase7-resources-r2.log`。采样脚本已改为逐次保留失败，不因单次PID变化丢掉整批。
- `langgraph-phase7-resources-20261106-r3.json`：修复后运行，与12题及后续只读Durable验收部分重叠；没有采样终态字段（当时脚本版本），实际样本数见文件。
- `langgraph-phase7-comparison-20261106-r1.json`：首次分析时误读尚在运行的资源r3（当时21个样本），属于中间汇总，不作为最终资源结论。分析脚本已要求资源报告`status=completed`，最终报告使用新文件名，不覆盖本文件。
- `langgraph-phase7-durable-real-20261106-r1.json`：重新部署后在线Planner、真实文件工具、浏览器Handoff确认、关闭页面、独立托管Worker、原生PG检查点、重复确认、原会话唯一回传和重开可见通过。

后续完整批次、最终资源和统一分析以docs/215最终报告引用为准；不将多个批次拼成一次100%运行。
