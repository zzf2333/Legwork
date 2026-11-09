"""保存链接直达：真实适配脚本＋独立页面传输替身，不发真实消息。"""
import json
import unittest
from pathlib import Path

import support
import test_ego_live_loading as loading

from legwork.ego import BrowserError, EgoBrowser


class DirectLinkTests(unittest.TestCase):
    state: dict
    state_file: Path
    browser: EgoBrowser
    city: str
    setUp = loading.LoadingTests.setUp
    save = loading.LoadingTests.save

    def target(self):
        return support.JOBS[1] | {'job_url': 'https://www.zhipin.com/job_detail/job-a2.html'}

    @staticmethod
    def contact(job_id, company, boss_id):
        """会话列表的 vue 源数据形状：读片段按 encrypt* 字段取身份。"""
        return {
            'encryptJobId': job_id,
            'brandName': company,
            'encryptBossId': boss_id,
            'uid': 'uid-' + boss_id,
            'uniqueId': 'conv-' + boss_id,
        }

    def test_saved_link_does_not_need_search_context(self):
        target = self.target()
        self.state['keywords'] = {}
        self.save()
        self.browser.open_saved_job(target)
        card = self.browser.card_state()
        self.assertEqual((card['job_id'], card['boss_id'], card['company']),
                         (target['job_id'], target['boss_id'], target['company']))
        self.assertEqual(card['description'].strip(), target['description'])
        self.assertTrue(card['can_add_friend'])
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state['events'], ['navigate:job-a2'])

    def test_bad_url_is_rejected_before_browser_action(self):
        for url in ['https://example.com/job_detail/job-a2.html',
                    'https://www.zhipin.com/job_detail/job-a1.html', '',
                    'https://www.zhipin.com/job_detail/job-a2.html?redirect=other']:
            with self.subTest(url=url), self.assertRaises(BrowserError):
                self.browser.open_saved_job(self.target() | {'job_url': url})
        self.assertEqual(json.loads(self.state_file.read_text())['events'], [])

    def test_detail_identity_mismatch_does_not_contact(self):
        self.state['redirectJob'] = 'job-a1'
        self.save()
        with self.assertRaises(BrowserError):
            self.browser.open_saved_job(self.target())
        state = json.loads(self.state_file.read_text())
        self.assertFalse(any(e.startswith('contact:') for e in state['events']))

    def test_direct_detail_checks_account(self):
        self.state['detailSelf'] = 'wrong-account'
        self.save()
        with self.assertRaises(BrowserError):
            self.browser.open_saved_job(self.target())

    def test_collect_again_after_direct_detail(self):
        self.browser.open_saved_job(self.target())
        result = self.browser.search('first', self.city)
        self.assertEqual(result['rows'][0]['job_id'], 'job-a1')
        self.assertEqual(result['rows'][0]['job_url'], 'https://www.zhipin.com/job_detail/job-a1.html')

    def test_direct_contact_guard_keeps_expected_target(self):
        target = self.target()
        self.browser.open_saved_job(target)
        self.browser.click_contact(target)
        selected = self.browser.chat_state()['selected']
        self.assertEqual(selected['job_id'], target['job_id'])
        self.assertEqual(json.loads(self.state_file.read_text())['events'].count('contact:job-a2'), 1)

    def test_contact_click_finds_target_beyond_rendered_window(self):
        target = self.target()
        self.state['contacts'] = [
            {'job_id': f'job-old-{i}', 'company': f'示例旧公司{i}', 'boss_id': f'boss-old-{i}'}
            for i in range(40)
        ]
        self.save()
        self.browser.open_saved_job(target)
        self.browser.click_contact(target)
        self.assertEqual(self.browser.chat_state()['selected']['job_id'], target['job_id'])

    def test_open_contact_selects_existing_conversation_without_clicking(self):
        target = self.target()
        self.state['contacts'] = [
            self.contact(f'job-old-{i}', f'示例旧公司{i}', f'boss-old-{i}') for i in range(40)
        ] + [self.contact(target['job_id'], target['company'], target['boss_id'])]
        self.save()
        self.browser.open_saved_job(target)
        self.browser.open_contact(target)
        self.assertEqual(self.browser.chat_state()['selected']['job_id'], target['job_id'])
        events = json.loads(self.state_file.read_text())['events']
        self.assertFalse(
            any(e.startswith('contact:') for e in events), '补发不能重新点「立即沟通」'
        )

    def test_open_contact_refuses_without_existing_conversation(self):
        target = self.target()
        self.browser.open_saved_job(target)
        with self.assertRaises(BrowserError):
            self.browser.open_contact(target)
        events = json.loads(self.state_file.read_text())['events']
        self.assertFalse(any(e.startswith('contact:') for e in events))
