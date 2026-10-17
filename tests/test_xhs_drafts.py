import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio.service import Studio, Problem
from studio.adapters import DeliveryError
from studio.xhs_drafts import validate_note
from desktop_xhs import DesktopXhsBrowser


class XhsDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name))
        self.addCleanup(self.app.close)
        self.src = '/media/' + 'a' * 32 + '.png'
        (Path(self.tmp.name) / 'media').mkdir(exist_ok=True)
        (Path(self.tmp.name) / self.src.lstrip('/')).write_bytes(b'fixture')
        self.post = self.app.save_post({'title': '文章标题', 'body': '正文', 'summary':'摘要', 'stage': 'ready',
            'xhs': {'title': '智能体记忆怎么分工', 'caption': '图文内容', 'photos': [self.src]}})
        self.browser = Mock()
        self.browser.save_draft.return_value = {'draft_id':'test-draft-0001','account':{'id':'user-1','name':'测试账号'}, 'verified':True}

    def queue(self):
        return self.app.enqueue(self.post['id'], {'version':1, 'channels':['xiaohongshu'], 'ack_warnings':True,'publish_public':True})['ids'][0]

    def test_new_task_is_draft_only_and_verified_receipt_is_recorded(self):
        self.app.xhs_drafts.browser = self.browser
        job_id = self.queue()
        self.assertFalse(self.app.job_detail(job_id)['snapshot']['delivery_config']['publish'])
        self.app.tick()
        result = self.app.job_detail(job_id)
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(result['external_id'], 'test-draft-0001')
        self.assertEqual(result['url'], '')
        self.assertEqual(result['snapshot']['xhs_receipt']['account']['id'], 'user-1')
        channel = next(c for c in self.app.channels() if c['id']=='xiaohongshu')
        self.assertTrue(channel['auto_send'])
        self.assertFalse(channel['publish'])
        self.app.xhs_drafts.account = {'id':'user-2','name':'另一个账号'}
        channel = next(c for c in self.app.channels() if c['id']=='xiaohongshu')
        self.assertFalse(channel['auto_send'])

    def test_old_manual_task_requires_explicit_upgrade_and_preserves_original(self):
        job_id = self.queue()
        self.app.xhs_drafts.browser = self.browser
        self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'], 'manual')
        self.browser.save_draft.assert_not_called()
        self.app.xhs_drafts.queue_existing(job_id, {'title':'另一个短标题'})
        with self.assertRaises(Problem): self.app.xhs_drafts.queue_existing(job_id, {})
        self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'], 'draft')
        self.assertEqual(self.app.post(self.post['id'])['xhs']['title'], '智能体记忆怎么分工')
        self.assertEqual(len(self.app.jobs()), 1)
        self.assertEqual(self.browser.save_draft.call_args.args[0]['title'], '另一个短标题')

    def test_missing_desktop_rejects_upgrade_without_changing_task(self):
        job_id = self.queue(); self.app.tick()
        with self.assertRaises(DeliveryError): self.app.xhs_drafts.queue_existing(job_id, {})
        self.assertEqual(self.app.job_detail(job_id)['status'],'manual')

    def legacy_long_title(self):
        job_id = self.queue()
        self.app.tick()
        snapshot = self.app.job_detail(job_id)['snapshot']
        snapshot['xhs']['title'] = '07 | 从知识库到长期记忆：智能体记忆体系怎么分工'
        with self.app.db.connect() as db:
            db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (json.dumps(snapshot), job_id))
        self.app.xhs_drafts.browser = self.browser
        return job_id, snapshot

    def test_legacy_title_is_adapted_before_upload_and_original_preserved(self):
        job_id, original = self.legacy_long_title()
        self.app.xhs_drafts.queue_existing(job_id, {})
        self.browser.save_draft.assert_not_called()
        with patch.object(self.app, 'generate_draft', return_value='{"title":"智能体记忆如何分工"}') as model:
            self.app.tick()
        result = self.app.job_detail(job_id)
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['snapshot']['xhs'], original['xhs'])
        delivery = self.browser.save_draft.call_args.args[0]
        self.assertEqual(delivery['title'], '智能体记忆如何分工')
        self.assertEqual(delivery['photos'], original['xhs']['photos'])
        self.assertEqual(delivery['caption'], original['xhs']['caption'])
        self.assertEqual(result['snapshot']['xhs_delivery']['title'], delivery['title'])

    def test_failed_title_repair_never_uploads(self):
        job_id, original = self.legacy_long_title()
        self.app.xhs_drafts.queue_existing(job_id, {})
        with patch.object(self.app, 'generate_draft', return_value=json.dumps({'title':'长'*21})):
            self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'], 'failed')
        self.browser.save_draft.assert_not_called()
        self.assertEqual(self.app.job_detail(job_id)['snapshot']['xhs'], original['xhs'])

    def test_retry_reuses_adapted_title(self):
        job_id, _ = self.legacy_long_title()
        self.app.xhs_drafts.queue_existing(job_id, {})
        self.browser.save_draft.side_effect = DeliveryError('尚未上传，窗口不可用')
        with patch.object(self.app, 'generate_draft', return_value='{"title":"智能体记忆如何分工"}') as model:
            self.app.tick()
            self.app.job_action(job_id, 'retry', {})
            self.browser.save_draft.side_effect = None
            self.app.tick()
            self.assertEqual(model.call_count, 1)
        self.assertEqual(self.app.job_detail(job_id)['status'], 'draft')

    def test_uncertain_upload_cannot_retry_or_promote_capability(self):
        self.app.xhs_drafts.browser = self.browser
        self.browser.save_draft.side_effect = DeliveryError('uploaded then disconnected', uncertain=True)
        job_id = self.queue(); self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'],'uncertain')
        with self.assertRaises(Problem): self.app.job_action(job_id,'retry',{})
        self.app.tick()
        self.assertEqual(self.browser.save_draft.call_count,1)
        self.assertFalse(next(c for c in self.app.channels() if c['id']=='xiaohongshu')['auto_send'])

    def test_invalid_receipt_stays_uncertain(self):
        self.app.xhs_drafts.browser = self.browser
        self.browser.save_draft.return_value = {'draft_id':'test-draft-0001','verified':False}
        job_id=self.queue();self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'],'uncertain')

    def test_validation_does_not_truncate_or_allow_arbitrary_files(self):
        for changes in ({'title':'长'*21}, {'caption':'长'*1001}, {'photos':['/media/../../private.json']}, {'photos':[self.src,self.src]}, {'photos': ['/media/'+'b'*32+'.png']}):
            with self.subTest(changes=changes), self.assertRaises(DeliveryError):
                validate_note({**self.post,'xhs':{**self.post['xhs'],**changes}},self.tmp.name)
        self.assertEqual(validate_note(self.post,self.tmp.name)[0]['title'],self.post['xhs']['title'])

    def test_recovery_never_resends_in_progress_xhs(self):
        self.app.xhs_drafts.browser=self.browser
        job_id=self.queue()
        with self.app.db.connect() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job_id,))
        self.app.recover();self.app.tick()
        self.assertEqual(self.app.job_detail(job_id)['status'],'uncertain')
        self.browser.save_draft.assert_not_called()


class BrowserStateTests(unittest.TestCase):
    def browser(self):
        b=DesktopXhsBrowser()
        b.login=Mock(return_value={'id':'u1','name':'one'})
        b._navigate=Mock()
        b._draft_box=Mock(side_effect=[[],[{'id':'new-draft-id','title':'title'}]])
        b._call=Mock(return_value=True)
        b._cdp=Mock(side_effect=[{'root':{'nodeId':1}},{'nodeId':2},{},{},{}])
        b._wait=Mock(return_value=True)
        b.inspect_save_state=Mock(return_value={})
        b.window=Mock()
        return b

    def test_save_reads_back_unique_new_draft_and_never_public_publish(self):
        b=self.browser()
        result=b.save_draft({'title':'title','caption':'text'},['1.png','2.png'],'u1',lambda _:None)
        self.assertTrue(result['verified'])
        self.assertEqual(result['draft_id'],'new-draft-id')
        actions=[c.args[0] for c in b._call.call_args_list]
        self.assertEqual(actions,['close-box','focus-title','focus-caption','save','edit-draft'])
        self.assertEqual([call.args[1]['text'] for call in b._cdp.call_args_list if call.args[0]=='Input.insertText'],['title','text'])
        self.assertEqual(b._cdp.call_args_list[2].args[1]['files'],['1.png','2.png'])

    def test_autosave_means_upload_failure_is_uncertain(self):
        b=self.browser()
        b._cdp.side_effect=[{'root':{'nodeId':1}},{'nodeId':2},RuntimeError('disconnect')]
        with self.assertRaises(DeliveryError) as e:b.save_draft({'title':'title','caption':'text'},['1.png'],None,lambda _:None)
        self.assertTrue(e.exception.uncertain)
        self.assertNotIn('save',[c.args[0] for c in b._call.call_args_list])

    def test_wrong_account_sends_nothing(self):
        b=self.browser()
        with self.assertRaises(DeliveryError) as e:b.save_draft({'title':'title','caption':'text'},['1.png'],'u2',lambda _:None)
        self.assertFalse(e.exception.uncertain)
        b._cdp.assert_not_called()

    def test_duplicate_title_is_not_overwritten_or_resent(self):
        b=self.browser();b._draft_box.side_effect=None;b._draft_box.return_value=[{'id':'existing','title':'title'}]
        with self.assertRaises(DeliveryError) as e:b.save_draft({'title':'title','caption':'text'},['1.png'],None,lambda _:None)
        self.assertTrue(e.exception.uncertain)
        b._cdp.assert_not_called()

    def test_draft_capacity_does_not_evict_older_drafts(self):
        b=self.browser();b._draft_box.side_effect=None;b._draft_box.return_value=[{'id':str(i),'title':str(i)} for i in range(99)]
        with self.assertRaises(DeliveryError) as e:b.save_draft({'title':'title','caption':'text'},['1.png'],None,lambda _:None)
        self.assertFalse(e.exception.uncertain)
        b._cdp.assert_not_called()


class DraftListTests(unittest.TestCase):
    @staticmethod
    def state(rows=(), *, loading=False, empty=False, untyped=0):
        return {'origin': 'https://creator.xiaohongshu.com', 'path': '/publish/publish',
                'drawer': True, 'imageTab': True, 'loading': loading, 'empty': empty,
                'rowCount': len(rows), 'untypedCount': untyped,
                'drafts': [{'id': item[0], 'title': item[1]} for item in rows]}

    def read(self, states, *, before=None, title='target', phase='before'):
        b = DesktopXhsBrowser()
        last = states[-1]
        values = iter(states)
        b._call = Mock(side_effect=lambda action: next(values, last))
        clock = [0.0]
        b.stopped = Mock()
        b.stopped.is_set.return_value = False
        b.stopped.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        self.b, self.clock = b, clock
        with patch('desktop_xhs.time.monotonic', side_effect=lambda: clock[0]):
            return b._read_draft_list(phase=phase, before=before, title=title)

    def test_empty_baseline_requires_explicit_empty_without_loading(self):
        result = self.read([self.state(loading=True, empty=True), self.state(),
                            self.state(empty=True), self.state(empty=True)])
        self.assertEqual(result, [])
        self.assertEqual(self.b._call.call_count, 4)
        diagnostic = self.b.diagnostics['before']
        self.assertTrue(diagnostic['loading_seen'])
        self.assertTrue(diagnostic['empty'])
        self.assertTrue(diagnostic['ready'])

    def test_active_tab_and_stable_empty_array_do_not_prove_loaded_baseline(self):
        with self.assertRaisesRegex(DeliveryError, '加载完成'):
            self.read([self.state()])
        self.assertGreater(self.b._call.call_count, 20)
        self.assertLessEqual(self.clock[0], 10)
        self.assertFalse(self.b.diagnostics['before']['ready'])

    def test_loading_wins_over_existing_rows_and_late_baseline_rows_are_kept(self):
        result = self.read([self.state([('old', 'other')], loading=True),
                            self.state([('old', 'other'), ('later', 'later title')]),
                            self.state([('old', 'other'), ('later', 'later title')])])
        self.assertEqual([d['id'] for d in result], ['old', 'later'])
        self.assertEqual(self.b.diagnostics['before']['ids'], ['old', 'later'])

    def test_tab_count_prevents_accepting_stale_empty_or_partial_list(self):
        empty = self.state(empty=True)
        empty['tabCount'] = 2
        partial = self.state([('one', 'other')])
        partial['tabCount'] = 2
        complete = self.state([('one', 'other'), ('two', 'second')])
        complete['tabCount'] = 2
        result = self.read([empty, partial, complete, complete])
        self.assertEqual(len(result), 2)
        self.assertEqual(self.b._call.call_count, 4)
        self.assertTrue(self.b.diagnostics['before']['count_matches'])

    def test_missing_duplicate_or_untyped_ids_are_refused(self):
        for state in (self.state([(None, 'target')]), self.state([('', 'target')]),
                      self.state([('same', 'one'), ('same', 'two')]),
                      self.state(untyped=1), self.state([('contains credential?=', 'target')])):
            with self.subTest(state=state), self.assertRaisesRegex(DeliveryError, '唯一标识'):
                self.read([state])
            self.assertEqual(self.b._call.call_count, 1)

    def test_post_save_waits_read_only_for_one_new_id(self):
        result = self.read([self.state(empty=True), self.state([('old', 'target')]),
                            self.state([('old', 'target'), ('new', 'target')], loading=True),
                            self.state([('old', 'target'), ('new', 'target')]),
                            self.state([('old', 'target'), ('new', 'target')])],
                           before={'old'}, phase='after')
        self.assertEqual([d['id'] for d in result], ['old', 'new'])
        self.assertEqual(self.b.diagnostics['after']['new_ids'], ['new'])
        self.assertEqual(self.b.diagnostics['after']['candidate_count'], 1)
        self.assertEqual({call.args[0] for call in self.b._call.call_args_list}, {'draft-state'})

    def test_second_candidate_during_rendering_is_not_accepted(self):
        with self.assertRaisesRegex(DeliveryError, '唯一匹配'):
            self.read([self.state([('new', 'target')]),
                       self.state([('new', 'target'), ('another', 'target')])],
                      before=set(), phase='after')
        self.assertEqual(self.b.diagnostics['after']['candidate_count'], 2)

    def test_post_save_timeout_is_read_only_and_diagnostics_contain_no_content(self):
        state = self.state([('old', 'private other title')])
        state['path'] = '/publish/publish?secret=not-allowed'
        with self.assertRaisesRegex(DeliveryError, '唯一匹配'):
            self.read([state], before={'old'}, title='private requested title', phase='after')
        encoded = json.dumps(self.b.diagnostics)
        for secret in ('private other title', 'private requested title', 'secret=not-allowed'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(self.b.diagnostics['after']['title_matches'], [False])
        self.assertEqual({call.args[0] for call in self.b._call.call_args_list}, {'draft-state'})
        self.assertLessEqual(self.clock[0], 10)

    def test_one_new_id_is_reopened_even_when_drawer_label_is_truncated(self):
        result = self.read([self.state([('old', 'old label'), ('new', 'truncated…')])],
                           before={'old'}, title='complete title', phase='after')
        self.assertEqual(result[-1]['id'], 'new')
        self.assertEqual(self.b.diagnostics['after']['title_matches'], [False, False])
        self.assertEqual(self.b.diagnostics['after']['candidate_count'], 1)

    def test_multiple_new_ids_cannot_be_selected_by_matching_title(self):
        with self.assertRaisesRegex(DeliveryError, '唯一匹配'):
            self.read([self.state([('new', 'target'), ('another', 'other title')])],
                      before=set(), phase='after')

    def test_loading_after_candidate_resets_confirmation(self):
        self.read([self.state([('new', 'target')]), self.state([('new', 'target')], loading=True),
                   self.state([('new', 'target')]), self.state([('new', 'target')])],
                  before=set(), phase='after')
        self.assertEqual(self.b._call.call_count, 4)


class SaveReadbackSafetyTests(unittest.TestCase):
    def browser(self, *, mismatch=False, never_visible=False):
        b = DesktopXhsBrowser()
        b.login = Mock(return_value={'id': 'u1', 'name': 'one'})
        b._navigate = Mock()
        b.window = Mock()
        b._cdp = Mock(side_effect=[{'root': {'nodeId': 1}}, {'nodeId': 2}, {}, {}, {}])
        b._draft_box = Mock(side_effect=lambda **kwargs: [] if kwargs['phase'] == 'before'
                           else b._read_draft_list(**kwargs))
        phase = {'saved': False, 'opened': False, 'samples': 0}
        def call(action, arg=None):
            if action == 'save':
                phase['saved'] = True
            if action == 'edit-draft':
                phase['opened'] = True
            if action == 'state':
                return {'editing': not phase['saved'] or phase['opened'],
                        'title': 'title', 'caption': 'wrong text' if phase['opened'] and mismatch else 'text',
                        'images': 1, 'imagesReady': True}
            if action == 'save-state':
                editing = not phase['saved'] or phase['opened']
                return {'path': '/publish/publish', 'ready': True, 'editing': editing,
                        'editor_visible': int(editing), 'editor_total': 1,
                        'uploader_ready': not editing, 'buttons': [], 'save_hosts': [], 'signals': []}
            if action == 'draft-state':
                phase['samples'] += 1
                visible = not never_visible and phase['samples'] > 2
                return DraftListTests.state([('new', 'truncated display…')] if visible else [], empty=not visible)
            return True
        b._call = Mock(side_effect=call)
        clock = [0.0]
        b.stopped = Mock()
        b.stopped.is_set.return_value = False
        b.stopped.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        self.b = b
        self.time = patch('desktop_xhs.time.monotonic', side_effect=lambda: clock[0])
        self.time.start()
        self.addCleanup(self.time.stop)
        return b

    def test_delayed_candidate_is_reopened_and_all_content_still_verified(self):
        b = self.browser()
        result = b.save_draft({'title': 'title', 'caption': 'text'}, ['1.png'], 'u1', lambda _: None)
        self.assertTrue(result['verified'])
        self.assertEqual(b._call.call_args_list.count(unittest.mock.call('save')), 1)
        self.assertIn(unittest.mock.call('edit-draft', 'new'), b._call.call_args_list)
        self.assertTrue(b.diagnostics['readback_verified'])
        self.assertEqual(b.diagnostics['account_id'], 'u1')
        self.assertEqual(b.diagnostics['after']['title_matches'], [False])
        self.assertEqual(b.diagnostics['content_check'], {'title_match': True, 'title_length':5, 'expected_title_length':5, 'caption_match': True,
                         'image_count': 1, 'expected_image_count': 1, 'images_ready': True})

    def test_candidate_content_mismatch_never_succeeds_or_saves_again(self):
        b = self.browser(mismatch=True)
        with self.assertRaises(DeliveryError) as error:
            b.save_draft({'title': 'title', 'caption': 'text'}, ['1.png'], 'u1', lambda _: None)
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(b._call.call_args_list.count(unittest.mock.call('save')), 1)
        self.assertFalse(b.diagnostics['readback_verified'])
        self.assertFalse(b.diagnostics['content_check']['caption_match'])
        self.assertEqual(b.diagnostics['result'], 'uncertain')

    def test_candidate_timeout_keeps_unknown_and_never_saves_again(self):
        b = self.browser(never_visible=True)
        with self.assertRaises(DeliveryError) as error:
            b.save_draft({'title': 'title', 'caption': 'text'}, ['1.png'], 'u1', lambda _: None)
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(b._call.call_args_list.count(unittest.mock.call('save')), 1)
        self.assertNotIn('edit-draft', [call.args[0] for call in b._call.call_args_list])
        self.assertFalse(b.diagnostics['readback_verified'])
        self.assertEqual(b.diagnostics['after']['candidate_count'], 0)




class IdentityHydrationTests(unittest.TestCase):
    def verify(self, states):
        b = DesktopXhsBrowser()
        b.account = {'id': 'remembered', 'name': 'cached'}
        b._ensure_window = Mock()
        last = states[-1]
        values = iter(states)
        def read(action):
            self.assertEqual(action, 'identity-state')
            value = next(values, last)
            if isinstance(value, Exception):
                raise value
            return value
        b._call = Mock(side_effect=read)
        b._navigate = Mock()
        b._cdp = Mock()
        clock = [0.0]
        b.stopped = Mock()
        b.stopped.is_set.return_value = False
        b.stopped.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        self.b, self.clock = b, clock
        with patch('desktop_xhs.time.monotonic', side_effect=lambda: clock[0]):
            return b.verify_identity()

    @staticmethod
    def state(account=None, *, login=False, ready=True, loading=False):
        return {'account': account, 'loginPage': login, 'ready': ready,
                'loading': loading, 'path': '/login' if login else '/new/home'}

    def test_identity_waits_for_spa_and_does_not_navigate_or_mutate(self):
        account = {'id': 'confirmed', 'name': 'current'}
        self.assertEqual(self.verify([self.state(ready=False), self.state(), self.state(account)]), account)
        self.assertEqual(self.b._call.call_count, 3)
        self.b._navigate.assert_not_called()
        self.b._cdp.assert_not_called()
        self.assertEqual(self.b.account, account)

    def test_explicit_login_page_returns_none_only_after_read_window(self):
        self.assertIsNone(self.verify([self.state(login=True)]))
        self.assertEqual(self.clock[0], 10)
        self.assertIsNone(self.b.account)
        self.assertEqual(self.b.diagnostics['identity']['status'], 'signed-out')

    def test_initial_login_surface_does_not_override_later_valid_identity(self):
        account = {'id': 'confirmed', 'name': 'current'}
        self.assertEqual(self.verify([self.state(login=True), self.state(login=True), self.state(account)]), account)

    def test_unready_or_loading_page_does_not_clear_remembered_account(self):
        for state in (self.state(), self.state(login=True, loading=True), self.state(login=True, ready=False)):
            with self.subTest(state=state), self.assertRaisesRegex(DeliveryError, '尚未加载完成'):
                self.verify([state])
            self.assertEqual(self.b.account['id'], 'remembered')
            self.assertEqual(self.clock[0], 10)
            self.assertEqual(self.b.diagnostics['identity']['status'], 'unverified')

    def test_transient_dom_read_failure_can_recover_without_reading_secrets(self):
        account = {'id': 'confirmed', 'name': 'current'}
        self.assertEqual(self.verify([DeliveryError('temporary navigation'), self.state(account)]), account)
        self.assertEqual(self.b.diagnostics['identity']['read_errors'], 1)

    def test_persistent_read_failure_does_not_clear_account_or_expose_error_payload(self):
        with self.assertRaises(DeliveryError):
            self.verify([DeliveryError('do not disclose remote detail')])
        self.assertEqual(self.b.account['id'], 'remembered')
        self.assertNotIn('do not disclose', json.dumps(self.b.diagnostics))




class SavePageObservationTests(unittest.TestCase):
    def browser(self, states):
        b = DesktopXhsBrowser()
        b.window = Mock()
        b._ensure_window = Mock()
        b._navigate = Mock()
        values = iter(states)
        b._call = Mock(side_effect=lambda action: next(values, states[-1]))
        clock = [0.0]
        b.stopped = Mock()
        b.stopped.is_set.return_value = False
        b.stopped.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        timer = patch('desktop_xhs.time.monotonic', side_effect=lambda: clock[0])
        timer.start();self.addCleanup(timer.stop)
        return b

    def test_hidden_stale_editor_allows_only_known_landing_page(self):
        b = self.browser([{'path': '/publish/publish', 'ready': True, 'editing': False,
                           'editor_total': 1, 'editor_visible': 0, 'uploader_ready': True}])
        b._wait_saved_page()
        self.assertEqual(b.diagnostics['save_state']['editor_total'], 1)
        self.assertEqual(b.diagnostics['save_state']['editor_visible'], 0)
        self.assertEqual(b._call.call_args_list, [unittest.mock.call('save-state')])

    def test_remaining_editor_blank_document_or_dialog_never_proves_saved(self):
        for state in ({'ready': True, 'editing': True, 'signals': ['saved']},
                      {'ready': True, 'editing': False},
                      {'ready': True, 'editing': False, 'uploader_ready': True, 'dialog_count': 1}):
            with self.subTest(state=state):
                b = self.browser([state])
                with self.assertRaisesRegex(DeliveryError, '未确认草稿保存结果'):
                    b._wait_saved_page()
                self.assertEqual({c.args[0] for c in b._call.call_args_list}, {'save-state'})
                b._navigate.assert_not_called()

    def test_diagnose_does_not_open_an_absent_window(self):
        b = DesktopXhsBrowser()
        b._ensure_window = Mock()
        b._call = Mock()
        self.assertEqual(b.diagnose(), {'available': False})
        b._ensure_window.assert_not_called()
        b._call.assert_not_called()

    def test_diagnose_returns_only_whitelisted_ui_metadata(self):
        b = self.browser([{'path': '/publish/publish?token=secret', 'ready': True,
                           'editing': True, 'title': 'PRIVATE TITLE', 'caption': 'PRIVATE CONTENT',
                           'buttons': [{'label': '暂存离开', 'disabled': True, 'visible': True},
                                       {'label': 'PRIVATE TITLE', 'visible': True}],
                           'signals': ['saved', 'PRIVATE CONTENT'], 'save_hosts': [{'handler': True, 'token': 'secret'}]}])
        result = b.diagnose()
        encoded = json.dumps(result)
        for secret in ('PRIVATE', 'secret', 'token'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(result['save_state']['buttons'], [{'label': '暂存离开', 'disabled': True, 'visible': True}])
        b._ensure_window.assert_not_called()
        b._navigate.assert_not_called()
        b._call.assert_called_once_with('save-state')


if __name__ == '__main__': unittest.main()
