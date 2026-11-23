"""Local-only independent-process smoke check for the isolated offline demo."""
import sys
import httpx


def main(port: int):
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10, trust_env=False) as client:
        assert client.get('/health').json() == {'status': 'ok', 'mode': 'offline_deterministic'}
        def login(name, password):
            response = client.post('/auth/admin-login', json={'username': name, 'password': password})
            assert response.status_code == 200
            return {'Authorization': 'Bearer ' + response.json()['token']}
        ordinary = login('fictional-demo', 'fictional-demo-only')
        other = login('fictional-demo-2', 'fictional-demo-only')
        def ask(query, *, conversation=None, headers=ordinary):
            payload = {'query': query}
            if conversation is not None:
                payload['conversationId'] = conversation
            response = client.post('/admin/ai/chat', headers=headers, json=payload)
            assert response.status_code == 200, response.status_code
            return response.json()
        first = ask('查询虚构小明剩余课时')
        assert len(first['candidates']) == 2
        cid = first['conversationId']
        assert client.post('/admin/ai/chat', headers=other, json={'query': '数学', 'conversationId': cid}).status_code == 404
        assert '科目' in ask('选择第二位', conversation=cid)['answer']
        hours = ask('数学', conversation=cid)
        assert hours['status'] == 'complete'
        before = hours['data'][0]['result']['subjects'][0]['remainingHours']
        assert before == 10
        for query, document in [('请假有什么规定？', 'DEMO-JINBI-LEAVE'), ('补课有什么规定？', 'DEMO-JINBI-MAKEUP')]:
            result = ask(query)
            assert any(c['document_id'] == document and c['supporting_excerpt'] for c in result['citations'])
        rejected = ask('帮虚构小明增加三节数学课')
        assert not rejected['data'] and not rejected['trace']
        assert client.post('/admin/demo-rules', headers=ordinary, json={}).status_code == 403
        assert client.post('/admin/lesson/change', headers=ordinary, json={}).status_code == 404
        assert client.post('/admin/ai/chat', json={'query': '数学'}).status_code == 401
        assert client.post('/admin/ai/chat', headers={'Authorization': 'Bearer invalid'}, json={'query': '数学'}).status_code == 401
        again = ask('查询虚构小明剩余课时')
        cid = again['conversationId']
        ask('选择第二位', conversation=cid)
        after = ask('数学', conversation=cid)['data'][0]['result']['subjects'][0]['remainingHours']
        assert after == before
    print('offline HTTP smoke: health, auth, multi-turn, RAG, read-only, isolation OK')


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) == 2 else 18765)
