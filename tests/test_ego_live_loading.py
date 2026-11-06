"""由Ego实页证据提取的加载契约；执行实际生成的页面脚本，不操作真实账号。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from legwork import config as config_module
from legwork.ego import BrowserError, EgoBrowser


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = config_module.load(support.write_config(self.root / 'config.json', live=True))
        self.state_file = self.root / 'page.json'
        self.state = {'self': config.platform_user_id, 'city': config.city_code,
                      'keywords': {'first': ['job-a1'], 'second': ['job-a2']},
                      'jobs': support.JOBS[:2], 'query': 'first', 'contacts': [],
                      'messages': [], 'selected': None, 'draft': '', 'opened': 'job-a1', 'events': []}
        self.save()
        execute = subprocess.run
        harness = Path(__file__).parent / 'js/ego_transport.mjs'
        def transport(argv, **kwargs):
            if argv == ['ego-browser', 'nodejs']:
                return execute(['node', str(harness), str(self.state_file)], **kwargs)
            return execute(argv, **kwargs)
        self.enterContext(patch('legwork.ego.subprocess.run', side_effect=transport))
        self.browser = EgoBrowser(config)
        self.city = config.city_code

    def save(self):
        support.write_json(self.state_file, self.state)

    def test_loading_empty_is_not_final_empty(self):
        result = self.browser.search('second', self.city)
        self.assertEqual([r['job_id'] for r in result['rows']], ['job-a2'])

    def test_query_changes_before_new_request_starts(self):
        self.state['delayedStart'] = True
        self.save()
        result = self.browser.search('second', self.city)
        self.assertEqual([r['job_id'] for r in result['rows']], ['job-a2'])

    def test_search_when_resource_timing_buffer_is_full(self):
        self.state['resourceBufferFull'] = True
        self.save()
        result = self.browser.search('second', self.city)
        self.assertEqual([r['job_id'] for r in result['rows']], ['job-a2'])

    def test_completed_empty_search_is_valid(self):
        self.assertEqual(self.browser.search('no-results', self.city)['rows'], [])
        self.assertEqual(json.loads(self.state_file.read_text())['loadingTicks'], 0)

    def test_initial_page_without_url_city_checks_page_city(self):
        self.state['omitCity'] = True
        self.save()
        self.assertEqual(self.browser.search('second', self.city)['rows'][0]['job_id'], 'job-a2')

    def test_initial_page_does_not_accept_wrong_city(self):
        self.state['omitCity'] = True
        self.save()
        with self.assertRaises(BrowserError):
            self.browser.search('second', 'wrong-city')

    def test_history_loads_until_explicit_end(self):
        self.state['historyBatches'] = [[{'encryptJobId': 'prior-1'}], [{'encryptJobId': 'prior-2'}]]
        self.save()
        history = self.browser.load_history()
        self.assertTrue(history['history_complete'])
        self.assertEqual(len(history['contacts']), 2)
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state['events'], ['history-scroll', 'history-scroll'])

    def test_history_without_progress_stops(self):
        self.state['historyBatches'] = [[{'encryptJobId': 'prior-1'}]]
        self.state['historyStalled'] = True
        self.save()
        with self.assertRaises(BrowserError):
            self.browser.load_history()
        self.assertEqual(json.loads(self.state_file.read_text())['events'], ['history-scroll'])
