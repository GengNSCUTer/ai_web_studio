# 统一执行内核可靠性对照

隔离PG/真实文件工具/固定Planner/实际45秒租约/进程强退

SQLAlchemy业务SQL计数，不含原生Saver的psycopg语句；固定Planner不调用模型API

| 场景 | 结果 | 工具实际调用 | 业务SQL | 等待租约秒 | 恢复就绪ms | 复用结果 |
|---|---|---:|---:|---:|---:|---:|
| normal | completed_evidence | 2 | 144 | 0 | None | 0 |
| parallel | completed_evidence | 2 | 144 | 0 | None | 0 |
| before_call | completed_evidence | 2 | 223 | 42.31 | 459.99 | 0 |
| after_call | completed_evidence | 3 | 227 | 42.31 | 546.94 | 0 |
| after_commit | completed_evidence | 2 | 223 | 42.31 | 413.47 | 1 |
| before_checkpoint | completed_evidence | 2 | 238 | 41.81 | 384.32 | 2 |
| unknown_write | blocked_pending_reconciliation | 0 | 82 | 43.06 | 41.47 | 0 |
