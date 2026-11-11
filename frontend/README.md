# Frontend

这个目录是当前智能问答网页的前端部分，使用：

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS

当前定位不是通用模板，而是本项目自己的聊天前端。

## 当前已完成

- 登录 / 注册页面
- 主聊天页
- 历史会话列表
- 新建 / 重命名 / 删除会话
- 文本流式聊天
- 停止生成
- 设置面板
- Provider 切换
- 在线 API 测试连接
- 文件 / 图片上传入口

## 当前聊天链路

当前主聊天链路是：

- 前端原生 `EventSource`
- Next 通用鉴权代理 `/api/backend/chat/runs...`
- `POST /api/chat/runs` 创建持久任务，`GET /api/chat/runs/{id}/events` 统一 SSE 订阅；编辑与重生成用 `/runs/edit`、`/runs/regenerate`，取消用独立 `/runs/{id}/cancel`。旧POST流、NDJSON和纯文本Chat入口已移除。

当前使用原生 `EventSource` 解析与订阅SSE，浏览器断线重连时自动发送 `Last-Event-ID`，经同源Next代理透传给后端。已删除手写fetch读流、分帧和指数退避；仅保留业务去重、终态/取消关闭，以及连续五次无业务进展错误时的提示。页面重开新建EventSource后从已保存事件/快照恢复。创建/查询/取消仍使用普通JSON请求，不再使用 AI SDK `useChat / TextStreamChatTransport` 或按行JSON兼容流。

## 开发启动

```bash
cd /disk2/gengnan/ai_web_studio/frontend
npm install
npm run dev -- --hostname 127.0.0.1 --port 32008
```

## 校验

```bash
npm run lint
npm run build
```

## 近期待做

- 会话搜索
- 上下文统计与可视化
- 摘要压缩升级
- 附件索引化与按需展开
