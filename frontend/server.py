#!/usr/bin/env python3
"""Loopback-only static workspace + optional Dify Workflow gateway (stdlib only)."""
import argparse
import json
import os
import re
import socket
import threading
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
MAX_BODY = 2_000_000
ACTIONS = {'CLARIFY', 'OFFER', 'PROCESS_REQUEST', 'HUMAN_REVIEW', 'FALLBACK'}


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if re.fullmatch(r'[A-Z_][A-Z_0-9]*', key.strip()):
                    os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def configured():
    return bool(os.environ.get('DIFY_API_KEY') and os.environ.get('DIFY_BASE_URL'))


class NoRedirect(HTTPRedirectHandler):
    # Never forward a Bearer credential to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dify_call(path, payload=None, timeout=60):
    base = os.environ.get('DIFY_BASE_URL', '').rstrip('/')
    parts = urlsplit(base)
    if parts.scheme not in {'http', 'https'} or not parts.netloc or parts.username or parts.password:
        raise ValueError('DIFY_BASE_URL 必须是有效的 API 地址（含 /v1）')
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(base + path, data=data, headers={'Authorization': 'Bearer ' + os.environ['DIFY_API_KEY'], 'Content-Type': 'application/json'})
    with build_opener(NoRedirect).open(req, timeout=timeout) as response:
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError('Dify 返回内容过大')
        return json.loads(raw)


def validate_request(body):
    if not isinstance(body, dict) or not isinstance(body.get('session_id'), str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', body['session_id']):
        raise ValueError('会话编号无效')
    if type(body.get('context_version')) is not int or body['context_version'] < 1:
        raise ValueError('会话版本无效')
    messages = body.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 500:
        raise ValueError('会话消息数量无效')
    for m in messages:
        if not isinstance(m, dict) or m.get('role') not in {'customer', 'agent'} or not isinstance(m.get('text'), str) or not 1 <= len(m['text']) <= 1000 or not isinstance(m.get('id'), str):
            raise ValueError('消息格式无效')
    ids = [m['id'] for m in messages]
    if len(ids) != len(set(ids)):
        raise ValueError('消息编号重复')
    return body


def refusal_state(messages, confirmed_id=None):
    refused = False
    for m in messages:
        if m['role'] != 'customer':
            continue
        text = m['text']
        if re.search(r'不用推荐|不要推荐|别推荐|不推荐|不想了解|不愿意|不挽留|别挽留|直接.{0,5}降|只想.{0,5}降|不需要.{0,5}(优惠|方案|推荐)|不接受', text):
            refused = True
        elif re.search(r'(愿意|可以|想|重新).{0,8}(了解|看看|比较|听听)', text) and not re.search(r'不愿意|不想|不可以|不再|不要', text):
            if refused:
                refused = m.get('id') != confirmed_id
    return refused


def validate_result(result, messages, confirmed_id=None):
    if isinstance(result, str):
        result = json.loads(re.sub(r'\s*```$', '', re.sub(r'^```(?:json)?\s*', '', result.strip())))
    if not isinstance(result, dict) or result.get('action') not in ACTIONS:
        raise ValueError('Dify 返回的处置动作无效')
    for key in ('summary', 'customer_reply'):
        if not isinstance(result.get(key), str) or not result[key].strip() or len(result[key]) > 10000:
            raise ValueError('Dify 返回内容不完整：' + key)
    for key in ('facts', 'unknowns', 'reason_codes', 'evidence_ids', 'candidate_policy_ids', 'quote_ids', 'operator_checks'):
        if not isinstance(result.get(key), list) or len(result[key]) > 100:
            raise ValueError('Dify 返回字段格式无效：' + key)
        if key != 'facts' and any(not isinstance(x, str) or len(x) > 2000 for x in result[key]):
            raise ValueError('Dify 返回文本数组格式无效：' + key)
    for fact in result['facts']:
        if not isinstance(fact, dict) or not isinstance(fact.get('key'), str) or not isinstance(fact.get('source_id'), str) or not {'value', 'as_of'} <= fact.keys():
            raise ValueError('Dify 返回事实缺少来源')
    archive = result.get('archive_draft')
    if not isinstance(archive, dict) or not isinstance(archive.get('request'), str) or not isinstance(archive.get('proposed_policy_ids'), list) or 'customer_decision' not in archive or archive.get('handling_status') != 'unconfirmed':
        raise ValueError('归档缺少必要信息，或包含未经回执确认的办理状态')
    if result['action'] == 'OFFER':
        raise ValueError('可信政策、资格和费用校验尚未接入，本版暂不展示推荐方案')
    if result['candidate_policy_ids'] or result['quote_ids']:
        raise ValueError('当前处置动作不允许包含推荐方案')
    if refusal_state(messages, confirmed_id) and result['action'] not in {'PROCESS_REQUEST', 'HUMAN_REVIEW', 'FALLBACK'}:
        raise ValueError('Dify 建议与已记录的拒绝状态冲突')
    last = next((m['text'] for m in reversed(messages) if m['role'] == 'customer'), '')
    if re.search(r'多扣|乱扣|扣错|账单.{0,8}(问题|疑问|不对|贵|错)|账单有|争议|投诉', last) and result['action'] not in {'HUMAN_REVIEW', 'FALLBACK'}:
        raise ValueError('费用争议尚未进入人工核查')
    return result


def run_analysis(body):
    body = validate_request(body)
    # The server selects the demo account; browser-provided snapshots are never trusted.
    customer = json.loads((ROOT.parent / 'poc/customers.json').read_text())['customers'][0]
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    confirmed_id = body.get('confirmed_reconsent_id')
    if confirmed_id is not None and (not isinstance(confirmed_id, str) or confirmed_id not in {m['id'] for m in body['messages'] if m['role'] == 'customer'}):
        raise ValueError('重新同意证据编号无效')
    refused = refusal_state(body['messages'], confirmed_id)
    any_consent = any(re.search(r'(愿意|可以|想|重新).{0,8}(了解|看看|比较|听听)', m['text']) and not re.search(r'不愿意|不想|不可以|不再|不要', m['text']) for m in body['messages'] if m['role'] == 'customer')
    conversation = [{'id': m['id'], 'segment_id': m['id'], 'revision': 1, 'speaker': 'customer' if m['role'] == 'customer' else 'operator', 'text': m['text'], 'as_of': m.get('at', now), 'is_final': True} for m in body['messages']]
    verified = [{'key': 'retention_reconsent', 'value': True, 'source_id': 'OP_RECONSENT_' + body['session_id'], 'as_of': now}] if confirmed_id and not refused else []
    context = {'now': now, 'session_id': body['session_id'], 'context_version': body['context_version'], 'customer_id': customer['customer_id'], 'authorized_account_key': customer['authorized_account_key'], 'conversation': conversation, 'verified_facts': verified, 'synthetic_only': True, 'session_state': {'retention_consent': 'refused' if refused else 'allowed' if any_consent else 'unknown'}, 'usage_assumptions': None, 'quote_month': None}
    if len(json.dumps(context, ensure_ascii=False)) > 100000:
        raise ValueError('会话已超过 Dify 工作流输入限制，请新建会话')
    payload = {'inputs': {os.environ.get('DIFY_INPUT_KEY', 'request_json'): json.dumps(context, ensure_ascii=False)}, 'response_mode': 'blocking', 'user': 'copilot-demo-seat-001'}
    response = dify_call('/workflows/run', payload)
    data = response.get('data', {})
    if data.get('status') != 'succeeded':
        raise ValueError('Dify 工作流未成功完成，请检查发布状态与运行日志')
    outputs = data.get('outputs', {})
    if outputs.get('metadata', {}).get('display_allowed') is False:
        raise ValueError('工作流结果未通过展示校验')
    result = validate_result(outputs.get(os.environ.get('DIFY_OUTPUT_KEY', 'result')), body['messages'], confirmed_id)
    return {'session_id': body['session_id'], 'context_version': body['context_version'], 'result': result, 'workflow_run_id': response.get('workflow_run_id')}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):
        pass  # No conversation, token, or upstream response logging.

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        super().end_headers()

    def json(self, status, data):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def same_origin(self):
        host = self.headers.get('Host', '')
        allowed = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        origin = self.headers.get('Origin')
        return host in allowed and (not origin or origin == 'http://' + host) and self.headers.get('Sec-Fetch-Site') != 'cross-site'

    def do_GET(self):
        if not self.same_origin():
            return self.json(403, {'error': '仅允许本机同源访问'})
        path = urlsplit(self.path).path
        if path == '/api/health':
            if not configured():
                return self.json(200, {'configured': False, 'connected': False, 'error': '本地服务已启动，尚未配置 DIFY_BASE_URL 和 DIFY_API_KEY'})
            if urlsplit(self.path).query != 'verify=1':
                return self.json(200, {'configured': True, 'connected': False})
            try:
                info = dify_call('/info', timeout=8)
                if info.get('mode') != 'workflow':
                    return self.json(200, {'configured': True, 'connected': False, 'error': '本入口需要 Dify Workflow 类型应用'})
                return self.json(200, {'configured': True, 'connected': True, 'name': info.get('name', 'Dify'), 'mode': info['mode']})
            except Exception:
                return self.json(502, {'error': '无法验证 Dify 连接，请检查 API 地址、密钥与应用状态'})
        # Explicit static allowlist: never expose .env, server source, or directory listings.
        permitted = {'/', '/index.html', '/styles.css', '/app.js', '/core.js', '/assets/logo.svg', '/assets/customer.png', '/assets/agent.png'}
        if path not in permitted:
            return self.json(404, {'error': '资源不存在'})
        super().do_GET()

    def do_HEAD(self):
        self.send_error(405)

    def do_POST(self):
        if not self.same_origin():
            return self.json(403, {'error': '仅允许本机同源访问'})
        if self.path != '/api/analyze':
            return self.json(404, {'error': '接口不存在'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                return self.json(413, {'error': '请求体大小无效'})
            if self.headers.get_content_type() != 'application/json':
                return self.json(415, {'error': '需要 application/json'})
            body = json.loads(self.rfile.read(length))
            validate_request(body)
            if not configured():
                return self.json(503, {'error': 'Dify 尚未配置，请在接入设置中使用本地模拟，或配置服务端环境变量'})
            if not self.server.analysis_slot.acquire(blocking=False):
                return self.json(429, {'error': '仍有分析任务执行中，请稍后重试'})
            try:
                result = run_analysis(body)
            finally:
                self.server.analysis_slot.release()
            self.json(200, result)
        except HTTPError as error:
            self.json(502, {'error': 'Dify 请求失败（HTTP ' + str(error.code) + '），请检查服务端配置'})
        except (socket.timeout, TimeoutError):
            self.json(504, {'error': 'Dify 响应超时，可继续人工沟通后重试'})
        except URLError:
            self.json(502, {'error': '无法连接 Dify，请检查本地服务地址'})
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            # Only curated validation messages, never raw JSON or upstream body.
            msg = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else '请求或返回结果格式不符合接入契约'
            self.json(422, {'error': msg})
        except Exception:
            self.json(500, {'error': '接入服务暂时不可用，请检查服务端配置'})


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.analysis_slot = threading.BoundedSemaphore(1)
    print('AI小通：http://127.0.0.1:' + str(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
