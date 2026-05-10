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

- 前端 `fetch + ReadableStream`
- Next BFF `/api/chat`
- FastAPI `/api/chat/text-stream`

不再使用旧的 AI SDK `useChat / TextStreamChatTransport` 方案，也不再使用旧的手写 SSE 主链路。

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
