"""Local identity resolution and privacy-preserving read-only model planning."""
import json
import re
import secrets
import time
from sqlalchemy import select
from app.models.student import Student
from app.services.ai_query_service import _active_student_filter, COMMON_SUBJECT_NAMES
from app.services.ai_tools import execute_tool
from app.core.config import settings

SUBJECTS = tuple(COMMON_SUBJECT_NAMES)


def resolve_student(db, query):
    students = db.scalars(select(Student).where(_active_student_filter()).order_by(Student.id)).all()
    # Only exact stored identifiers are matched. Multiple matches are never guessed.
    def explicit(value):
        return bool(value and re.search(r'(?<![A-Za-z0-9])' + re.escape(value) + r'(?![A-Za-z0-9])', query))
    ids = [s for s in students if any(explicit(x) for x in (s.phone, s.student_no))]
    names = [s for s in students if s.name and s.name in query]
    if ids and names:
        matched = [s for s in ids if s in names]
        return matched if matched else None
    return ids or names


def candidate(s):
    return {'studentId': s.id, 'studentName': s.name, 'studentNo': s.student_no, 'grade': s.grade,
            'phone': s.phone[:3] + '****' + s.phone[-4:] if len(s.phone) == 11 else '****'}


def response(status, answer, data=None, trace=None, candidates=None):
    return {'status': status, 'answer': answer, 'data': data or [], 'trace': trace or [], 'candidates': candidates or []}


def run_chat(db, query, client=None, confirmed_student_id=None, *, demo=False):
    # A supplied client is test-only. Production external calls remain disabled.
    if client is None:
        return response('degraded', '外部模型调用尚未启用，当前不能执行模型规划。')
    if re.search(r'扣课|扣除|加课|修改|删除|退费|收费|转账|支付', query):
        return response('clarification', '请前往人工操作页面核对并确认；助手仅提供只读查询。')
    hits = resolve_student(db, query)
    if hits is None:
        return response('clarification', '姓名与其他身份信息不一致，请重新确认。')
    if confirmed_student_id is None and len(hits) == 1:
        item = hits[0]
        identifier = any(value and re.search(r'(?<![A-Za-z0-9])' + re.escape(value) + r'(?![A-Za-z0-9])', query) for value in (item.phone, item.student_no))
        if not identifier:
            return response('clarification', '请确认候选学员。', candidates=[candidate(item)])
    if confirmed_student_id is not None and hits:
        hits = [s for s in hits if s.id == confirmed_student_id]
        if not hits:
            return response('clarification', '学员身份不一致，请重新选择。')
    if len(hits) > 1:
        return response('clarification', '找到多个学员，请选择候选。', candidates=[candidate(s) for s in hits])
    if hits:
        if confirmed_student_id is not None and hits[0].id != confirmed_student_id:
            return response('clarification', '学员身份不一致，请重新选择。')
        student_id = hits[0].id
    else:
        student_id = confirmed_student_id
    if not student_id:
        if demo and re.search(r'(?:查(?:询|看)?|看看|找(?:一下)?)[^，。！？；]{2,40}?(?:的|学生|学员)[^。！？；]*课', query):
            return response('clarification', '未找到匹配的学生，请核对姓名或学号后重试。')
        return response('clarification', '请提供明确的学员身份。')
    # Revalidate active status on each query. Access scope is the current single institution.
    if db.scalar(select(Student.id).where(Student.id == student_id, _active_student_filter())) is None:
        return response('error', '学员不存在或已删除。')
    subjects = [s for s in SUBJECTS if s in query]
    if len(subjects) > 1:
        return response('clarification', '请一次指定一个科目。')
    # Empty scope is permitted only for a clearly expressed, unqualified
    # request for all subjects. Anything else must be clarified locally.
    if not subjects:
        scope_text = query
        for item in hits or []:
            for identifier in (item.name, item.phone, item.student_no):
                if identifier:
                    scope_text = scope_text.replace(identifier, '')
        all_scope = r'(?:全部|所有|各个|各)(?:科目|学科|课程)'
        safe_words = r'(?:查询|查看|查|请|帮我|帮|我|看看|看|下|一下|该学员|这个学员|这位学员|该学生|这个学生|学生|学员|他的|她的|他|她|的|还有|还剩|剩余|剩下|多少|课时|课程|小时|余额|情况|总共|合计|是|有|吗|呢|以及|和|与|\s|[，,。？?！!：:])'
        if not re.search(all_scope, scope_text) or not re.fullmatch(f'(?:{all_scope}|{safe_words})+', scope_text):
            return response('clarification', '请明确需要查询的科目；如需全部科目请明确说明。')
    elif re.search(r'(?:全部|所有|各个|各)(?:科目|学科|课程)|除了|排除|不要|不查', query) or any(re.search(re.escape(s) + r'\s*(?:和|与|及|、)', query) or
             re.search(r'(?:和|与|及|、)\s*' + re.escape(s), query) for s in subjects):
        return response('clarification', '请一次明确指定一个科目。')
    subject = subjects[0] if subjects else ''
    intent = 'get_lesson_records' if re.search('流水|记录|明细', query) else 'get_student_hours'
    ref = secrets.token_urlsafe(18)
    # Only locally generated finite values enter the request; raw query and history never do.
    safe_query = {'student_ref': ref, 'subject': subject or '全部', 'task': 'records' if intent.endswith('records') else 'hours'}
    tools = [{'type': 'function', 'function': {'name': name, 'description': '查询已确认对象的只读数据', 'parameters': {
        'type': 'object', 'properties': {'student_ref': {'type': 'string'}}, 'required': ['student_ref'], 'additionalProperties': False}}}
        for name in ('get_student_hours', 'get_lesson_records')]
    messages = [{'role': 'system', 'content': '选择与任务匹配的只读工具，使用给定匿名引用。'},
                {'role': 'user', 'content': json.dumps(safe_query, ensure_ascii=False)}]
    trace, data, used_call_ids = [], [], set()
    try:
        for round_index in range(3):
            kwargs = {'model': settings.deepseek_model, 'messages': list(messages), 'tools': tools,
                      'tool_choice': 'none' if round_index == 2 else 'auto',
                      'extra_body': {'thinking': {'type': 'disabled'}}}
            choice = client.chat.completions.create(**kwargs).choices[0]
            msg = choice.message
            calls = msg.tool_calls or []
            reason = choice.finish_reason
            if not calls:
                if reason != 'stop':
                    return response('error', '模型未正常结束。', data, trace)
                return response('complete' if data else 'clarification', '查询完成，请查看结果。' if data else '模型未选择查询工具。', data, trace)
            if reason != 'tool_calls' or round_index == 2:
                return response('error', '模型工具调用未正常结束或已达上限。', data, trace)
            if len(trace) + len(calls) > 4:
                return response('error', '工具调用次数已达上限。', data, trace)
            safe_calls = []
            for call in calls:
                name = call.function.name
                def unique_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError('duplicate key')
                        result[key] = value
                    return result
                if not isinstance(call.function.arguments, str) or len(call.function.arguments) > 512:
                    return response('error', '工具参数校验失败。', data, trace)
                args = json.loads(call.function.arguments, object_pairs_hook=unique_pairs)
                if (name != intent or not isinstance(args, dict) or set(args) != {'student_ref'}
                    or not isinstance(args['student_ref'], str) or args['student_ref'] != ref or not isinstance(call.id, str)
                    or call.id in used_call_ids or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', call.id)):
                    return response('error', '工具参数或权限校验失败。', data, trace)
                used_call_ids.add(call.id)
                safe_calls.append({'id': call.id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps({'student_ref': ref})}})
            messages.append({'role': 'assistant', 'content': None, 'tool_calls': safe_calls})
            for call in calls:
                start = time.monotonic()
                local = execute_tool(db, intent, {'student_id': student_id, 'subject': subject})
                data.append({'tool': intent, 'result': local})
                trace.append({'tool': intent, 'status': 'ok', 'durationMs': round((time.monotonic()-start)*1000)})
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': '{"executed":true}'})
        return response('error', '调用次数已达上限。', data, trace)
    except Exception:
        return response('error', '模型或查询服务暂不可用，请稍后重试。', data, trace)
