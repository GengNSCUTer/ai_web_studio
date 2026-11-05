# 12条Agent业务先行集：2026-11-05 r2

这是一次完整重跑，不是成功样本拼接。在线DeepSeek-V3.2，8条Chat/RAG及4条独立Worker文档任务，严格10通过、2失败、0跳过。生产代码起点975f154，执行器为本次工作区新增代码，原始报告已明确worktree_modified=true。

- `report.json` / `report.html`：原始真实执行结果，不覆盖；包括逐题事实、引用、Trace、阶段及清理结果。
- `analysis.json` / `analysis.html`：对同一批原始结果的最新汇总与可读报告，没有重新调用模型；HTML内有4个产物链接。
- `artifacts/`：四份实际下载的Markdown/Word，以及浏览器下载的Word；都是合成输入的报告，不是用户正式项目文件。
- `browser-document.png`：实际产品页预览，包含三阶段成功和“未保存（可选）”状态。
- `browser-report.png`：在D01完成时对原始报告的浏览器检查；当时后续三题尚未执行，不能作为最终汇总。
- `analysis-browser.png` / `analysis-browser.json`：全部完成后的最终可读报告，12行、4个产物链接，页面错误0、无横向溢出。

K01/K02最终答案、Gold Chunk和KB引用检查通过，但误规划Context7，实际外部调用被Action Intent拒绝；K01还做了三次无关的本地artifact列表查询。严格判失败，不将安全拦截或最终done当作任务成功。

前一次r1保留在相邻目录；其中四个文件题的失败来自将只读baseline误判成写入，评分器已校准。r1→r2不能用于宣称产品成功率提升。本轮20项专项及1013项全量含真实PostgreSQL/pgvector测试通过；没有测试200题、强制Worker崩溃、脚本容器或完整语义忠实度。

两轮各12个临时项目均已清理，清理后数据库内`agent-pilot-*`项目数量为0。原账号模型/工具配置、已有知识库及服务均保留。复现、分母和后续计划见仓库`docs/203_Agent分层评测与业务闭环计划_2026-11-04.md`。
