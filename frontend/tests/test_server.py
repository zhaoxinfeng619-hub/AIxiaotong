import copy
import importlib.util
import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('copilot_server', Path(__file__).parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def fixture():
    return {'summary': '需要降低费用', 'facts': [], 'unknowns': ['预算'], 'action': 'CLARIFY', 'reason_codes': ['BUDGET'], 'evidence_ids': ['MSG-1'], 'candidate_policy_ids': [], 'quote_ids': [], 'operator_checks': ['确认预算'], 'customer_reply': '您希望每月费用控制在多少？', 'archive_draft': {'request': '需要降低费用', 'proposed_policy_ids': [], 'customer_decision': None, 'handling_status': 'unconfirmed'}}


def body(text='套餐有点贵'):
    return {'session_id': 'DEMO-test', 'context_version': 3, 'messages': [{'id': 'MSG-1', 'text': text, 'role': 'customer'}]}


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.http.analysis_slot = threading.BoundedSemaphore(1)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def request(self, method, path, data=None, headers=None):
        c = HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        raw = None if data is None else json.dumps(data)
        c.request(method, path, raw, {'Content-Type': 'application/json', **(headers or {})})
        r = c.getresponse()
        content = r.read()
        c.close()
        return r.status, content

    def test_no_secrets_or_source_served(self):
        for path in ['/.env', '/server.py', '/tests/test_server.py', '/assets/', '/%2e%2e/.env']:
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_cross_origin_mutation_rejected(self):
        self.assertEqual(self.request('POST', '/api/analyze', body(), {'Origin': 'https://example.com'})[0], 403)

    def test_unconfigured_health_and_analysis(self):
        with patch.dict(server.os.environ, {'DIFY_API_KEY': '', 'DIFY_BASE_URL': ''}):
            status, content = self.request('GET', '/api/health')
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(content)['connected'])
            self.assertEqual(self.request('POST', '/api/analyze', body())[0], 503)

    def test_mocked_workflow_contract_and_snapshot_source(self):
        def dify_call(path, payload):
            self.assertEqual(path, '/workflows/run')
            self.assertEqual(payload['response_mode'], 'blocking')
            context = json.loads(payload['inputs']['request_json'])
            self.assertEqual(context['customer_id'], 'C001')
            self.assertEqual(context['conversation'][0]['speaker'], 'customer')
            self.assertTrue(context['now'].endswith('+08:00'))
            self.assertEqual(context['context_version'], 3)
            return {'data': {'status': 'succeeded', 'outputs': {'result': json.dumps(fixture())}}, 'workflow_run_id': 'run-test'}
        with patch.object(server, 'dify_call', side_effect=dify_call):
            result = server.run_analysis(body())
        self.assertEqual(result['session_id'], 'DEMO-test')
        self.assertEqual(result['context_version'], 3)
        self.assertEqual(result['result']['action'], 'CLARIFY')

    def test_reject_offers_and_false_completion(self):
        r = fixture()
        r['action'] = 'OFFER'
        with self.assertRaises(ValueError):
            server.validate_result(r, body()['messages'])
        r = fixture()
        r['archive_draft']['handling_status'] = 'completed'
        with self.assertRaises(ValueError):
            server.validate_result(r, body()['messages'])

    def test_refusal_and_dispute_guards(self):
        for text in ['不用推荐了', '这个月多扣了钱']:
            with self.assertRaises(ValueError):
                server.validate_result(fixture(), body(text)['messages'])

    def test_renewed_consent_requires_confirmation_but_initial_consent_does_not(self):
        messages = [{'id': 'yes', 'role': 'customer', 'text': '我愿意了解其他方案'}]
        self.assertFalse(server.refusal_state(messages))
        messages.insert(0, {'id': 'no', 'role': 'customer', 'text': '不用推荐'})
        self.assertTrue(server.refusal_state(messages))
        self.assertFalse(server.refusal_state(messages, 'yes'))

    def test_failed_workflow_not_displayed(self):
        with patch.object(server, 'dify_call', return_value={'data': {'status': 'failed', 'error': 'private internal data'}}):
            with self.assertRaisesRegex(ValueError, '未成功完成'):
                server.run_analysis(body())


if __name__ == '__main__':
    unittest.main()
