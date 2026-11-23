"""Local demo-only evidence lookup; never an authoritative policy source."""
from app.services import rule_retrieval


def search_demo(query: str) -> dict:
    """Explicit demonstration mode. Fail closed if the local corpus is invalid."""
    try:
        # Reload every time so removed or changed files cannot remain cached.
        rule_retrieval.load_index.cache_clear()
        matches = rule_retrieval.search_rules(query)
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'answer': '演示规则库不可用。', 'citations': [],
                'matched_rules': [], 'warnings': ['规则文件缺失或损坏'], 'retrieval_metadata': {'mode': 'demo'}}
    # Do not render potentially instruction-bearing passages as answers.
    import re
    if any(re.search(r'忽略.{0,10}指令|系统指令|导出.{0,10}学生|调用.{0,10}工具|https?://', m['text'], re.I) for m in matches):
        return {'status': 'needs_clarification', 'answer': '规则内容需要人工审核。', 'citations': [],
                'matched_rules': [], 'warnings': ['检测到需要审核的内容'], 'retrieval_metadata': {'mode': 'demo'}}
    citations = [{'document_id': m['id'].rsplit('-', 1)[0], 'document_title': m['title'],
                  'document_version': m['version'], 'section': m['id'], 'chunk_id': m['id'],
                  'source_location': None, 'supporting_excerpt': m['text'][:200]} for m in matches]
    return {'status': 'demo' if matches else 'not_found',
            'answer': '仅供演示，请查看引用。' if matches else '未找到相关演示规则。',
            'citations': citations, 'matched_rules': [m['id'] for m in matches],
            'warnings': ['演示内容，不是正式机构规则'], 'retrieval_metadata': {'mode': 'demo'}}
