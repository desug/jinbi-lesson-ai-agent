from datetime import date
import re
from sqlalchemy import select
from rank_bm25 import BM25Okapi
from app.models.demo_rules import DemoRule
from app.services.ai_conversation_chat import DEMO_MAKEUP_COUNT_QUERY, DEMO_MAKEUP_TIME_QUERY, is_rule_query
from app.services.demo_rule_approval import is_current
from app.services.rule_retrieval import tokens

# Generic policy words cannot by themselves establish relevance. A category
# fallback is available only for an explicit policy question and a category
# literally present in the query; it operates on the already-authorized rules.
GENERIC_TERMS = frozenset({'规则', '规定', '要求', '教学', '学规', '虚构', '演示', '机构', '制度', '课程', '学员', '有关', '相关'})

# Each summary describes a specific approved body. A changed rule must not
# inherit an older summary just because its document ID still matches.
DEMO_RULE_PREFIX = '虚构教学规则，仅供 Jinbi Lesson 参赛演示。'
DEMO_RULE_SUFFIX = '；此内容不是任何真实机构制度。'
DEMO_RULE_TITLES = {'DEMO-JINBI-LEAVE': '请假规则', 'DEMO-JINBI-MAKEUP': '补课规则'}
LEAVE_MAKEUP_ANSWER = '经教务老师确认后，可以安排一次补课。'
MAKEUP_TIME = '补课时间由教务老师与学员协商确定'
RULE_ANSWERS = {
    ('DEMO-JINBI-LEAVE',
     DEMO_RULE_PREFIX + '请假规则：虚构学员在课程开始前向演示教务提出请假，'
     '演示教务确认后可安排一次补课' + DEMO_RULE_SUFFIX):
        '学员如需请假，应在课程开始前联系教务老师提出申请。' + LEAVE_MAKEUP_ANSWER,
    ('DEMO-JINBI-MAKEUP',
     DEMO_RULE_PREFIX + '补课规则：虚构学员的补课时间由演示教务与学员协商确定' + DEMO_RULE_SUFFIX):
        MAKEUP_TIME + '，具体安排请联系教务老师确认。',
}


def _named_category(rule, query):
    category = rule.category.strip()
    return bool(re.fullmatch(r'[\u4e00-\u9fff]{2,12}', category)
                and category not in GENERIC_TERMS and category in query)

def search_approved(db, query, *, class_type='', category='', top_k=3):
    asks_makeup_count = bool(DEMO_MAKEUP_COUNT_QUERY.search(query))
    asks_makeup_time = bool(DEMO_MAKEUP_TIME_QUERY.search(query))
    rules = [r for r in db.scalars(select(DemoRule)).all() if is_current(db, r)
             and date.fromisoformat(r.effective_date) <= date.today()
             and (not class_type or r.class_type == class_type)
             and (not category or r.category == category)]
    result = {'status': 'no_approved_rules' if not rules else 'not_found', 'answer': '目前没有找到相关规定。', 'citations': [], 'matched_rules': [], 'warnings': ['DEMO 虚构规则，仅用于演示'], 'retrieval_metadata': {'mode': 'DEMO'}}
    if not rules: return result
    terms = tokens(query)
    corpus = [tokens(r.title + ' ' + r.category + ' ' + r.class_type + ' ' + r.text) for r in rules]
    if not terms: return result
    scores = BM25Okapi(corpus).get_scores(terms)
    overlaps = [set(terms) & set(words) for words in corpus]
    hits = [i for i, overlap in enumerate(overlaps)
            if len(overlap) >= 2 and overlap - GENERIC_TERMS]
    if is_rule_query(query, demo=True):
        hits = sorted(set(hits) | {i for i, rule in enumerate(rules) if _named_category(rule, query)})
        # The leave body also mentions arranging makeup classes. Require the
        # query to name both topics before that overlap can return both rules.
        topics = {'请假', '补课'}
        named_topics = {topic for topic in topics if topic in query}
        if named_topics:
            hits = [i for i in hits if rules[i].category not in topics - named_topics]
        if asks_makeup_count:
            hits = [i for i in hits if rules[i].category == '请假']
    hits.sort(key=lambda i: (_named_category(rules[i], query),
                             float(scores[i]) + len(overlaps[i])), reverse=True)
    if not hits: return result
    scopes = [(rules[i].category, rules[i].class_type) for i in hits]
    review = len(scopes) != len(set(scopes))
    selected = hits if review else hits[:max(1, min(top_k, 10))]
    matched = [rules[i] for i in selected]
    citations = [
        {'document_id': rule.document_id, 'document_title': rule.title,
         'version': rule.version, 'section': '正文', 'chunk_id': f'demo-db:{rule.id}',
         'source_location': {'kind': 'demo_sqlite', 'rule_id': rule.id, 'line_start': 1,
                             'line_end': len(rule.text.splitlines())},
         'supporting_excerpt': rule.text[:200], 'rule_text': rule.text}
        for rule in matched
    ]
    for rule, citation in zip(matched, citations):
        title = DEMO_RULE_TITLES.get(rule.document_id)
        if title:
            citation['display_title'] = title
            citation['display_text'] = (rule.text.removeprefix(DEMO_RULE_PREFIX)
                                        .removeprefix(title + '：')
                                        .replace(DEMO_RULE_SUFFIX, '。', 1))
    if review:
        answer = '有多条规定适用，请查看依据并联系教务老师确认。'
    else:
        parts = []
        for rule in matched:
            summary = RULE_ANSWERS.get((rule.document_id, rule.text))
            if summary is None:
                continue
            if asks_makeup_count and rule.document_id == 'DEMO-JINBI-LEAVE':
                summary = '学员请假' + LEAVE_MAKEUP_ANSWER
            elif asks_makeup_time and rule.document_id == 'DEMO-JINBI-MAKEUP':
                summary = MAKEUP_TIME + '。'
            parts.append(summary)
        if len(parts) < len(matched):
            parts.append('请查看下方依据，或联系教务老师确认。')
        answer = '\n'.join(parts)
    result.update(status='needs_review' if review else 'ok', answer=answer,
                  citations=citations, matched_rules=[citation['chunk_id'] for citation in citations])
    return result
