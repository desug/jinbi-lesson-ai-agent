import copy
import unittest
from unittest.mock import patch
from sqlalchemy import select
from app.models.ai_memory import AiConversation
from app.models.student import Student
from app.services import ai_function_chat, demo_mock_online
from app.services.offline_demo_planner import OfflineDemoPlanner
import test_d4_r2_p3_mock_online as p3
READY, MISSING = p3.READY, p3.MISSING

class SuggestionConsumptionTests(p3.MockOnlineTests):
    def test_model_proposal_is_consumed_by_original_validator(self):
        observed=[]
        original=demo_mock_online.SuggestionClient.create
        def record(client, **kwargs):
            result=original(client,**kwargs)
            for call in result.choices[0].message.tool_calls:
                observed.append((client.suggestion.tool,call.function.name,client.suggestion.parameters['subject']))
            return result
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline planner forbidden')) as offline, \
             patch.object(demo_mock_online.SuggestionClient,'create',new=record), \
             patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            start=self.post('帮我看看虚构小明数学课还有没有剩余。'); self.assertEqual(len(start['candidates']),2)
            self.assertEqual(tool.call_count,0)
            result=self.post('第二位。',start['conversationId'])
            self.assertEqual(result['data'][0]['result']['subjects'][0]['remainingHours'],10)
            self.assertEqual(observed,[('get_student_hours','get_student_hours','数学')])
            self.assertEqual(tool.call_args.args[1: ],('get_student_hours',{'student_id':2,'subject':'数学'}))
            self.assertEqual(len(self.requests),1); self.assertEqual(offline.call_count,0); self.assertEqual(tool.call_count,1)
            self.post('第二位。',start['conversationId']); self.assertEqual(tool.call_count,1)
    def test_validator_and_active_identity_are_still_authoritative(self):
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline planner forbidden')), \
             patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            start=self.post('帮我看看虚构小明数学课还有没有剩余。')
            with self.app.state.demo_sessions() as db:
                student=db.get(Student,2)
                # A confirmed selection must match an active student when resumed.
                student.is_deleted=True; db.commit()
            result=self.post('第二位。',start['conversationId']); self.assertFalse(result['data']); self.assertEqual(tool.call_count,0)
        before=self.snapshot()
        self.assertEqual(before,self.snapshot())
    def test_expiry_cancel_switch_and_ownership(self):
        def pending(): return self.post('帮我看看虚构小明数学课还有没有剩余。')['conversationId']
        other=self.http.post('/auth/admin-login',json={'username':'fictional-demo-2','password':'fictional-demo-only'}).json()['token']
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            cid=pending(); r=self.http.post('/admin/ai/chat',headers={'Authorization':'Bearer '+other},json={'query':'第二位。','conversationId':cid}); self.assertEqual(r.status_code,404)
            with self.app.state.demo_sessions() as db:
                c=db.get(AiConversation,cid); state=copy.deepcopy(c.context); state['demo_model_suggestion']['at']='2000-01-01T00:00:00+00:00'; c.context=state; db.commit()
            self.assertFalse(self.post('第二位。',cid)['data'])
            cid=pending(); self.post('取消',cid); self.assertFalse(self.post('第二位。',cid)['data'])
            cid=pending(); self.output=MISSING
            changed=self.post('查一下虚构小明还剩多少课。',cid); self.assertEqual(len(changed['candidates']),2)
            self.post('第二位。',cid); self.assertEqual(tool.call_count,0)
            self.assertFalse(self.post('取消',cid)['data']); self.assertFalse(self.post('数学',cid)['data'])
            self.assertEqual(tool.call_count,0)
    def test_scope_resume_uses_confirmed_context_without_another_model_call(self):
        self.output=MISSING
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline planner forbidden')) as offline, \
             patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            first=self.post('查一下虚构小明还剩多少课。'); cid=first['conversationId']
            self.post('第二位。',cid); self.assertEqual(tool.call_count,0)
            self.output=READY; result=self.post('数学',cid)
            self.assertEqual(result['data'][0]['result']['subjects'][0]['remainingHours'],10)
            self.assertEqual(len(self.requests),1); self.assertEqual(offline.call_count,0)
            self.assertEqual(tool.call_args.args[1:],('get_student_hours',{'student_id':2,'subject':'数学'}))
            self.assertEqual(tool.call_count,1); self.post('数学',cid); self.assertEqual(tool.call_count,1)

    def test_conflicting_name_and_unapproved_parameter_fail_closed(self):
        before=self.snapshot()
        invalid=[{**READY,'parameters':{'student_name':'虚构小红','subject':'数学'}},
                 {**READY,'parameters':{'student_name':'虚构小明','subject':'数学','class_type':'VIP'}}]
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            for value in invalid:
                self.output=value; self.assertEqual(self.post('帮我看看虚构小明数学课还有没有剩余。')['status'],'error')
            self.assertEqual(tool.call_count,0)
        self.assertEqual(before,self.snapshot())

    def test_original_tool_argument_guard_blocks_forgery(self):
        import json
        original=demo_mock_online.SuggestionClient.create
        def forged(client, **kwargs):
            out=original(client,**kwargs)
            calls=out.choices[0].message.tool_calls
            if calls: calls[0].function.arguments=json.dumps({'student_ref':'forged'})
            return out
        before=self.snapshot()
        with patch.object(demo_mock_online.SuggestionClient,'create',new=forged), \
             patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline planner forbidden')), \
             patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            first=self.post('帮我看看虚构小明数学课还有没有剩余。')
            result=self.post('第二位。',first['conversationId'])
            self.assertFalse(result['data']); self.assertEqual(tool.call_count,0)
        self.assertEqual(before,self.snapshot())

    def test_scope_expiry_prevents_replanning_and_execution(self):
        self.output=MISSING
        first=self.post('查一下虚构小明还剩多少课。'); cid=first['conversationId']
        self.post('第二位。',cid)
        with self.app.state.demo_sessions() as db:
            c=db.get(AiConversation,cid); state=copy.deepcopy(c.context)
            state['demo_pending_scope']['at']='2000-01-01T00:00:00+00:00'; c.context=state; db.commit()
        self.output=READY
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            self.assertFalse(self.post('数学',cid)['data']); self.assertEqual(tool.call_count,0)
        self.assertEqual(len(self.requests),1)

    def test_clarify_reject_and_tampering_never_dispatch(self):
        reject={'intent':'unsupported','parameters':{},'complete':False,'ambiguity':'none','action':'reject','tool':None,'reason':'out_of_scope','question':''}
        clarify={'intent':'clarification','parameters':{},'complete':False,'ambiguity':'conflict','action':'clarify','tool':None,'reason':'conflict','question':'请确认'}
        before=self.snapshot()
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline planner forbidden')), \
             patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            for output in (reject,clarify):
                self.output=output; answer=self.post('帮我看看虚构小明数学课还有没有剩余。'); self.assertEqual(answer['status'],'clarification'); self.assertFalse(answer['data'])
            self.output=READY; cid=self.post('帮我看看虚构小明数学课还有没有剩余。')['conversationId']
            with self.app.state.demo_sessions() as db:
                c=db.get(AiConversation,cid); state=copy.deepcopy(c.context); state['demo_model_suggestion']['suggestion']['parameters']['subject']='英语'; c.context=state; db.commit()
            self.assertFalse(self.post('第二位。',cid)['data']); self.assertEqual(tool.call_count,0)
        self.assertEqual(before,self.snapshot())
