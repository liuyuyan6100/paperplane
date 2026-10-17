"""Text transport contract: bounded SSE, no partial acceptance or paid replays."""
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from studio import adapters
from studio.service import Studio


def event(value):
    return ('data: ' + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + '\n\n').encode()


def delta(text=None, reason=None, **extra):
    return {'model': 'reported-model', 'choices': [{'index': 0, 'delta':
            ({'content': text} if text is not None else {}) | extra, 'finish_reason': reason}]}


def stream(text='中文正文'):
    return event(delta(text)) + event(delta(reason='stop')) + event('[DONE]')


class Response:
    def __init__(self, chunks, content_type='text/event-stream'):
        self.chunks = iter(chunks)
        self.headers = {'Content-Type': content_type}
        self.timeouts = []
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))

    def read1(self, size):
        chunk = next(self.chunks, b'')
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def invoke(response):
    with patch('studio.adapters.urllib.request.build_opener') as build:
        build.return_value.open.return_value = response
        return adapters.request_json('https://synthetic.invalid/v1/chat/completions', {'stream': True}, model=True, timeout=90)


def test_fragmented_utf8_multiline_heartbeat_reasoning_identity_and_usage():
    raw = b': ping\r\n\r\nevent: ping\ndata: keepalive\n\n' + event(delta(reasoning_content='INTERNAL_REASONING'))
    raw += b'event: message\ndata: {"model":"reported-model",\ndata: "choices":[{"index":1,"delta":{"content":"OTHER"}}]}\n\n'
    raw += event(delta('正文🐤')) + event(delta(reason='stop'))
    raw += event({'model': 'reported-model', 'choices': [], 'usage': {'total_tokens': 123}}) + event('[DONE]')
    response = Response([raw[i:i+1] for i in range(len(raw))])
    result = invoke(response)
    assert result['choices'][0]['message']['content'] == '正文🐤'
    assert result['model'] == 'reported-model'
    assert result['usage'] == {'total_tokens': 123}
    assert 'INTERNAL_REASONING' not in json.dumps(result)
    assert response.timeouts and all(0 < timeout <= 90 for timeout in response.timeouts)


@pytest.mark.parametrize('raw', [
    event(delta('partial')),
    event(delta('partial')) + event('[DONE]'),
    event(delta('partial')) + event(delta(reason='stop')),
    event(delta(reasoning_content='reasoning')) + event(delta(reason='stop')) + event('[DONE]'),
    event(delta('partial')) + b'data: {broken}\n\n',
    event(delta('partial')) + event({'error': {'message': 'PRIVATE_PROVIDER_DETAIL'}}),
    event(delta('partial')) + b'event: error\ndata: PRIVATE_PROVIDER_DETAIL\n\n',
    event(delta('partial')) + event({'model': 'changed-model', 'choices': []}),
    event(delta('partial')) + event(delta('after', reason='stop')) + event(delta('too late')) + event('[DONE]'),
])
def test_incomplete_or_invalid_stream_never_returns_partial_or_retries(raw):
    with pytest.raises(adapters.DeliveryError) as error:
        invoke(Response([raw]))
    assert not error.value.retryable
    assert 'PRIVATE_PROVIDER_DETAIL' not in str(error.value)


@pytest.mark.parametrize('reason', ['length', 'content_filter', 'tool_calls'])
def test_non_text_or_truncated_finish_is_rejected(reason):
    with pytest.raises(adapters.DeliveryError, match='未完整生成'):
        invoke(Response([event(delta('partial')) + event(delta(reason=reason)) + event('[DONE]')]))


def test_plain_json_compatible_provider_and_json_truncation():
    reply = {'model': 'legacy', 'choices': [{'message': {'content': 'ordinary JSON'}, 'finish_reason': 'stop'}]}
    raw = json.dumps(reply).encode()
    assert invoke(Response([raw[:7], raw[7:]], 'application/json; charset=utf-8')) == reply
    with patch.object(adapters, 'request_json', return_value={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]}):
        with pytest.raises(adapters.DeliveryError, match='未完整生成'):
            adapters.ai_draft({'title': '', 'body': '', 'summary': ''}, '', {'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'})


def test_size_limits_include_reasoning_and_bound_reader_text(monkeypatch):
    monkeypatch.setattr(adapters, 'MODEL_RESPONSE_LIMIT', 64)
    with pytest.raises(adapters.DeliveryError, match='大小限制'):
        invoke(Response([event(delta(reasoning_content='x' * 100))]))
    monkeypatch.setattr(adapters, 'MODEL_RESPONSE_LIMIT', 3000000)
    monkeypatch.setattr(adapters, 'MODEL_CONTENT_LIMIT', 3)
    with pytest.raises(adapters.DeliveryError, match='大小限制'):
        invoke(Response([event(delta('123')) + event(delta('4'))]))


def test_heartbeat_cannot_extend_total_deadline(monkeypatch):
    monkeypatch.setattr(adapters, 'MODEL_TOTAL_TIMEOUT', 2)
    ticks = iter([0, 0, 1, 1, 2])
    monkeypatch.setattr(adapters.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(adapters.DeliveryError, match='总等待时间'):
        invoke(Response([b': ping\n\n', b': ping\n\n']))


@pytest.mark.parametrize('failure', [TimeoutError(), http.client.IncompleteRead(b'partial'), ConnectionResetError()])
def test_read_failure_is_not_automatically_replayed(failure):
    with pytest.raises(adapters.DeliveryError) as error:
        invoke(Response([event(delta('partial')), failure]))
    assert not error.value.retryable
    assert '模型服务' in str(error.value)


def test_520_is_model_specific_and_delivery_uncertainty_unchanged():
    with patch('studio.adapters.urllib.request.build_opener') as build:
        build.return_value.open.side_effect = urllib.error.HTTPError('https://synthetic.invalid', 520, 'unknown', {}, None)
        with pytest.raises(adapters.DeliveryError) as model_error:
            adapters.request_json('https://synthetic.invalid', model=True)
        assert not model_error.value.retryable
        assert '模型服务返回 HTTP 520' in str(model_error.value)
        with pytest.raises(adapters.DeliveryError) as delivery_error:
            adapters.request_json('https://synthetic.invalid', delivery=True)
        assert delivery_error.value.uncertain
        assert not delivery_error.value.retryable


def test_service_does_not_repeat_interrupted_generation(tmp_path):
    app = Studio(tmp_path / 'isolated')
    try:
        app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'})
        with patch('studio.adapters.urllib.request.build_opener') as build:
            build.return_value.open.return_value = Response([event(delta('partial')), ConnectionResetError()])
            with pytest.raises(adapters.DeliveryError):
                app.generate_draft({'title': '合成', 'body': '', 'summary': ''}, '合成测试')
            assert build.return_value.open.call_count == 1
            assert len(app.db.rows('SELECT * FROM model_calls')) == 1
    finally:
        app.close()


@pytest.mark.parametrize('recovers', [True, False])
def test_pre_response_temporary_network_failure_keeps_one_retry_budget(tmp_path, recovers):
    app = Studio(tmp_path / 'isolated')
    try:
        app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'})
        failures = [urllib.error.URLError(ConnectionRefusedError())]
        failures += [Response([stream('恢复成功')])] if recovers else [urllib.error.URLError(TimeoutError())]
        with patch('studio.adapters.urllib.request.build_opener') as build:
            build.return_value.open.side_effect = failures
            if recovers:
                assert app.generate_draft({'title': '合成', 'body': '', 'summary': ''}, '合成测试') == '恢复成功'
            else:
                with pytest.raises(adapters.DeliveryError, match='暂时无法连接'):
                    app.generate_draft({'title': '合成', 'body': '', 'summary': ''}, '合成测试')
            assert build.return_value.open.call_count == 2
            calls = app.db.rows('SELECT * FROM model_calls ORDER BY rowid')
            assert len(calls) == 2
            assert calls[0]['status'] == 'failed'
            assert calls[1]['status'] == ('completed' if recovers else 'failed')
    finally:
        app.close()


def test_real_http_stream_survives_multiple_idle_periods_and_requests_stream(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            for packet in [b': ping\n\n'] * 5 + [stream('合成正文')]:
                self.wfile.write(packet)
                self.wfile.flush()
                time.sleep(.025)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), adapters.NoRedirect())
    try:
        with patch('studio.adapters.urllib.request.build_opener', return_value=opener):
            result = adapters.request_json(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', {'stream': True}, timeout=.1, model=True)
        assert result['choices'][0]['message']['content'] == '合成正文'
        assert received == [{'stream': True}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_ai_draft_enables_stream_without_exposing_transport_to_callers():
    with patch.object(adapters, 'request_json', return_value={'choices': [{'message': {'content': '完成'}}]}) as request:
        assert adapters.ai_draft({'title': '合成', 'body': '', 'summary': ''}, '', {'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'}) == '完成'
    assert request.call_args.args[1]['stream'] is True
    assert request.call_args.kwargs['model'] is True
