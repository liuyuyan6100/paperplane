"""All provenance tests are isolated; responses are synthetic and never leave the process."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from studio import adapters, image_provider, model_provenance as provenance
from studio.db import Database


class ModelProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = SimpleNamespace(db=Database(self.temp.name))
        with self.app.db.connect() as db:
            db.executescript(provenance.SCHEMA)
        self.route = {'task': 'body', 'model': 'chosen-model', 'mode': 'api',
                      'owner_kind': 'workflows', 'owner_id': 'test-workflow',
                      'base_url': 'https://private.example/tenant-path', 'api_key': 'private-key'}

    def read(self, **kwargs):
        return provenance.public_for(self.app, 'workflows', 'test-workflow', task='body', **kwargs)

    def test_response_identity_and_sanitized_history(self):
        def invoke():
            current = self.read()
            self.assertEqual(current['status'], 'running')
            provenance.report_response({'model': 'reported-alias', 'secret': 'returned-secret'})
            return '正文'
        self.assertEqual(provenance.tracked_call(self.app, self.route, invoke), '正文')
        result = self.read()
        self.assertEqual(result['requested_model'], 'chosen-model')
        self.assertEqual(result['reported_model'], 'reported-alias')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['calls'][0]['attempt'], 1)
        self.assertTrue(result['calls'][0]['started_at'])
        self.assertTrue(result['calls'][0]['finished_at'])
        stored = json.dumps(self.app.db.rows('SELECT * FROM model_calls'))
        for private in ('private.example', 'tenant-path', 'private-key', 'returned-secret'):
            self.assertNotIn(private, stored)

    def test_each_retry_is_retained_without_exception_message(self):
        def fail():
            raise adapters.DeliveryError('private-error-url-and-key', retryable=True)
        with self.assertRaises(adapters.DeliveryError):
            provenance.tracked_call(self.app, self.route, fail)
        provenance.tracked_call(self.app, self.route, lambda: 'retry response')
        result = self.read()
        self.assertEqual([c['status'] for c in result['calls']], ['failed', 'completed'])
        self.assertEqual([c['attempt'] for c in result['calls']], [1, 2])
        self.assertEqual(result['reported_model'], '')
        self.assertNotIn('private-error', json.dumps(result))

    def test_old_route_uses_only_saved_model(self):
        result = self.read(route={'model': 'old-snapshot-model', 'base_url': 'https://old-private.example'})
        self.assertEqual(result['source'], 'legacy')
        self.assertEqual(result['requested_model'], 'old-snapshot-model')
        self.assertEqual(result['reported_model'], '')
        self.assertNotIn('old-private', json.dumps(result))
        self.assertEqual(self.read()['source'], 'unknown')

    def test_image_provider_snapshot_and_no_model_origins(self):
        result = provenance.public_for(self.app, 'illustration_candidates', 'old-image',
            task='cover', route={'IMAGE_MODEL': 'past-image-model', 'IMAGE_BASE_URL': 'https://private.example'})
        self.assertEqual(result['requested_model'], 'past-image-model')
        for origin in ('original', 'uploaded', 'layout', 'publish'):
            result = self.read(origin=origin, route=self.route)
            self.assertEqual(result['source'], 'none')
            self.assertEqual(result['requested_model'], '')
            self.assertEqual(result['calls'], [])

    def test_phase_filter_does_not_mix_outline_and_body(self):
        provenance.tracked_call(self.app, {**self.route, 'task': 'outline', 'model': 'outline-model'}, lambda: '')
        provenance.tracked_call(self.app, self.route, lambda: '')
        self.assertEqual(len(self.read()['calls']), 1)
        self.assertEqual(self.read()['calls'][0]['task'], 'body')
        outline = provenance.public_for(self.app, 'workflows', 'test-workflow', task='outline')
        self.assertEqual(outline['requested_model'], 'outline-model')

    def test_response_collector_does_not_leak_across_threads_or_calls(self):
        ready = threading.Barrier(2)
        def run(name):
            def invoke():
                ready.wait(timeout=5)
                provenance.report_response({'model': name})
            provenance.tracked_call(self.app, {**self.route, 'owner_id': name}, invoke)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(run, ['first', 'second']))
        for name in ('first', 'second'):
            result = provenance.public_for(self.app, 'workflows', name, task='body')
            self.assertEqual(result['reported_model'], name)
        provenance.report_response({'model': 'outside-call'})
        provenance.tracked_call(self.app, self.route, lambda: '')
        self.assertEqual(self.read()['reported_model'], '')

    def test_restart_marks_unfinished_calls_interrupted(self):
        provenance.tracked_call(self.app, self.route, lambda: '')
        with self.app.db.connect() as db:
            db.execute("UPDATE model_calls SET status='running',finished_at='' ")
        provenance.interrupt(self.app)
        self.assertEqual(self.read()['status'], 'interrupted')
        self.assertTrue(self.read()['calls'][0]['finished_at'])

    def test_untrusted_response_model_is_not_stringified(self):
        for model in ({'key': 'private'}, ['model'], 'https://secret.example/model', 'x\nprivate', 'token=secret'):
            def invoke():
                provenance.report_response({'model': model})
            provenance.tracked_call(self.app, self.route, invoke)
            self.assertEqual(self.read()['reported_model'], '')

    def test_text_adapter_records_actual_response_model_and_keeps_string_contract(self):
        secrets = {'AI_BASE_URL': 'https://synthetic.example/v1', 'AI_API_KEY': 'private', 'AI_MODEL': 'chosen-model'}
        response = {'model': 'returned-text-model', 'choices': [{'message': {'content': '正文'}}]}
        with patch.object(adapters, 'request_json', return_value=response):
            result = provenance.tracked_call(self.app, self.route,
                lambda: adapters.ai_draft({'title': '测试', 'summary': '', 'body': ''}, '', secrets))
        self.assertEqual(result, '正文')
        self.assertEqual(self.read()['reported_model'], 'returned-text-model')

    def test_image_adapter_records_response_model_and_keeps_bytes_contract(self):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(buffer, format='PNG')
        raw = buffer.getvalue()
        response = {'model': 'returned-image-model', 'data': [{'b64_json': base64.b64encode(raw).decode()}]}
        config = {'IMAGE_BASE_URL': 'https://synthetic.example/v1', 'IMAGE_MODEL': 'chosen-image',
                  'IMAGE_API_KEY': 'private', 'IMAGE_REFERENCES': 'edits'}
        with patch.object(image_provider, '_request', return_value=json.dumps(response).encode()):
            result = provenance.tracked_call(self.app, self.route,
                lambda: image_provider.generate(config, 'synthetic', '1024x1024', []))
        self.assertEqual(result, raw)
        self.assertEqual(self.read()['reported_model'], 'returned-image-model')

    def test_planned_is_explicitly_prospective_and_no_credentials_escape(self):
        class Config(dict):
            lock = threading.RLock()
        config = Config(AI_MODE='api', AI_MODEL='default', AI_BODY_MODEL='next-body',
                        AI_BASE_URL='https://private.example/v1', AI_API_KEY='private-secret',
                        IMAGE_MODEL='image-default', IMAGE_COVER_MODEL='next-cover')
        result = provenance.planned(config, 'body')
        self.assertEqual(result['source'], 'planned')
        self.assertEqual(result['requested_model'], 'next-body')
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(provenance.planned(config, 'cover')['requested_model'], 'next-cover')
        self.assertEqual(provenance.planned(config, 'publish')['source'], 'none')
        self.assertEqual(provenance.planned(config, 'publish')['task'], 'publish')

    def test_program_mode_does_not_claim_unused_api_model(self):
        provenance.tracked_call(self.app, {**self.route, 'mode': 'program'}, lambda: 'program output')
        result = self.read()
        self.assertEqual(result['mode'], 'program')
        self.assertEqual(result['requested_model'], '')


if __name__ == '__main__':
    unittest.main()
