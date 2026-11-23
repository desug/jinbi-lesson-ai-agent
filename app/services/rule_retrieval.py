"""Read-only retrieval of explicitly registered demonstration clauses."""
import json
import re
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1] / 'knowledge'


def tokens(text: str) -> list[str]:
    segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text.lower())
    return [token for part in segments for token in
            ([part[i:i + 2] for i in range(len(part) - 1)] if re.fullmatch(r'[\u4e00-\u9fff]+', part) and len(part) > 1 else [part])]


@lru_cache(maxsize=1)
def load_index():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8-sig'))
    clauses = []
    seen = set()
    for doc in manifest:
        path = (ROOT / doc['file']).resolve()
        if path.parent != ROOT.resolve() or not doc.get('demo'):
            raise ValueError('无效的演示规则清单')
        text = path.read_text(encoding='utf-8-sig')
        for match in re.finditer(r'^## ([A-Z]+-\d+)\s*\n([\s\S]*?)(?=^## |\Z)', text, re.M):
            clause_id, content = match.group(1), match.group(2).strip()
            if clause_id in seen or not clause_id.startswith(doc['id'] + '-') or not content:
                raise ValueError('无效或重复的条款编号')
            seen.add(clause_id)
            clauses.append({'id': clause_id, 'text': content, 'title': doc['title'],
                            'version': doc['version'], 'effectiveDate': doc['effectiveDate'],
                            'scope': doc['scope'], 'demo': True})
    if not clauses:
        raise ValueError('规则库为空')
    corpus = [tokens(c['title'] + c['text']) for c in clauses]
    return clauses, corpus, BM25Okapi(corpus)


def search_rules(query: str) -> list[dict]:
    clauses, corpus, index = load_index()
    terms = tokens(query)
    if not terms:
        return []
    scores = index.get_scores(terms)
    matches = []
    for i in sorted(range(len(clauses)), key=lambda n: scores[n], reverse=True):
        # Require multiple overlapping bigrams so a generic single match cannot establish relevance.
        if scores[i] > 0 and len(set(terms) & set(corpus[i])) >= 2:
            matches.append(clauses[i])
        if len(matches) == 3:
            break
    return matches


def get_source(source_id: str) -> dict | None:
    return next((c for c in load_index()[0] if c['id'] == source_id), None)


def validate_citation(source_id: str, quote: str, retrieved_ids: set[str]) -> bool:
    source = get_source(source_id) if source_id in retrieved_ids else None
    return bool(source and quote and quote in source['text'])
