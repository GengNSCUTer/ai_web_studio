"""语义选择器单测替身；真实SQL、ANN与持久化另由隔离PostgreSQL测试覆盖。"""

import math

from app.services.tools.search_index import search_terms


class MemoryToolSearchIndex:
    def __init__(self):
        self.rows = {}

    def prepare(self, config, documents):
        ready = set()
        for document in documents:
            key = (config.scope(), document.tool_key)
            previous = self.rows.get(key)
            if previous and previous[0] == document:
                if previous[1] is not None:
                    ready.add(document.tool_key)
            else:
                self.rows[key] = (document, None)
        return ready

    def save(self, config, documents, vectors):
        saved = set()
        for document, vector in zip(documents, vectors):
            key = (config.scope(), document.tool_key)
            if self.rows.get(key, (None,))[0] == document:
                self.rows[key] = (document, vector)
                saved.add(document.tool_key)
        return saved

    def lexical(self, config, documents, query, categories=(), limit=30):
        terms = set(search_terms(query))
        scores = {d.tool_key: len(terms.intersection(search_terms(d.lexical_text))) + 20 * (d.category in categories)
                  for d in documents}
        return sorted((key for key, score in scores.items() if score), key=lambda key: (-scores[key], key))[:limit]

    def semantic(self, config, documents, vector, limit=30, force_exact=False):
        scores = {}
        for document in documents:
            stored = self.rows.get((config.scope(), document.tool_key))
            if stored and stored[0] == document and stored[1] is not None:
                candidate = stored[1]
                scores[document.tool_key] = sum(a * b for a, b in zip(vector, candidate)) / (
                    math.hypot(*vector) * math.hypot(*candidate))
        keys = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
        return {key: scores[key] for key in keys}, dict(search_mode='test_exact', exact_topup=False)
