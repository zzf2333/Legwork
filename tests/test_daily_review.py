"""今日清单首轮审查的行为回归；仅使用虚构数据与离线外部边界。"""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import support
import test_daily as daily_tests
import test_phases as phase_tests

from legwork import config as config_module
from legwork import daily, sending, sources, workflow
from legwork.simulated import SimulatedBrowser
from legwork.store import Store

DAY1 = daily_tests.NOW_DAY1
DAY2 = daily_tests.NOW_DAY2


class DailyReviewTests(daily_tests.DailyCase):
    def test_repeat_preparation_merges_duplicate_reused_rows(self):
        jobs = [daily_tests.custom('job-repeat', source_keyword='词一'),
                daily_tests.custom('job-repeat', source_keyword='词二')]
        first, _ = self.prepare(jobs)
        _, packet = self.packet(first['run_id'])
        self.decide(first['run_id'], packet)
        second, _ = self.prepare(jobs, name='second')
        self.assertEqual(second['reused'], 1)
        detail = self.today('--day', '2026-03-02', '--job', 'job-repeat')
        self.assertIn('词一', detail)
        self.assertIn('词二', detail)
        self.assertIn('待发送', detail)

    def test_incomplete_observation_does_not_hide_later_complete_same_job(self):
        result, _ = self.prepare([daily_tests.custom('job-a', description=''),
                                  daily_tests.custom('job-a', source_keyword='另一个词')])
        self.assertEqual(result['accepted'], 1)
        self.assertIn('常规岗位描述', self.today('--day', '2026-03-02', '--job', 'job-a'))

    def test_complete_jd_wins_over_more_optional_fields(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                job_id = 'job-rich-' + str(int(reverse))
                complete = daily_tests.custom(job_id, salary='', boss_name='', description='完整JD必须保留')
                partial = daily_tests.custom(job_id, description='')
                observed = [partial, complete] if reverse else [complete, partial]
                result, _ = self.prepare(observed, name=job_id)
                self.assertEqual(result['accepted'], 1)
                with Store(self.data / 'state.sqlite3') as store:
                    row = store.job(result['run_id'], job_id)
                    assert row is not None
                    self.assertEqual(row['description'], '完整JD必须保留')
                    self.assertEqual(row['status'], 'awaiting_decisions')
                    self.assertEqual(row['salary'], '', '不拼接其他观察的可选字段伪造快照')

    def test_jd_is_retained_when_both_observations_are_incomplete(self):
        complete_text = daily_tests.custom('job-partial', salary='', boss_name='', address='', description='已取得的JD')
        no_text = daily_tests.custom('job-partial', description='')
        result, _ = self.prepare([complete_text, no_text])
        self.assertEqual(result['accepted'], 0)
        self.assertIn('已取得的JD', self.today('--day', '2026-03-02', '--job', 'job-partial'))

    def test_old_skip_does_not_hide_new_config(self):
        first, _ = self.prepare([daily_tests.custom('job-a')])
        _, packet = self.packet(first['run_id'])
        self.decide(first['run_id'], packet, action='skip')
        support.write_config(self.config_path, live=True, batch_size=3, daily_limit=4)
        self.prepare([daily_tests.custom('job-a')], name='second')
        self.assertIn('状态：待判断', self.today('--day', '2026-03-02', '--job', 'job-a'))

    def test_new_exclusion_invalidates_old_ready(self):
        first, _ = self.prepare([daily_tests.custom('job-a')])
        _, packet = self.packet(first['run_id'])
        self.decide(first['run_id'], packet)
        self.prepare([daily_tests.custom('job-a', title='实习开发', description='新要求')], name='second')
        detail = self.today('--day', '2026-03-02', '--job', 'job-a')
        self.assertIn('状态：采集排除', detail)
        self.assertIn('新要求', detail)
        with Store(self.data / 'state.sqlite3') as store:
            old_job = store.job(first['run_id'], 'job-a')
            assert old_job is not None
            self.assertEqual(old_job['status'], 'stale')
            self.assertIsNotNone(store.decision(first['run_id'], 'job-a'))

    def test_cross_day_attempt_uses_original_snapshot_and_body(self):
        support.write_config(self.config_path, live=True, dedupe_scope=['job'])
        for state, label in [('claimed', '发送中'), ('sent', '已发送'), ('uncertain', '结果待核对')]:
            with self.subTest(state=state):
                job_id = 'job-' + state
                original = '原始JD-' + state
                first, _ = self.prepare([daily_tests.custom(job_id, description=original)])
                _, packet = self.packet(first['run_id'])
                self.decide(first['run_id'], packet)
                with Store(self.data / 'state.sqlite3') as store:
                    decision = store.decision(first['run_id'], job_id)
                    assert decision is not None
                    body = decision['greeting']
                    store.claim_attempt(state, support.BASE_CONFIG['account_id'], first['run_id'], job_id, '2026-03-02', DAY1)
                    if state != 'claimed':
                        store.finish_attempt(state, state, {'mid': 'original-receipt'}, DAY1)
                self.prepare([daily_tests.custom(job_id, description='新JD不能覆盖原发送依据')], now=DAY2, name='next')
                detail = self.today('--day', '2026-03-03', '--job', job_id)
                self.assertIn('状态：' + label, detail)
                self.assertIn(original, detail)
                self.assertIn(body, detail)
                self.assertIn('占用日 2026-03-02', detail)

    def test_source_anomalies_and_all_keyword_origins_reach_daily_view(self):
        config = config_module.load(self.config_path)
        class Browser(daily_tests.FakeLiveBrowser):
            def search(self, keyword, city_code, limit=0):
                result = super().search(keyword, city_code, limit)
                result['rows'].append({'job_id': 'job-missing', 'title': '缺身份岗位', 'href_id': 'job-missing'})
                return result
        browser = Browser()
        with Store(self.data / 'state.sqlite3') as store:
            prepared = workflow.prepare(store, config, sources.EgoSource(browser), datetime.fromisoformat(DAY1))
            payload = daily.build(store, config, '2026-03-02')
            rows = {row['job_id']: row for row in payload['rows']}
            self.assertEqual(set(rows), {'job-ok1', 'job-bad1', 'job-missing'})
            self.assertGreater(prepared['anomalies'], 0)
            self.assertEqual({s['source_keyword'] for s in rows['job-ok1']['sources']}, set(config.keywords))
            self.assertIn('missing_identity', daily.render(payload))
        self.assertEqual(browser.opened, ['job-ok1'])


class PhaseReviewTests(phase_tests.PhaseCase):
    def test_failed_task_cannot_be_finalized_and_sent(self):
        failed = self.run_cli(command=self.flaky_host())
        self.assertEqual(failed.returncode, 4)
        run_id = json.loads(failed.stdout)['run_id']
        result = self.cli('finalize', '--config', str(self.config_path), '--run-id', run_id, '--now', DAY1)
        self.assertEqual(result.returncode, 3, result.stdout)
        run = self.store().run(run_id)
        assert run is not None
        self.assertEqual(run['prepare_state'], 'failed')

    def test_budget_applies_to_manual_packets_and_late_results(self):
        support.write_config(self.config_path, live=True, batch_size=4, daily_limit=5)
        prepared = json.loads(self.cli('prepare', '--config', str(self.config_path), '--fixtures', str(self.fixtures), '--now', DAY1).stdout)
        run_id = prepared['run_id']
        first = json.loads(self.cli('packet', '--config', str(self.config_path), '--run-id', run_id).stdout)
        original = json.loads(Path(first['path']).read_text())
        self.assertEqual(len(original['jobs']), 4)
        with Store(self.data / 'state.sqlite3') as store:
            for n in range(2):
                store.claim_attempt('seed'+str(n), support.BASE_CONFIG['account_id'], 'seed', 'seed'+str(n), '2026-03-02', DAY1)
        smaller = json.loads(self.cli('packet', '--config', str(self.config_path), '--run-id', run_id).stdout)
        self.assertEqual(smaller['batch'], 3)
        self.assertNotEqual(first['packet_id'], smaller['packet_id'])
        result_file = support.write_json(self.tmp / 'late.json', support.model_results(original))
        rejected = self.cli('decide', '--config', str(self.config_path), '--run-id', run_id,
                            '--packet', first['path'], '--results', str(result_file), '--now', DAY1)
        self.assertEqual(rejected.returncode, 3)
        self.assertEqual(self.store().ready_jobs(run_id), [])

    def test_result_arriving_after_quota_change_is_rejected_atomically(self):
        support.write_config(self.config_path, live=True, batch_size=4, daily_limit=5)
        prepared = json.loads(self.cli('prepare', '--config', str(self.config_path), '--fixtures', str(self.fixtures), '--now', DAY1).stdout)
        run_id = prepared['run_id']
        packet = json.loads(self.cli('packet', '--config', str(self.config_path), '--run-id', run_id).stdout)
        document = json.loads(Path(packet['path']).read_text())
        with Store(self.data / 'state.sqlite3') as store:
            for n in range(3):
                store.claim_attempt('seed'+str(n), support.BASE_CONFIG['account_id'], 'seed', 'seed'+str(n), '2026-03-02', DAY1)
        result_file = support.write_json(self.tmp / 'late.json', support.model_results(document))
        rejected = self.cli('decide', '--config', str(self.config_path), '--run-id', run_id,
                            '--packet', packet['path'], '--results', str(result_file), '--now', DAY1)
        self.assertEqual(rejected.returncode, 3)
        self.assertEqual(self.store().ready_jobs(run_id), [])

    def test_duplicate_accepted_result_stays_idempotent_after_quota_changes(self):
        support.write_config(self.config_path, live=True, batch_size=4, daily_limit=5)
        prepared = json.loads(self.cli('prepare', '--config', str(self.config_path), '--fixtures', str(self.fixtures), '--now', DAY1).stdout)
        run_id = prepared['run_id']
        packet = json.loads(self.cli('packet', '--config', str(self.config_path), '--run-id', run_id).stdout)
        document = json.loads(Path(packet['path']).read_text())
        result_file = support.write_json(self.tmp / 'accepted.json', support.model_results(document))
        args = ('decide', '--config', str(self.config_path), '--run-id', run_id,
                '--packet', packet['path'], '--results', str(result_file), '--now', DAY1)
        self.assertEqual(self.cli(*args).returncode, 0)
        with Store(self.data / 'state.sqlite3') as store:
            store.claim_attempt('outside', support.BASE_CONFIG['account_id'], 'outside', 'outside', '2026-03-02', DAY1)
        repeated = self.cli(*args)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertTrue(json.loads(repeated.stdout)['duplicate'])
        self.assertEqual(len(self.store().ready_jobs(run_id)), 4)

    def test_send_entry_uses_only_saved_link(self):
        prepared = json.loads(self.run_cli('--prepare-only').stdout)
        config = config_module.load(self.config_path)
        browser = SimulatedBrowser(self.fixtures)
        original = browser.open_saved_job
        calls = []
        def direct(job):
            calls.append(job['job_url'])
            return original(job)
        browser.open_saved_job = direct
        with patch.object(browser, 'open_job', side_effect=AssertionError('不能搜索回找')):
            outcome, code = sending.send_ready(self.store(), config, self.store().run(prepared['run_id']),
                                               browser, clock=lambda: datetime.fromisoformat(DAY1), data_dir=self.data)
        self.assertEqual(code, 0, outcome)
        self.assertEqual(len(calls), 4)
