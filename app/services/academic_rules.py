"""Governed local policy retrieval. Manifest content is untrusted data."""
import json
import html
import hashlib
import re
import unicodedata
from datetime import date
from pathlib import Path
from rank_bm25 import BM25Okapi
from app.services.rule_retrieval import tokens

ROOT = Path(__file__).resolve().parents[1] / 'knowledge'


def search(query, *, role='admin', demo=False, top_k=3, category='', class_type='', root=None, approval_check=None):
    result = {'status': 'not_found', 'answer': '未找到相关规则。', 'citations': [],
              'matched_rules': [], 'warnings': [], 'retrieval_metadata': {'mode': 'DEMO' if demo else 'formal'}}
    if demo:
        result['warnings'].append('DEMO：虚构内容，不是正式机构规则。')
    if role not in ('admin', 'student'):
        result.update(status='forbidden', answer='无权访问规则。')
        return result
    root = Path(root or ROOT).resolve()
    try:
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, list):
            raise ValueError('invalid manifest')
        chunks, ids, eligible = [], set(), 0
        for doc in manifest:
            doc_id = doc.get('document_id', doc.get('id'))
            if not isinstance(doc_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', doc_id) or doc_id in ids:
                raise ValueError('invalid id')
            ids.add(doc_id)
            is_demo = doc.get('demo') is True
            if is_demo != demo:
                continue
            if not demo:
                if doc.get('status') != 'approved' or doc.get('active') is not True:
                    continue
                if date.fromisoformat(doc['effective_date']) > date.today():
                    continue
            access = doc.get('access_level', 'admin' if is_demo else '')
            if access not in ('admin', 'student'):
                raise ValueError('invalid access')
            if role == 'student' and access != 'student':
                continue
            if category and category != doc.get('category', ''):
                continue
            if class_type and class_type not in doc.get('class_types', []):
                continue
            name = doc['file']
            if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() not in ('.md', '.txt'):
                raise ValueError('invalid path')
            unresolved = root / name
            if unresolved.is_symlink():
                raise ValueError('symlink not allowed')
            path = unresolved.resolve(strict=True)
            if path.parent != root or not path.is_file():
                raise ValueError('invalid path')
            # Hash and decode the same snapshot: later file swaps cannot alter returned text.
            raw = path.read_bytes()
            if not demo and (approval_check is None or not approval_check(doc, hashlib.sha256(raw).hexdigest())):
                continue
            text = raw.decode('utf-8-sig')
            if len(text) > 500000 or not doc.get('title') or not doc.get('version'):
                raise ValueError('invalid document')
            eligible += 1
            lines = text.splitlines()
            heading, start, body = '', 0, []
            def add():
                content = '\n'.join(body).strip()
                if content and heading:
                    chunks.append({'document_id': doc_id, 'document_title': doc['title'],
                        'document_version': str(doc['version']), 'version': str(doc['version']), 'section': heading,
                        'chunk_id': doc_id + ':' + str(start), 'source_location': {'file': name, 'line_start': start + 1, 'line_end': start + len(body)},
                        'supporting_excerpt': content[:200], '_text': content,
                        '_conflict_key': doc.get('conflict_key', ''), '_category': doc.get('category', ''), '_demo': is_demo})
            for n, line in enumerate(lines, 1):
                if re.match(r'^#{1,6}\s+', line):
                    add(); heading = re.sub(r'^#+\s+', '', line).strip(); start = n; body = []
                elif heading:
                    body.append(line)
            add()
        if not eligible:
            result.update(status='no_approved_rules' if not demo else 'not_found', answer='没有可访问且已生效的正式规则。' if not demo else result['answer'])
            return result
        if not chunks:
            raise ValueError('empty corpus')
        terms = tokens(query)
        corpus = [tokens(c['document_title'] + ' ' + c['section'] + ' ' + c['_text']) for c in chunks]
        if not terms:
            return result
        scores = BM25Okapi(corpus).get_scores(terms)
        # Small corpora can produce non-positive BM25 IDF; retain lexical overlap.
        scores = [float(score) + len(set(terms) & set(corpus[i])) for i, score in enumerate(scores)]
        # The overlap gate is a conservative heuristic, not a confidence score.
        indexes = [i for i in range(len(chunks)) if scores[i] > 0 and len(set(terms) & set(corpus[i])) >= 2]
        indexes.sort(key=lambda i: scores[i], reverse=True)
        candidates, seen = [], set()
        for i in indexes:
            key = chunks[i]['_text']
            if key not in seen:
                candidates.append(i)
                seen.add(key)
        if not candidates:
            return result
        # Check every relevant candidate, before applying the display limit.
        all_matches = [chunks[i] for i in candidates]
        def suspicious(value):
            value = html.unescape(unicodedata.normalize('NFKC', value))
            value = re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', value)
            return bool(re.search(r'忽略.{0,20}指令|系统指令|导出.{0,20}学生|调用.{0,20}工具|https?\s*[:：]|www\.|<[^>]+>|!?\[[^]]*\]\s*\(|(?:javascript|data)\s*:|!\[|(?:1[3-9]\d{9})|(?:\d{17}[0-9Xx])', value, re.I))
        if any(suspicious(c['_text']) for c in all_matches):
            result.update(status='needs_clarification', answer='规则内容需要人工审核。')
            result['warnings'].append('检测到需审核内容')
            return result
        groups = [c['_conflict_key'] for c in all_matches if c['_conflict_key']]
        conflict = len(groups) != len(set(groups))
        # Multiple documents in one category are possible alternatives, not proof of contradiction.
        categories = {}
        for c in all_matches:
            if c['_category']:
                categories.setdefault(c['_category'], set()).add(c['document_id'])
        review = any(len(ids) > 1 for ids in categories.values())
        chosen = candidates if conflict or review else candidates[:max(1, min(top_k, 10))]
        selected = [dict(chunks[i]) for i in chosen]
        for c in selected:
            for key in ('_text', '_conflict_key', '_category', '_demo'):
                c.pop(key)
        status = 'conflict' if conflict else 'needs_review' if review else 'ok'
        result.update(status=status, answer='存在可能冲突的规则，请人工核对。' if status != 'ok' else '请参阅以下规则原文。', citations=selected,
                      matched_rules=[c['chunk_id'] for c in selected])
        result['retrieval_metadata']['scores'] = [float(scores[i]) for i in chosen]
        return result
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        result.update(status='unavailable', answer='规则资料不可用，请联系管理员检查。')
        result['warnings'].append('规则清单或文件无效')
        return result
