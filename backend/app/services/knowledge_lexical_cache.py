"""按已打开文件的身份缓存词法索引，独立限制并发加载与缓存容量。"""

from collections import OrderedDict
from concurrent.futures import Future
import json
import os
from sys import getsizeof
from threading import Lock, BoundedSemaphore


class LexicalIndexCache:
    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=8):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries = OrderedDict()
        self.loading: dict[tuple, Future] = {}
        self.lock = Lock()
        self.parsers = BoundedSemaphore(2)
        self.bytes = 0

    @classmethod
    def _size(cls, value):
        size = getsizeof(value)
        if isinstance(value, dict):
            return size + sum(cls._size(key) + cls._size(item) for key, item in value.items())
        if isinstance(value, list):
            return size + sum(cls._size(item) for item in value)
        return size

    def load(self, path, version):
        with path.open(encoding="utf-8") as stream:
            stat = os.fstat(stream.fileno())
            key = (path, version, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            with self.lock:
                if key in self.entries:
                    self.entries.move_to_end(key)
                    return self.entries[key][0]
                pending = self.loading.get(key)
                leader = pending is None
                if leader:
                    pending = self.loading[key] = Future()
            if not leader:
                return pending.result()
            try:
                with self.parsers:
                    payload = json.load(stream)
                    if not isinstance(payload, dict) or payload.get("version") != version:
                        raise RuntimeError("知识库 BM25 索引版本不兼容，请重建索引。")
                    size = self._size(payload)
                with self.lock:
                    if size <= self.max_bytes:
                        while self.entries and (self.bytes + size > self.max_bytes or len(self.entries) >= self.max_entries):
                            self.bytes -= self.entries.popitem(last=False)[1][1]
                        self.entries[key] = (payload, size)
                        self.bytes += size
                pending.set_result(payload)
                return payload
            except BaseException as exc:
                pending.set_exception(exc)
                raise
            finally:
                with self.lock:
                    del self.loading[key]


lexical_index_cache = LexicalIndexCache()
