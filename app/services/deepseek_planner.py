"""DeepSeek JSON planner adapter for explicit, server-side injection only.

This module never reads configuration or credentials from the environment and is
not wired into either application. Closing the adapter closes its HTTP client.
Only an existing server-confirmed anonymous hours request can be proposed; the
adapter itself has no database access and never dispatches a tool.
"""
import json
import re
from types import SimpleNamespace

from openai import OpenAI
from app.services.offline_demo_planner import OfflineDemoPlanner
from experiments.few_shot.schemas import Suggestion

DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
MAX_RESPONSE_BYTES = 8192
MAX_OUTPUT_TOKENS = 512
REQUEST_TIMEOUT_SECONDS = 20.0
CALL_ID = 'deepseek_proposal_1'

_SYSTEM_PROMPT = '''You are a read-only planning component. Return a single JSON object,
not prose. You are given only a server-confirmed task and subject. Do not infer
student identity, IDs, authorization, or tool arguments. Never propose writes.
For a supported hours task, return exactly this JSON structure, replacing the
subject with the input subject:
{"intent":"student_hours","parameters":{"subject":"数学"},"complete":true,
"ambiguity":"none","action":"propose_tool","tool":"get_student_hours",
"reason":"ready","question":""}
If the task cannot be safely handled, return this JSON instead:
{"intent":"unsupported","parameters":{},"complete":false,"ambiguity":"none",
"action":"reject","tool":null,"reason":"out_of_scope","question":""}'''


class PlannerError(ValueError):
    """A generic, non-sensitive fail-closed planner error."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError('non-finite JSON constant')


def _parse_object(raw, max_bytes):
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode('utf-8')) > max_bytes:
        raise ValueError('empty or oversized JSON')
    value = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('expected JSON object')
    return value


def _reply(calls, reason):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(tool_calls=calls), finish_reason=reason)])


class DeepSeekPlanner:
    """Implements the existing ``client.chat.completions.create`` protocol.

    The injected HTTP client must be supplied explicitly; no application route
    creates this adapter. ``allowed_subjects`` comes from server-owned constants.
    """
    def __init__(self, *, api_key, model, allowed_subjects, http_client):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('explicit API key required')
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', model):
            raise ValueError('invalid model')
        if (not isinstance(allowed_subjects, (tuple, list)) or not allowed_subjects
                or any(not isinstance(s, str) or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9]{1,30}', s)
                       for s in allowed_subjects)):
            raise ValueError('server subject allowlist required')
        if http_client is None:
            raise ValueError('explicit HTTP client required')
        self._subjects = frozenset(allowed_subjects) | {'全部'}
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL,
                              timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0,
                              http_client=http_client)
        self.chat = SimpleNamespace(completions=self)

    def close(self):
        self._client.close()

    def create(self, *, model, messages, tools, tool_choice, extra_body):
        # The caller's model and extra_body are not sent to the remote service.
        try:
            if (not isinstance(messages, list) or len(messages) not in (2, 4)
                    or not all(isinstance(m, dict) for m in messages)
                    or messages[0].get('role') != 'system'
                    or messages[1].get('role') != 'user'):
                raise ValueError('invalid message envelope')
            payload = _parse_object(messages[1].get('content'), 1024)
            if (set(payload) != {'student_ref', 'subject', 'task'}
                    or payload['task'] != 'hours'
                    or not isinstance(payload['student_ref'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', payload['student_ref'])
                    or not isinstance(payload['subject'], str)
                    or payload['subject'] not in self._subjects):
                raise ValueError('invalid controlled input')
            if (not isinstance(tools, list) or len(tools) != 2
                    or [t.get('function', {}).get('name') for t in tools]
                    != ['get_student_hours', 'get_lesson_records']):
                raise ValueError('invalid tool inventory')
            if len(messages) == 4:
                expected_args = {'student_ref': payload['student_ref']}
                assistant, tool = messages[2:]
                calls = assistant.get('tool_calls')
                if (tool_choice not in ('auto', 'none') or assistant.get('role') != 'assistant'
                        or not isinstance(calls, list) or len(calls) != 1
                        or not isinstance(calls[0], dict) or calls[0].get('id') != CALL_ID
                        or calls[0].get('type') != 'function'
                        or not isinstance(calls[0].get('function'), dict)
                        or calls[0]['function'].get('name') != 'get_student_hours'
                        or _parse_object(calls[0]['function'].get('arguments'), 512) != expected_args
                        or tool.get('role') != 'tool' or tool.get('tool_call_id') != CALL_ID
                        or tool.get('content') != '{"executed":true}'):
                    raise ValueError('invalid tool completion')
                return _reply([], 'stop')
            if tool_choice != 'auto':
                raise ValueError('invalid tool choice')
            # Never forward the raw user question, name, anonymous ref, auth,
            # tool history, DB data, or the caller's messages to the model.
            safe_input = json.dumps({'task': 'student_hours', 'subject': payload['subject']}, ensure_ascii=False)
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{'role': 'system', 'content': _SYSTEM_PROMPT},
                          {'role': 'user', 'content': safe_input}],
                response_format={'type': 'json_object'}, stream=False,
                max_tokens=MAX_OUTPUT_TOKENS, timeout=REQUEST_TIMEOUT_SECONDS,
                extra_body={'thinking': {'type': 'disabled'}},
            )
            if len(response.choices) != 1:
                raise ValueError('invalid choices')
            choice = response.choices[0]
            if (choice.finish_reason != 'stop' or choice.message.role != 'assistant'
                    or choice.message.tool_calls or getattr(choice.message, 'refusal', None)):
                raise ValueError('incomplete output')
            suggestion = Suggestion.model_validate(_parse_object(choice.message.content, MAX_RESPONSE_BYTES))
            # The input supplied no identity or class type, so neither may be
            # introduced by a suggestion, including a non-execution response.
            if set(suggestion.parameters) - {'subject'} or ('subject' in suggestion.parameters
                    and suggestion.parameters['subject'] != payload['subject']):
                raise ValueError('unverified parameters')
            if suggestion.action != 'propose_tool':
                return _reply([], 'stop')
            if (suggestion.intent != 'student_hours' or suggestion.tool != 'get_student_hours'
                    or suggestion.parameters != {'subject': payload['subject']}):
                raise ValueError('unsupported suggestion')
            args = json.dumps({'student_ref': payload['student_ref']}, ensure_ascii=False)
            call = SimpleNamespace(id=CALL_ID, function=SimpleNamespace(name='get_student_hours', arguments=args))
            return _reply([call], 'tool_calls')
        except Exception:
            # SDK, transport, JSON, and validation errors are deliberately not
            # exposed: their messages may contain request headers or content.
            raise PlannerError('规划服务不可用或建议无效') from None


def build_planner(mode, *, api_key=None, model=None, allowed_subjects=None, http_client=None):
    """Explicit mode selection for a trusted caller; no application enables it."""
    if mode == 'offline_deterministic':
        if any(value is not None for value in (api_key, model, allowed_subjects, http_client)):
            raise ValueError('offline mode does not accept online configuration')
        return OfflineDemoPlanner()
    if mode == 'deepseek_online':
        return DeepSeekPlanner(api_key=api_key, model=model, allowed_subjects=allowed_subjects,
                               http_client=http_client)
    raise ValueError('unsupported planner mode')
