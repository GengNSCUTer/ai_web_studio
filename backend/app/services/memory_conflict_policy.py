"""冲突分析仅提供建议；最终激活的内容、范围与版本由代码复核。"""

import json
import logging
import re

from app.services.memory_policy import contains_credential, lifetime, memory_identity


CONFIDENCE_MIN = .85
logger = logging.getLogger(__name__)
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


def structured_fact_key(memory):
    identity = memory_identity(memory.memory_type, memory.title, memory.content)
    if (identity.structured and lifetime(memory.title, memory.content) == 'durable'
            and getattr(memory, 'sensitivity', 'normal') != 'sensitive'
            and not contains_credential(memory.title, memory.content)):
        return identity.key
    return None


def validate_review(memory, existing, project_id, *, acknowledged=False):
    """调用方在用户锁内按审核范围重查相关 active 行。"""
    review = load_review(memory)
    if review is None:
        return None
    if review.get('status') == 'stale' or review.get('candidate') != candidate_snapshot(memory, project_id):
        raise ValueError('记忆或相关版本已变化，请重新检查冲突后确认')
    current = {row.id: row.version for row in existing}
    before = review.get('active_versions')
    if before != current:
        before = before if isinstance(before, dict) else {}
        analyzed = {pair.get('memory_id') for pair in review.get('pairs', [])}
        changed = {key for key in current.keys() | before.keys() if current.get(key) != before.get(key)}
        logger.info('memory_review_scope_drift analyzed=%d other=%d', len(changed & analyzed), len(changed - analyzed))
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
