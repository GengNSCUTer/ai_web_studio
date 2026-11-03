"""冲突分析仅提供建议；最终激活的内容、范围与版本由代码复核。"""

import json
import re

from app.services.memory_policy import memory_identity


CONFIDENCE_MIN = .85
_HIGH_IMPACT = re.compile(r'生产|权限|授权|安全|密钥|删除|支付|医疗|诊断|用药|法律|财务|预算|薪资|'
                          r'\b(?:production|permission|security|delete|medical|payment|salary|budget)\b', re.I)


def high_impact(memory):
    return (memory.memory_type == 'instruction' or getattr(memory, 'sensitivity', 'normal') == 'sensitive'
            or (getattr(memory, 'importance', 0) or 0) >= .8
            or bool(_HIGH_IMPACT.search(f'{memory.title}\n{memory.content}')))


def candidate_snapshot(memory, project_id):
    return dict(memory_type=memory.memory_type, title=memory.title, content=memory.content, project_id=project_id)


def load_review(memory):
    raw = getattr(memory, 'conflict_review_json', None)
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {'status': 'stale'}
    except (ValueError, TypeError):
        return {'status': 'stale'}


def validate_review(memory, existing, project_id, *, acknowledged=False):
    """调用方已在用户锁内刷新所有active行；任何并发变化都要求重新分析。"""
    review = load_review(memory)
    if review is None:
        return None
    if (review.get('status') == 'stale' or review.get('candidate') != candidate_snapshot(memory, project_id)
            or review.get('active_versions') != {row.id: row.version for row in existing}):
        raise ValueError('记忆或相关版本已变化，请重新检查冲突后确认')
    if review.get('requires_review') and not acknowledged:
        raise ValueError('该候选需要人工确认关系及影响，不能自动启用')
    return review


def coexist_ids(review, *, acknowledged=False):
    return {pair['memory_id'] for pair in (review or {}).get('pairs', [])
            if pair['relation'] in {'complement', 'different_scope'}
            and (pair['confidence'] >= CONFIDENCE_MIN or acknowledged)}


def has_structured_conflict(new, old):
    a = memory_identity(new.memory_type, new.title, new.content)
    b = memory_identity(old.memory_type, old.title, old.content)
    return a.key == b.key and a.structured and b.structured and a.value != b.value
