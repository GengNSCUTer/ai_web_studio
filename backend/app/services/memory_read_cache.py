"""短期缓存已选择的记忆 ID 与排名，不缓存答案、记忆正文或模型密钥。"""

from __future__ import annotations

import json
from functools import lru_cache

from redis import Redis, RedisError

from app.core.config import settings


@lru_cache(maxsize=1)
def _redis_client():
    return Redis.from_url(settings.memory_redis_url, socket_connect_timeout=.1,
        socket_timeout=.1, max_connections=16, decode_responses=False)


class MemoryReadCache:
    MAX_QUERIES = 8

    def __init__(self, client=None, *, enabled=None):
        self.client = client
        self.enabled = settings.memory_read_cache_enabled if enabled is None else enabled

    @staticmethod
    def key(db, user_id, project_id):
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return None
        # 测试临时库和正式库不能共用缓存；键中不包含连接密码、问题或正文。
        url = bind.url
        return f"aiws:memory:selection:{url.host}:{url.port}:{url.database}:{user_id}:{project_id or 'global'}"

    def get(self, key, field):
        if not self.enabled or not key:
            return None, "disabled"
        try:
            client = self.client if self.client is not None else _redis_client()
            raw = client.hget(key, field)
            if raw is None:
                return None, "miss"
            if len(raw) > max(1024, settings.memory_read_cache_max_bytes):
                return None, "oversize"
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get("ids"), list) or len(value["ids"]) > 32:
                return None, "invalid"
            if not all(isinstance(item, str) for item in value["ids"]):
                return None, "invalid"
            return value, "candidate"
        except (RedisError, ValueError, TypeError):
            return None, "unavailable"

    def put(self, key, field, value):
        if not self.enabled or not key:
            return
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > max(1024, settings.memory_read_cache_max_bytes):
            return
        try:
            client = self.client if self.client is not None else _redis_client()
            # 原子限制每个用户/项目最多八种问题，TTL 只回收空间；一致性依赖 PG 复核。
            client.eval("""
                if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 0
                   and redis.call('HLEN', KEYS[1]) >= tonumber(ARGV[4]) then
                    redis.call('DEL', KEYS[1])
                end
                redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
                redis.call('EXPIRE', KEYS[1], ARGV[3])
                return 1
            """, 1, key, field, raw, max(1, min(300, settings.memory_read_cache_ttl_seconds)), self.MAX_QUERIES)
        except (RedisError, ValueError, TypeError):
            return
