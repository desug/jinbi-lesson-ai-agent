"""Mock-only natural language planner and adapter for the isolated demo."""
import json
import hashlib
import re
import logging
from contextvars import ContextVar
from uuid import uuid4
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from openai import OpenAI
from fastapi import HTTPException
from sqlalchemy import select
from app.models.ai_memory import AiConversation
from app.services.ai_conversation_chat import (
    CANDIDATE_SELECTION, _is_lesson_change_request, chat, is_empty_demo_query, is_rule_query,
)
from app.services.ai_function_chat import SUBJECTS, response
from app.services.deepseek_planner import _parse_object, MAX_RESPONSE_BYTES
from app.services.demo_natural_input import require_fictional_query
from experiments.few_shot.schemas import Suggestion

STATE_KEY = 'demo_model_suggestion'
DIAGNOSTIC_REQUEST_ID = ContextVar('demo_diagnostic_request_id', default=None)


@dataclass(frozen=True)
class ConfirmedScope:
    student_name: str
    subject: str


def canonical_subject(value):
    aliases = {label: subject for subject in SUBJECTS
               for label in (subject, subject + '课', subject + '课程')}
    return aliases.get(value.strip()) if isinstance(value, str) else None


def followup_subject(query):
    text = query.strip(' 。！？?!')
    for prefix in ('虚构小明的', '虚构小明', '小明的', '小明'):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return canonical_subject(text)


class SourceMismatch(ValueError):
    def __init__(self, reason, checks):
        super().__init__(reason)
        self.checks = checks


def parameter_diagnostic(checks):
    # Only fixed field names, booleans and local provenance labels; no values.
    return {'parameter_checks': checks,
            'mismatch_fields': [key for key, item in checks.items() if not item['matched']]}
DIAGNOSTIC_STAGES = {
    'ok': 'validated', 'input_rejected': 'input', 'request_limit': 'budget',
    'budget_error': 'budget', 'http_error': 'upstream_http',
    'network_error': 'transport', 'tls_error': 'transport', 'timeout': 'transport',
    'redirect_denied': 'transport', 'encoding_denied': 'transport',
    'response_too_large': 'transport', 'request_failed': 'transport',
    'invalid_response': 'response', 'incomplete_response': 'response',
    'invalid_json': 'json', 'invalid_schema': 'schema', 'unverified_source': 'source',
}


def validate_source(suggestion, query, *, confirmed=None, checks=None):
    """Recheck both the schema and input provenance; no model identity is authoritative."""
    suggestion = Suggestion.model_validate(suggestion)
    checks = {} if checks is None else checks
    name = '虚构小明' if '虚构小明' in query else None
    subjects = [s for s in SUBJECTS if s in query]
    if confirmed is not None:
        if (type(confirmed) is not ConfirmedScope or confirmed.student_name != '虚构小明'
                or confirmed.subject not in SUBJECTS or followup_subject(query) != confirmed.subject):
            raise SourceMismatch('missing confirmed context',
                                 {'session': {'matched': False, 'source': 'confirmed_session'}})
        name = confirmed.student_name
    expected = {}
    if name: expected['student_name'] = name
    if len(subjects) == 1: expected['subject'] = subjects[0]
    parameters = dict(suggestion.parameters)
    for key, value in parameters.items():
        canonical = value.strip()
        if key == 'subject': canonical = canonical_subject(value) or value
        if key == 'student_name' and confirmed and canonical == '小明':
            canonical = confirmed.student_name  # Closed alias; never selects an identity.
        source = ('confirmed_session' if confirmed and key == 'student_name' else 'current_user')
        label = key if key in ('student_name', 'subject', 'student_ref') else 'other'
        matched = expected.get(key) == canonical
        checks[label] = {'matched': matched, 'source': source, 'normalized': value != canonical}
        parameters[key] = canonical
    if (confirmed and 'student_name' not in parameters
            and suggestion.action == 'propose_tool'):
        parameters['student_name'] = confirmed.student_name
        checks['student_name'] = {'matched': True, 'source': 'confirmed_session', 'inherited': True}
    if len(subjects) > 1 or any(not item['matched'] for item in checks.values()):
        raise SourceMismatch('unverified parameters', checks)
    if suggestion.action == 'propose_tool':
        if (suggestion.intent != 'student_hours' or suggestion.tool != 'get_student_hours'
                or not name or not subjects or parameters != expected):
            for key in ('student_name', 'subject'):
                if key not in parameters:
                    checks[key] = {'matched': False, 'source': 'confirmed_session' if confirmed and key == 'student_name' else 'current_user'}
            raise SourceMismatch('unsupported proposal', checks)
    elif suggestion.action == 'clarify':
        if suggestion.intent not in ('student_hours', 'clarification'):
            raise ValueError('unsupported clarification')
        if suggestion.reason == 'missing_scope':
            if not name or subjects:
                raise ValueError('invalid scope clarification')
            # The current query already supplies the name. A generic
            # clarification can omit it without confirming either candidate.
            return Suggestion.model_validate({**suggestion.model_dump(), 'intent': 'student_hours',
                                              'parameters': expected})
    elif suggestion.action != 'reject':
        raise ValueError('unsupported action')
    # Revalidate rather than treating a copied model object as trusted.
    return Suggestion.model_validate({**suggestion.model_dump(), 'parameters': parameters})


class MockNaturalPlanner:
    def __init__(self, transport):
        if type(transport) is not httpx.MockTransport:
            raise ValueError('explicit mock transport required')
        self.model = 'mock-only'
        self.client = OpenAI(api_key='fictional-mock-only', base_url='https://mock.invalid',
                             http_client=httpx.Client(transport=transport, trust_env=False),
                             max_retries=0, timeout=20.0)

    def check(self, query, *, confirmed=None):
        # Only fixed labels and bounded numeric metadata reach the optional
        # diagnostic hook; neither query, model content nor exception text do.
        diagnostic = {'request_id': DIAGNOSTIC_REQUEST_ID.get() or uuid4().hex,
                      'category': 'input_rejected', 'json_valid': None,
                      'schema_valid': None, 'source_valid': None,
                      'finish_reason': None, 'content_present': None, 'usage': None, 'suggestion': None}
        try:
            model_query = followup_subject(query) if confirmed else query
            require_fictional_query(model_query)
            labels = ({'student_name': confirmed.student_name, 'subject': confirmed.subject}
                      if confirmed else {})
            diagnostic['category'] = 'request_failed'
            result = self.client.chat.completions.create(
                model=self.model, messages=[
                    {'role':'system','content':'Return one JSON object with fields intent, parameters, complete, ambiguity, action, tool, reason, question. For an explicit student and subject use intent student_hours, parameters student_name and subject, complete true, ambiguity none, action propose_tool, tool get_student_hours, reason ready, question empty. If subject missing use intent student_hours, parameters student_name, complete false, ambiguity missing_scope, action clarify, tool null, reason missing_scope and a question. Never infer IDs or write tools. Copy canonical labels exactly; never abbreviate student names or add subject suffixes. Server-confirmed labels: '+json.dumps(labels,ensure_ascii=False)},
                    {'role':'user','content':model_query}], response_format={'type':'json_object'},
                stream=False, max_tokens=512, timeout=20.0,
                extra_body={'thinking': {'type':'disabled'}})
            usage = getattr(result, 'usage', None)
            if usage is not None:
                diagnostic['usage'] = {key: value for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                       if type(value := getattr(usage, key, None)) is int and 0 <= value <= 10000000}
            diagnostic['category'] = 'invalid_response'
            if len(result.choices) != 1: raise ValueError('invalid choices')
            choice = result.choices[0]
            reason = choice.finish_reason
            diagnostic['finish_reason'] = reason if reason in ('stop','length','content_filter','tool_calls','insufficient_system_resource','aborted') else 'other'
            content = choice.message.content
            diagnostic['content_present'] = isinstance(content, str) and bool(content.strip())
            if (reason != 'stop' or choice.message.role != 'assistant'
                    or choice.message.tool_calls or getattr(choice.message, 'refusal', None)):
                diagnostic['category'] = 'incomplete_response'
                raise ValueError('invalid completion')
            diagnostic['category'] = 'invalid_json'
            diagnostic['json_valid'] = False
            obj = _parse_object(content, MAX_RESPONSE_BYTES)
            diagnostic['json_valid'] = True
            if isinstance(obj.get('parameters'), dict) and 'student_ref' in obj['parameters']:
                diagnostic.update(parameter_diagnostic({'student_ref': {'matched': False, 'source': 'server_generated'}}))
            diagnostic['category'] = 'invalid_schema'
            diagnostic['schema_valid'] = False
            suggestion = Suggestion.model_validate(obj)
            diagnostic['schema_valid'] = True
            diagnostic['suggestion'] = {'intent': suggestion.intent, 'action': suggestion.action,
                                        'tool': suggestion.tool, 'reason': suggestion.reason,
                                        'ambiguity': suggestion.ambiguity,
                                        'parameter_keys': sorted(suggestion.parameters)}
            diagnostic['category'] = 'unverified_source'
            diagnostic['source_valid'] = False
            try:
                checks = {}
                suggestion = validate_source(suggestion, query, confirmed=confirmed, checks=checks)
                diagnostic.update(parameter_diagnostic(checks))
            except ValueError as exc:
                if isinstance(exc, SourceMismatch): diagnostic.update(parameter_diagnostic(exc.checks))
                # Fixed local labels only; never expose parameter values.
                labels = {'unverified parameters': 'parameter_mismatch',
                          'unsupported proposal': 'unsupported_proposal',
                          'unsupported clarification': 'unsupported_clarification',
                          'invalid scope clarification': 'invalid_scope_clarification',
                          'unsupported action': 'unsupported_action'}
                diagnostic['source_failure'] = labels.get(str(exc), 'validation_failed')
                raise
            diagnostic['source_valid'] = True
            diagnostic['category'] = 'ok'
            return suggestion
        except Exception:
            return None
        finally:
            diagnostic['stage'] = DIAGNOSTIC_STAGES.get(diagnostic['category'], 'planner')
            reporter = getattr(self, '_record_diagnostic', None)
            if reporter is not None:
                try: reporter(diagnostic)
                except Exception: pass
            self.last_diagnostic = diagnostic.copy()


class SuggestionClient:
    """Translate only a validated proposal into the existing tool-call protocol."""
    def __init__(self, suggestion):
        self.suggestion = suggestion
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model, messages, tools, tool_choice, extra_body):
        if not isinstance(messages, list) or len(messages) not in (2, 4):
            raise ValueError('invalid envelope')
        payload = _parse_object(messages[1].get('content'), 1024)
        if (set(payload) != {'student_ref', 'subject', 'task'} or payload['task'] != 'hours'
                or self.suggestion.action != 'propose_tool'
                or self.suggestion.intent != 'student_hours'
                or self.suggestion.tool != 'get_student_hours'
                or payload['subject'] != self.suggestion.parameters.get('subject')
                or not isinstance(payload['student_ref'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', payload['student_ref'])):
            raise ValueError('proposal does not match server scope')
        if [x.get('function', {}).get('name') for x in tools] != ['get_student_hours', 'get_lesson_records']:
            raise ValueError('invalid tool inventory')
        if len(messages) == 2 and tool_choice == 'auto':
            args = json.dumps({'student_ref': payload['student_ref']})
            call = SimpleNamespace(id='model_suggestion_1', function=SimpleNamespace(name=self.suggestion.tool, arguments=args))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]), finish_reason='tool_calls')])
        if (len(messages) == 4 and tool_choice in ('auto', 'none') and messages[2].get('role') == 'assistant'
                and messages[3].get('role') == 'tool' and messages[3].get('tool_call_id') == 'model_suggestion_1'
                and messages[3].get('content') == '{"executed":true}'):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]), finish_reason='stop')])
        raise ValueError('invalid tool completion')


def _clear(db, conversation):
    if conversation is not None:
        conversation.context = {}
        db.commit()


def _answer(status, text, conversation_id, error_code=None):
    result = response(status, text)
    if conversation_id: result['conversationId'] = conversation_id
    if status == 'error':
        result.update(errorCode=error_code or 'unsafe_request',
                      recovery=('operator_required' if error_code in ('model_request_limit', 'model_budget_error')
                                else 'retry_current' if conversation_id else 'new_session'))
    return result


def _clarification(db, admin_id, text, conversation_id):
    # Non-executing successful turns still need a real, owned conversation.
    # Failed/input-rejected turns never enter this path or persist raw input.
    if conversation_id is None:
        conversation = AiConversation(admin_id=admin_id, title='虚构演示查询', context={}, summary='')
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
        db.commit()
    return _answer('clarification', text, conversation_id)


def online_chat(db, admin_id, query, conversation_id, selected_student_id, planner):
    conversation = None
    if conversation_id:
        conversation = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id,
                                                             AiConversation.admin_id == admin_id))
        if not conversation: raise HTTPException(status_code=404, detail='会话不存在')
    context = dict(conversation.context or {}) if conversation else {}
    pending = context.get('candidates', [])
    if selected_student_id is not None and not any(s['studentId'] == selected_student_id for s in pending):
        raise HTTPException(status_code=422, detail='所选学员不在本会话待确认候选中')
    if _is_lesson_change_request(query):
        return chat(db, admin_id, '帮虚构小明增加三节数学课。', conversation_id,
                    selected_student_id, client=None, demo_scope_followup=True)
    if query.strip(' 。！!') in ('取消', '换一个问题', '重新查询'):
        _clear(db, conversation)
        return _clarification(db, admin_id, '已取消，请重新提出查询。', conversation_id)
    if selected_student_id is None and (is_rule_query(query, demo=True) or is_empty_demo_query(query)):
        return chat(db, admin_id, query, conversation_id, demo_scope_followup=True)
    ordinal_selection = bool(pending and CANDIDATE_SELECTION.fullmatch(query))
    selected = selected_student_id is not None or ordinal_selection
    scope = context.get('demo_pending_scope')
    scope_subject = followup_subject(query) if scope else None
    try:
        if not ordinal_selection:
            require_fictional_query(scope_subject if scope_subject else query)
    except ValueError:
        _clear(db, conversation)
        return _answer('error', '输入或模型建议无法安全处理。', conversation_id)
    if (pending and not selected) or (scope and scope_subject is None):
        # A new request abandons the old suggestion before it is interpreted.
        _clear(db, conversation)
        context, pending, scope = {}, [], None
    suggestion = None
    if selected or scope:
        state = context.get(STATE_KEY)
        try:
            at = datetime.fromisoformat(state['at'])
            if at.tzinfo is None or not timedelta(0) <= datetime.now(timezone.utc)-at <= timedelta(minutes=10):
                raise ValueError('expired')
            if scope:
                scope_at = datetime.fromisoformat(scope['at'])
                if scope_at.tzinfo is None or not timedelta(0) <= datetime.now(timezone.utc)-scope_at <= timedelta(minutes=10):
                    raise ValueError('expired scope')
            original = context['pending_query'] if selected else scope['query']
            require_fictional_query(original)
            if state['query_sha256'] != hashlib.sha256(original.encode('utf8')).hexdigest():
                raise ValueError('mismatched request')
            suggestion = validate_source(state['suggestion'], original)
            if scope:
                from app.services.ai_tools import _active_student
                student_id = scope.get('student_id')
                if (type(student_id) is not int
                        or state.get('confirmed_student_id') != student_id):
                    raise ValueError('missing confirmed session identity')
                student = _active_student(db, student_id)
                if student.name != '虚构小明': raise ValueError('unexpected confirmed name')
        except (TypeError, KeyError, ValueError):
            logging.getLogger('jinbi.demo.diagnostics').warning('demo_parameter_diagnostic %s', json.dumps({
                'request_id': DIAGNOSTIC_REQUEST_ID.get(),
                **parameter_diagnostic({'session': {'matched': False, 'source': 'confirmed_session'}})}, sort_keys=True))
            _clear(db, conversation)
            return _clarification(db, admin_id, '查询已失效，请重新提出查询。', conversation_id)
        if scope:
            if suggestion.action != 'clarify' or suggestion.reason != 'missing_scope':
                _clear(db, conversation)
                return _answer('error', '模型建议无法安全恢复。', conversation_id)
            # Both inputs are now authoritative. Complete the scope locally
            # using the same validated read-only tool proposal.
            suggestion = validate_source({
                'intent': 'student_hours',
                'parameters': {'student_name': student.name, 'subject': scope_subject},
                'complete': True, 'ambiguity': 'none', 'action': 'propose_tool',
                'tool': 'get_student_hours', 'reason': 'ready', 'question': '',
            }, query, confirmed=ConfirmedScope(student.name, scope_subject))
    else:
        suggestion = planner.check(query)
    if suggestion is None:
        _clear(db, conversation)
        category = getattr(planner, 'last_diagnostic', {}).get('category')
        code, text = {
            'request_limit': ('model_request_limit', '在线演示模型请求预算已耗尽，未向模型发送请求；重试或重启不会恢复预算，请联系演示负责人。'),
            'budget_error': ('model_budget_error', '在线演示请求预算无法安全读取，未向模型发送请求，请联系演示负责人。'),
            'http_error': ('model_upstream_http_error', '模型上游服务返回错误，已拒绝执行，请核对诊断记录。'),
            'network_error': ('model_transport_error', '模型连接失败，未执行查询，请稍后重试。'),
            'tls_error': ('model_transport_error', '模型安全连接校验失败，已拒绝执行，请核对诊断记录。'),
            'unverified_source': ('model_source_invalid', '模型建议与输入来源不一致，已拒绝执行，请重新提出查询。'),
            'timeout': ('model_timeout', '模型请求超时，请重新提出查询。'),
            'invalid_json': ('model_invalid_json', '模型响应格式无效，已拒绝执行，请重试。'),
            'invalid_schema': ('model_invalid_schema', '模型建议结构无效，已拒绝执行，请重试。'),
        }.get(category, ('model_unavailable', '模型请求失败或建议无效，已拒绝执行，请重试。'))
        return _answer('error', text, conversation_id, code)
    if suggestion.action == 'reject':
        _clear(db, conversation)
        return _clarification(db, admin_id, '不支持该请求。', conversation_id)
    if suggestion.action == 'clarify' and suggestion.reason != 'missing_scope':
        _clear(db, conversation)
        return _clarification(db, admin_id, '请补充明确的学员和查询范围。', conversation_id)
    client = SuggestionClient(suggestion)
    result = chat(db, admin_id, scope_subject if scope else query, conversation_id, selected_student_id,
                  client=client, demo_scope_followup=True)
    saved_conversation = db.get(AiConversation, result['conversationId'])
    new_context = dict(saved_conversation.context or {})
    if new_context.get('candidates') or new_context.get('demo_pending_scope'):
        saved_query = new_context.get('pending_query') or new_context['demo_pending_scope']['query']
        saved_suggestion = suggestion.model_dump()
        if suggestion.action == 'clarify':
            saved_suggestion['question'] = '请明确需要查询的科目。'
        new_context[STATE_KEY] = {'query_sha256': hashlib.sha256(saved_query.encode('utf8')).hexdigest(),
                                  'suggestion': saved_suggestion, 'at': datetime.now(timezone.utc).isoformat()}
        if selected and new_context.get('demo_pending_scope'):
            new_context[STATE_KEY]['confirmed_student_id'] = new_context['demo_pending_scope']['student_id']
        saved_conversation.context = new_context
        db.commit()
    return result
