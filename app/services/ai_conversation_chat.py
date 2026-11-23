"""Conversation orchestration: ownership and explicit candidate selection."""
import re
from fastapi import HTTPException
from sqlalchemy import select
from app.models.ai_memory import AiConversation, AiMessage
from app.services.ai_function_chat import run_chat

ORDINALS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
CANDIDATE_SELECTION = re.compile(
    r'\s*(?:选择|选)?第?([一二两三四五六七八九十\d]+)(?:个|位)?(?:候选(?:学员|学生)?)?\s*[。！!]?\s*'
)
RULE_QUERY = re.compile(r'规则|规定|要求|政策|如何计算|是否允许|扣几课时|怎么扣课')
DEMO_MAKEUP_COUNT_QUERY = re.compile(
    r'请假[^。！？；\n]*(?:补(?:课)?\s*(?:几|多少)\s*次|(?:几|多少)\s*次补课)'
)
DEMO_MAKEUP_TIME_QUERY = re.compile(
    r'补课的?时间(?:是)?(?:怎么|如何|由谁|谁来)(?:安排|协商|确定|决定|定)'
    r'|(?:怎么|如何)(?:安排|确定|决定|定)补课的?时间'
)
DEMO_RULE_QUERY = re.compile(
    r'(?:怎么|如何)(?:申请|办理|安排)?(?:请假|补课)'
    r'|(?:请假|补课)的?(?:流程|手续|(?:时间)?(?:怎么|如何)(?:办|处理|申请|安排|确定))'
)


def is_rule_query(query, *, demo=False):
    return bool(RULE_QUERY.search(query) or
                (demo and (DEMO_RULE_QUERY.search(query) or DEMO_MAKEUP_COUNT_QUERY.search(query)
                           or DEMO_MAKEUP_TIME_QUERY.search(query))))


def is_empty_demo_query(query):
    return not any(char.isalnum() for char in query)


def _is_lesson_change_request(query):
    # Quoted examples are not themselves instructions. The existing guard is
    # retained; the additional pattern requires a request and a lesson scope.
    unquoted = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', '', query)
    existing = re.search(r'(?:直接|帮我|请|把).*(?:扣课|扣除|加课|修改|删除|退费|收费)', query)
    lesson_change = re.search(
        r'(?:直接|帮(?:我)?|请(?!问|假|教|解释|说明)|把|将|给|为|替)[^。！？；\n]*?'
        r'(?:增加|加(?:上)?|补|扣除|扣|减少)\s*(?:[0-9一二两三四五六七八九十百]+\s*(?:节|课时)|课时)',
        unquoted,
    )
    bare_change = re.match(r'\s*(?:增加|加(?:上)?|补|扣除|扣|减少)\s*[0-9一二两三四五六七八九十百]+\s*(?:节|课时)', unquoted)
    # An explicit standalone modification command needs a lesson-hours target;
    # questions about the applicable policy remain rule inquiries.
    inquiry = re.search(r'什么|哪些|如何|怎么|是否|吗|能否|能不能|需不需要|需要[^。！？；\n]*(?:规则|审批|手续|规定|要求)', unquoted)
    modify_hours = re.match(r'\s*(?:修改|更改|调整)[^。！？；\n]{1,80}?课时', unquoted) if not inquiry else None
    return bool(existing or lesson_change or bare_change or modify_hours)


def chat(db, admin_id, query, conversation_id=None, selected_student_id=None, client=None, *, demo_scope_followup=False):
    if conversation_id:
        conversation = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id, AiConversation.admin_id == admin_id))
        if not conversation:
            raise HTTPException(status_code=404, detail='会话不存在')
    else:
        if selected_student_id is not None:
            raise HTTPException(status_code=422, detail='没有待确认候选')
        conversation = AiConversation(admin_id=admin_id, title=query[:80], context={}, summary='')
        db.add(conversation)
        db.flush()
    context = dict(conversation.context or {})
    pending = context.get('candidates', [])
    write_requested = _is_lesson_change_request(query)
    selected = None
    match = CANDIDATE_SELECTION.fullmatch(query)
    if selected_student_id is not None:
        if not any(s['studentId'] == selected_student_id for s in pending):
            raise HTTPException(status_code=422, detail='所选学员不在本会话待确认候选中')
        if not write_requested:
            selected = next(s for s in pending if s['studentId'] == selected_student_id)
    elif not write_requested and pending and match:
        raw = match.group(1)
        index = int(raw) if raw.isdigit() else ORDINALS.get(raw, 0)
        if not 1 <= index <= len(pending):
            raise HTTPException(status_code=422, detail='候选序号无效')
        selected = pending[index-1]
    if write_requested:
        result = {'status': 'clarification', 'answer': '教务助手不支持修改课时或其他业务数据，请前往人工操作页面核对并确认。', 'data': [], 'trace': [], 'candidates': []}
        context = {}
    elif demo_scope_followup and not selected and is_empty_demo_query(query):
        result = {'status': 'clarification', 'answer': '请说明要查询的学生课时或教学规则。', 'data': [], 'trace': [], 'candidates': pending}
    elif (pending and not selected and not re.search(r'重新查询|换一个问题|取消', query)
          and not (demo_scope_followup and is_rule_query(query, demo=True))):
        result = {'status': 'clarification', 'answer': '请选择候选学员，或输入取消。', 'data': [], 'trace': [], 'candidates': pending}
    elif not selected and is_rule_query(query, demo=demo_scope_followup):
        from app.services.ai_tools import execute_tool
        result = execute_tool(db, 'search_demo_academic_rules', {'query': query})
        if demo_scope_followup and result['status'] == 'no_approved_rules':
            result['answer'] = '目前没有找到相关规定。'
        result.update(data=[], trace=[], candidates=[])
        context = {}
    elif demo_scope_followup and not selected and context.get('demo_pending_scope'):
        from datetime import datetime, timezone, timedelta
        from app.services.ai_function_chat import SUBJECTS
        state=context['demo_pending_scope']
        try:
            recorded=datetime.fromisoformat(state['at'])
            valid=recorded.tzinfo is not None and timedelta(0) <= datetime.now(timezone.utc)-recorded <= timedelta(minutes=10)
        except (KeyError,ValueError,TypeError):
            valid=False
        subject=query.strip().strip('。！？?! ')
        if valid and subject in SUBJECTS and isinstance(state.get('student_id'),int) and isinstance(state.get('query'),str):
            result=run_chat(db,state['query']+' '+subject,client=client,confirmed_student_id=state['student_id'])
            context={}
        else:
            result=run_chat(db,query,client=client,confirmed_student_id=None,demo=True)
            context={'candidates':result['candidates'],'pending_query':query} if result.get('candidates') else {}
    elif selected:
        target = selected['studentId']
        actual_query = context.get('pending_query', '')
        result = run_chat(db, actual_query, client=client, confirmed_student_id=target)
        context = {'student_id': target}
        if demo_scope_followup and result.get('answer') == '请明确需要查询的科目；如需全部科目请明确说明。':
            from datetime import datetime, timezone
            context={'demo_pending_scope':{'student_id':target,'query':actual_query,'at':datetime.now(timezone.utc).isoformat()}}
    else:
        # Only explicit pronouns inherit identity; a new named request cannot reuse it.
        target = context.get('student_id') if re.search(r'他|她|该学员|这个学员', query) else None
        demo_options = {'demo': True} if demo_scope_followup else {}
        result = run_chat(db, query, client=client, confirmed_student_id=target, **demo_options)
        if result.get('candidates'):
            context = {'candidates': result['candidates'], 'pending_query': query}
        elif target:
            context = {'student_id': target}
        else:
            context = {}
    result['conversationId'] = conversation.id
    context_message = AiMessage(conversation_id=conversation.id, role='user', content=query, result={})
    answer = AiMessage(conversation_id=conversation.id, role='assistant', content=result['answer'], result=result)
    db.add_all([context_message, answer])
    conversation.context = context
    from datetime import datetime, timezone
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return result
