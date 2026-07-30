# 导入FastAPI核心应用类
from fastapi import FastAPI
# 导入跨域中间件
from fastapi.middleware.cors import CORSMiddleware

# 导入项目所有业务路由模块
from app.api.routes import (
    agent_runtime,       # 可恢复 Agent Run/Step/Approval 控制面
    auth,                # 认证/登录授权相关接口
    chat,                # 对话聊天核心接口
    conversations,      # 会话管理接口
    health,              # 服务健康检查接口
    knowledge,           # 知识库管理接口
    memories,            # 记忆库管理接口
    messages,            # 单条消息CRUD接口
    prompt_templates,    # 提示词模板接口
    projects,            # 项目管理接口
    providers,           # AI模型服务商配置接口
    settings,            # 系统全局设置接口
    shares,              # 会话/内容分享接口
    tools,               # 工具插件调用接口
    uploads,             # 文件上传接口
)
# 导入全局配置实例
from app.core.config import settings as app_settings, validate_runtime_security_settings
# 导入初始化函数：确保运行时数据库表/结构存在
from app.core.startup import ensure_runtime_schema


# 实例化FastAPI主应用
app = FastAPI(
    title=app_settings.app_name,  # 接口文档标题，读取配置文件应用名
    version="0.1.0",              # 后端服务版本号
)

# 服务启动前置初始化：校验并创建运行所需数据库结构
validate_runtime_security_settings()
ensure_runtime_schema()

# 注册跨域CORS中间件，解决前后端本地联调跨域问题
app.add_middleware(
    CORSMiddleware,
    # 允许访问的前端域名列表
    allow_origins=list(app_settings.cors_allowed_origins),
    allow_credentials=True,  # 允许携带Cookie、凭证信息
    allow_methods=["*"],      # 放行所有HTTP请求方法(GET/POST/PUT/DELETE等)
    allow_headers=["*"],      # 放行所有请求头
)

# 注册健康检查路由，无/api前缀，单独挂载根路径
app.include_router(health.router)
# 注册认证模块路由，统一接口前缀/api
app.include_router(auth.router, prefix="/api")
app.include_router(agent_runtime.router, prefix="/api")
# 注册会话管理路由
app.include_router(conversations.router, prefix="/api")
# 注册知识库基础管理路由
app.include_router(knowledge.router, prefix="/api")
# 注册知识库凭证(服务商密钥)子路由
app.include_router(knowledge.credential_router, prefix="/api")
# 注册记忆管理路由
app.include_router(memories.router, prefix="/api")
# 注册消息记录路由
app.include_router(messages.router, prefix="/api")
# 注册提示词模板路由
app.include_router(prompt_templates.router, prefix="/api")
# 注册项目管理路由
app.include_router(projects.router, prefix="/api")
# 注册分享功能路由
app.include_router(shares.router, prefix="/api")
# 注册系统配置路由
app.include_router(settings.router, prefix="/api")
# 注册AI服务商配置路由
app.include_router(providers.router, prefix="/api")
# 注册工具插件路由
app.include_router(tools.router, prefix="/api")
# 注册聊天交互主路由
app.include_router(chat.router, prefix="/api")
# 注册文件上传路由
app.include_router(uploads.router, prefix="/api")


# 根路径首页接口，用于快速验证服务是否正常启动
@app.get("/")
def root() -> dict[str, str]:
    return {"message": "ai_web_studio backend is running"}
