"""第2轮审查回归；浏览器桥接测试执行真实生成脚本，仅替换页面传输。"""
import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import support
from test_contract_fixes import NOW_DAY1, Harness

from legwork import config as config_module
from legwork import ego, runner, sending, sources, workflow
from legwork.errors import SendFailure
from legwork.simulated import SimulatedBrowser
from legwork.store import Store


class RoundTwoTests(Harness):
    def test_success_then_refusal_never_retries(self):
        config_path = self.config(live=True, batch_size=4)
        prepared = self.prepare(config_path, '--fixtures', str(self.fixtures))
        packet, document = self.packet(config_path, prepared['run_id'])
        self.assertEqual(self.decide(config_path, prepared['run_id'], packet, support.model_results(document)).returncode, 0)
        browser = SimulatedBrowser(self.fixtures)
        original = browser.chat_state
        reads = []
        def state():
            result = original()
            if browser._job_id == 'job-a2':
                reads.append('job-a2')
                if len(reads) == 1:
                    result['platform_user_id'] = 'wrong-account'
            return result
        browser.chat_state = state
        with Store(self.data / 'state.sqlite3') as store:
            outcome, code = runner.run_once(store, config_module.load(config_path), sources.FixtureSource(self.fixtures),
                data_dir=self.data, clock=lambda: datetime.fromisoformat(NOW_DAY1), browser=browser,
                resume_run=prepared['run_id'], host_command='must-not-run')
            self.assertEqual(code, 3, outcome)
            self.assertEqual(outcome['sent'], 1)
            self.assertEqual(reads, ['job-a2'])
            self.assertIsNone(store.attempt(support.BASE_CONFIG['account_id'], 'job-a2'))

    def test_exact_batch_quota_does_not_call_next_model(self):
        config = self.config(live=True, daily_limit=3, batch_size=3)
        proc = self.cli('run', '--config', str(config), '--fixtures', str(self.fixtures),
                        '--host-command', str(support.write_fake_host(self.tmp)), '--now', NOW_DAY1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['batches'], 1, payload)
        self.assertEqual(payload['sent'], 3)

    def test_receipt_without_conversation_id_is_not_evidence(self):
        config = config_module.load(self.config())
        identity = {'uid': 'target', 'conversation_id': 'conversation'}
        chat = {'messages': [{'sender': config.platform_user_id, 'recipient': 'target',
                             'mid': 'mid-1', 'text': 'hello', 'delivered': True, 'conversation_id': ''}]}
        with self.assertRaises(SendFailure):
            sending._unique_custom_receipt(config, chat, identity, 'hello')

    def test_real_bridge_across_distinct_pages_and_keywords(self):
        config = config_module.load(self.config(live=True))
        state_file = self.tmp / 'page-state.json'
        support.write_json(state_file, {'self': config.platform_user_id, 'city': config.city_code,
            'keywords': dict(zip(config.keywords, [['job-a1'], ['job-a2']], strict=True)), 'jobs': support.JOBS[:2],
            'query': config.keywords[0], 'contacts': [], 'messages': [], 'selected': None,
            'draft': '', 'opened': 'job-a1', 'events': []})
        actual_run = subprocess.run
        harness = Path(__file__).parent / 'js/ego_transport.mjs'
        def transport(argv, **kwargs):
            if argv == ['ego-browser', 'nodejs']:
                return actual_run(['node', str(harness), str(state_file)], **kwargs)
            return actual_run(argv, **kwargs)
        with patch('legwork.ego.subprocess.run', side_effect=transport):
            browser = ego.EgoBrowser(config)
            with Store(self.data / 'state.sqlite3') as store:
                prepared = workflow.prepare(store, config, sources.EgoSource(browser), datetime.fromisoformat(NOW_DAY1))
                jobs = store.jobs(prepared['run_id'])
                self.assertEqual([j['job_id'] for j in jobs], ['job-a1', 'job-a2'])
                self.assertEqual([j['source_keyword'] for j in jobs], list(config.keywords))
                # 采集停在第二个词；发送第一岗必须恢复其来源列表。
                opened = sending._preflight(config, jobs[0], browser)
                evidence = sending._deliver(config, jobs[0], browser, opened, '测试定制招呼')
                self.assertEqual(evidence['custom_message']['mid'], 'custom-mid')
        state = json.loads(state_file.read_text())
        self.assertEqual(state['events'].count('contact:job-a1'), 1)
        self.assertEqual(state['events'].count('send:job-a1'), 1)
