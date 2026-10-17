"""Isolated proposal fixtures and paired local benchmarks; no real model calls."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from studio import memories, profiles
from studio.db import uid, now
from studio.publication_progress import register
from studio.service import Studio
from studio.topics import FIELDS

KINDS = ('topic', 'memory', 'performance')


def output(kind):
    if kind == 'topic':
        value = {'candidates': [{k: f'候选{i}的{k}依据' for k in FIELDS} for i in range(3)]}
    elif kind == 'memory':
        value = {'candidates': [{'content': '保留具体操作步骤', 'quote': '保留具体操作步骤', 'keywords': ''}]}
    else:
        value = {'summary': '单篇累计数据不足以推断因果。', 'limitations': ['没有同期对照。'],
                 'observations': [{'content': '记录100次阅读。', 'evidence_ids': ['reads']}],
                 'candidates': []}
    return json.dumps(value, ensure_ascii=False)


def create_case(app, kind):
    profile = profiles.save(app, {'name': '合成核查画像-' + uid()[:8], 'goal': '解释实际操作边界'})
    post = app.save_post({'title': '合成恢复资料', 'body': '保留具体操作步骤',
                          'profile_id': profile['id'], 'stage': 'draft'})
    case = {'kind': kind, 'profile_id': profile['id'], 'post_id': post['id']}
    if kind == 'topic':
        case['id'] = app.topics.create({'profile_id': profile['id'], 'reference_ids': [post['id']],
                                         'idea': '依据本次材料提出选题'})['id']
    elif kind == 'memory':
        case['values'] = {'source_type': 'post', 'source_id': post['id'], 'source_version': post['version']}
    else:
        register(app, post['id'], {'version': post['version'], 'channels': ['wechat'], 'confirmed': True})
        job = next(j for j in app.jobs() if j['post_id'] == post['id'])
        sample = app.performance.add_sample(job['id'], {'request_id': uid(), 'previous_id': None,
            'reads': 100, 'likes': 0, 'observed_at': now(), 'source_note': '合成数据', 'note': ''})
        case['id'] = app.performance.create({'request_id': uid(), 'job_id': job['id'],
            'sample_id': sample['id'], 'profile_id': profile['id']})['id']
    case['post'] = app.post(post['id'])
    return case


def run_case(app, case):
    if case['kind'] == 'memory':
        return memories.extract(app, case['profile_id'], case['values'])
    manager = app.topics if case['kind'] == 'topic' else app.performance
    assert manager.tick()
    return manager.get(case['id'])


def assert_success(app, case, result):
    if case['kind'] == 'memory':
        assert len(result['memories']) == 1
        assert all(m['status'] == 'pending' for m in result['memories'])
    else:
        assert result['status'] == ('completed' if case['kind'] == 'topic' else 'ready')
    assert memories.recall(app, case['profile_id']) == []
    assert app.post(case['post_id']) == case['post']
    assert all(j['attempts'] == 0 for j in app.jobs())


def benchmark(out, pairs):
    apps, samples = {}, []
    prior = os.environ.get('PAPERPLANE_USE_SKILLS')
    try:
        for mode in ('legacy', 'skills'):
            os.environ['PAPERPLANE_USE_SKILLS'] = '1' if mode == 'skills' else '0'
            app = Studio(out / (mode + '-' + uid()[:8]))
            app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'})
            apps[mode] = app
        for index in range(pairs + 2):
            for kind in KINDS:
                pair_samples = []
                for mode in (('legacy', 'skills') if index % 2 == 0 else ('skills', 'legacy')):
                    app = apps[mode]
                    case = create_case(app, kind)
                    requests = []
                    def transport(url, payload, headers=None, **kwargs):
                        requests.append(payload)
                        return {'choices': [{'message': {'content': output(kind)}}]}
                    with patch('studio.adapters.request_json', side_effect=transport):
                        started = time.perf_counter_ns()
                        result = run_case(app, case)
                        duration = (time.perf_counter_ns() - started) / 1e6
                    assert_success(app, case, result)
                    assert len(requests) == 1
                    row = {'pair': index - 2, 'kind': kind, 'mode': mode, 'duration_ms': duration,
                           'input_chars': sum(len(m['content']) for m in requests[0]['messages']), 'calls': len(requests)}
                    pair_samples.append(row)
                    if index >= 2:
                        samples.append(row)
                assert pair_samples[0]['input_chars'] == pair_samples[1]['input_chars']
        summary = {}
        for kind in KINDS:
            modes = {}
            for mode in apps:
                values = sorted(s['duration_ms'] for s in samples if s['kind'] == kind and s['mode'] == mode)
                modes[mode] = {'median_ms': statistics.median(values), 'p95_ms': values[int(.95 * (len(values) - 1))],
                               'min_ms': min(values), 'max_ms': max(values)}
            modes['median_delta_ms'] = modes['skills']['median_ms'] - modes['legacy']['median_ms']
            summary[kind] = modes
        record = {'scope': 'Paired local business processing with synthetic transport; excludes remote latency and real token billing',
                  'pairs_per_kind': pairs, 'warmup_pairs': 2, 'samples': samples, 'summary': summary,
                  'single_call_and_equal_input_chars': True, 'business_guards_passed': True}
        (out / 'benchmark.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        for app in apps.values():
            app.close()
        if prior is None:
            os.environ.pop('PAPERPLANE_USE_SKILLS', None)
        else:
            os.environ['PAPERPLANE_USE_SKILLS'] = prior


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=30)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.relative_to((SOURCE.parent / 'work/test-results').resolve())
    if args.pairs < 10 or args.pairs > 100:
        parser.error('pairs must be 10..100')
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / 'benchmark.json').exists():
        parser.error('Use a new evidence directory; prior results must not be overwritten')
    benchmark(args.out, args.pairs)
