"""Deterministic test planner. Not a model, DB client, or tool dispatcher.

Intentionally not wired into the production application. It must only be
injected by an isolated harness using fictional data.
"""
import json
from types import SimpleNamespace


class OfflineDemoPlanner:
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model, messages, tools, tool_choice, extra_body):
        if not isinstance(messages, list) or len(messages) < 2:
            raise ValueError('invalid message envelope')
        if messages[0].get('role') != 'system' or messages[1].get('role') != 'user':
            raise ValueError('invalid message roles')
        payload = json.loads(messages[1]['content'])
        if (not isinstance(payload,dict) or set(payload) != {'student_ref','subject','task'}
                or payload['task'] != 'hours' or not isinstance(payload['student_ref'],str)
                or not payload['student_ref'] or not isinstance(payload['subject'],str)
                or not payload['subject']):
            raise ValueError('unsupported or malformed task')
        names = [t.get('function',{}).get('name') for t in tools]
        if names != ['get_student_hours','get_lesson_records']:
            raise ValueError('unexpected tool inventory')
        if len(messages)==2 and tool_choice=='auto':
            args=json.dumps({'student_ref':payload['student_ref']},ensure_ascii=False)
            call=SimpleNamespace(id='offline_demo_1',function=SimpleNamespace(name='get_student_hours',arguments=args))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]),finish_reason='tool_calls')])
        if len(messages)==4 and messages[-1].get('role')=='tool' and tool_choice in ('auto','none'):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]),finish_reason='stop')])
        raise ValueError('unexpected planning sequence')
