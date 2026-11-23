import unittest
from types import SimpleNamespace as NS
import httpx
from openai import APITimeoutError, RateLimitError, APIStatusError
from test_ai_query_safety import QuerySafetyTests
from test_function_chat import Client
from app.services.ai_function_chat import run_chat


class SDKFailureTests(QuerySafetyTests):
    def test_real_sdk_exception_types_do_not_leak(self):
        request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions')
        response = httpx.Response(429, request=request)
        cases = [APITimeoutError(request=request), RateLimitError('sensitive-placeholder', response=response, body=None),
                 APIStatusError('sensitive-placeholder', response=httpx.Response(503, request=request), body=None)]
        for error in cases:
            client = Client([])
            def fail(**kwargs): raise error
            client.create = fail
            result = run_chat(self.db, '查询S1数学课时', client)
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('sensitive-placeholder', str(result))

    def test_malformed_response_fails_closed(self):
        for choices in ([], [NS(finish_reason='tool_calls', message=NS(tool_calls=[]))]):
            client = Client([])
            client.create = lambda **kwargs: NS(choices=choices)
            self.assertEqual(run_chat(self.db, '查询S1数学课时', client)['status'], 'error')

if __name__ == '__main__': unittest.main()
