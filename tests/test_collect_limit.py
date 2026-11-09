"""加载更多：滚动到条数上限就停，单关键词加载失败只记采集异常，不中断整轮。"""
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import support
import test_ego_live_loading as loading

import legwork.ego as ego_module
from legwork import config as config_module
from legwork import sources
from legwork.ego import BrowserError, EgoBrowser


class CollectPerKeywordConfigTests(unittest.TestCase):
    def test_missing_field_uses_default(self):
        config = config_module.parse(dict(support.BASE_CONFIG))
        self.assertEqual(config.collect_per_keyword, 40)

    def test_explicit_limit_is_parsed(self):
        config = config_module.parse(dict(support.BASE_CONFIG) | {"collect_per_keyword": 40})
        self.assertEqual(config.collect_per_keyword, 40)

    def test_invalid_limit_is_rejected(self):
        for value in (-1, 101, "40", True, 2.5):
            with self.subTest(value=value), self.assertRaises(config_module.ConfigError):
                config_module.parse(dict(support.BASE_CONFIG) | {"collect_per_keyword": value})


class LoadMoreTests(unittest.TestCase):
    """执行真实生成的页面脚本；列表分页由传输替身模拟（每页 2 条，共 3 页）。"""

    state: dict
    state_file: Path
    root: Path
    browser: EgoBrowser
    city: str
    setUp = loading.LoadingTests.setUp
    save = loading.LoadingTests.save

    def load_more_state(self, **overrides):
        self.state['jobs'] = support.JOBS[:5]
        self.state['keywordBatches'] = {
            'first': [['job-a1', 'job-a2'], ['job-a3', 'job-a4'], ['job-intern']],
        }
        self.state.update(overrides)
        self.save()

    def browser_with_limit(self, limit):
        path = support.write_config(self.root / 'config.json', live=True, collect_per_keyword=limit)
        return EgoBrowser(config_module.load(path))

    def events(self):
        return json.loads(self.state_file.read_text())['events']

    def test_zero_limit_reads_only_first_screen(self):
        self.load_more_state()
        result = self.browser.search('first', self.city)
        self.assertEqual([row['job_id'] for row in result['rows']], ['job-a1', 'job-a2'])
        self.assertNotIn('load_error', result)
        self.assertEqual(self.events(), [])

    def test_scrolls_until_rows_reach_limit(self):
        self.load_more_state()
        result = self.browser.search('first', self.city, 3)
        self.assertEqual(
            [row['job_id'] for row in result['rows']],
            ['job-a1', 'job-a2', 'job-a3', 'job-a4'],
        )
        self.assertNotIn('load_error', result)
        # 加载到第二页已超过上限就停：第三页不再滚动。
        self.assertEqual(self.events(), ['list-scroll'])

    def test_scrolls_multiple_pages_to_reach_limit(self):
        self.load_more_state()
        result = self.browser.search('first', self.city, 5)
        self.assertEqual(
            [row['job_id'] for row in result['rows']],
            ['job-a1', 'job-a2', 'job-a3', 'job-a4', 'job-intern'],
        )
        self.assertNotIn('load_error', result)
        self.assertEqual(self.events(), ['list-scroll', 'list-scroll'])

    def test_exact_limit_stops_after_one_scroll(self):
        self.load_more_state()
        result = self.browser.search('first', self.city, 4)
        self.assertEqual(
            [row['job_id'] for row in result['rows']],
            ['job-a1', 'job-a2', 'job-a3', 'job-a4'],
        )
        self.assertNotIn('load_error', result)
        self.assertEqual(self.events(), ['list-scroll'])

    def test_search_uses_extended_subprocess_timeout(self):
        self.load_more_state()
        browser = self.browser_with_limit(40)
        captured = {}
        current = ego_module.subprocess.run

        def spy(*args, **kwargs):
            captured['timeout'] = kwargs.get('timeout')
            return current(*args, **kwargs)

        with patch('legwork.ego.subprocess.run', side_effect=spy):
            browser.search('first', self.city, 40)
        self.assertEqual(captured['timeout'], browser.timeout + 40 * 11)

    def test_duplicate_card_across_pages_opens_once(self):
        self.load_more_state()
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2'], ['job-a1', 'job-a3']]}
        self.state['opened'] = 'job-a2'
        self.save()
        browser = self.browser_with_limit(3)
        browser.open_job(support.JOBS[0] | {'source_keyword': 'first'})
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state['opened'], 'job-a1')
        self.assertEqual(state['cardClicks'], 1)

    def test_duplicate_card_with_conflicting_identity_is_rejected(self):
        self.load_more_state()
        other = dict(support.JOBS[0]) | {
            'job_id': 'job-other', 'company': '示例其他科技', 'boss_id': 'boss-other'
        }
        self.state['jobs'] = support.JOBS[:5] + [other]
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2'], ['job-other', 'job-a3']]}
        self.state['hrefOverrides'] = {'job-other': '/job_detail/job-a1.html'}
        self.save()
        browser = self.browser_with_limit(3)
        with self.assertRaises(BrowserError):
            browser.open_job(support.JOBS[0] | {'source_keyword': 'first'})
        self.assertEqual(json.loads(self.state_file.read_text()).get('cardClicks', 0), 0)

    def test_duplicate_card_without_data_is_rejected(self):
        self.load_more_state()
        other = dict(support.JOBS[0]) | {
            'job_id': 'job-other', 'company': '示例其他科技', 'boss_id': 'boss-other'
        }
        self.state['jobs'] = support.JOBS[:5] + [other]
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2'], ['job-other', 'job-a3']]}
        self.state['hrefOverrides'] = {'job-other': '/job_detail/job-a1.html'}
        self.state['dataOverrides'] = {'job-a1': None, 'job-other': None}
        self.save()
        browser = self.browser_with_limit(3)
        with self.assertRaises(BrowserError):
            browser.open_job(support.JOBS[0] | {'source_keyword': 'first'})
        self.assertEqual(json.loads(self.state_file.read_text()).get('cardClicks', 0), 0)

    def test_duplicate_card_with_other_job_id_is_rejected(self):
        self.load_more_state()
        other = dict(support.JOBS[0]) | {
            'job_id': 'job-other', 'company': '示例其他科技', 'boss_id': 'boss-other'
        }
        self.state['jobs'] = support.JOBS[:5] + [other]
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2'], ['job-other', 'job-a3']]}
        self.state['hrefOverrides'] = {'job-other': '/job_detail/job-a1.html'}
        self.state['dataOverrides'] = {
            'job-a1': {'encryptJobId': 'job-other'}, 'job-other': {'encryptJobId': 'job-other'}
        }
        self.save()
        browser = self.browser_with_limit(3)
        with self.assertRaises(BrowserError):
            browser.open_job(support.JOBS[0] | {'source_keyword': 'first'})
        self.assertEqual(json.loads(self.state_file.read_text()).get('cardClicks', 0), 0)

    def test_platform_end_stops_without_anomaly(self):
        self.load_more_state(listExhausted=True)
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2']]}
        self.save()
        result = self.browser.search('first', self.city, 40)
        self.assertEqual([row['job_id'] for row in result['rows']], ['job-a1', 'job-a2'])
        self.assertNotIn('load_error', result)
        self.assertEqual(self.events(), ['list-scroll'])

    def test_open_job_reloads_list_up_to_collected_row(self):
        self.load_more_state()
        # 上一个岗位读完详情后停在该岗位：再打开列表会重新从首屏加载。
        self.state['detailMode'] = True
        self.state['detailURL'] = 'https://www.zhipin.com/job_detail/job-a2.html'
        self.save()
        browser = self.browser_with_limit(3)
        browser.open_job(support.JOBS[2] | {'source_keyword': 'first'})
        self.assertEqual(json.loads(self.state_file.read_text())['opened'], 'job-a3')

    def test_load_timeout_keeps_loaded_rows_and_reports_error(self):
        self.load_more_state()
        self.state['keywordBatches'] = {'first': [['job-a1', 'job-a2']]}
        self.save()
        result = self.browser.search('first', self.city, 40)
        self.assertEqual([row['job_id'] for row in result['rows']], ['job-a1', 'job-a2'])
        self.assertIn('加载更多超时或失败', result['load_error'])


def row(job_id, company, boss_id):
    return {
        "job_id": job_id,
        "company": company,
        "boss_id": boss_id,
        "title": "Python 后端工程师",
        "salary": "18-25K",
        "address": "示例城市",
        "href_id": job_id,
    }


class TwoKeywordBrowser:
    """采集边界替身：第一个关键词加载更多失败，第二个照常返回。"""

    mode = "live"

    def __init__(self):
        self.calls = []
        self.limits = []
        self.opened = {}

    def load_history(self):
        return {"history_complete": True, "contacts": []}

    def search(self, keyword, city_code, limit=0):
        self.calls.append(keyword)
        self.limits.append(limit)
        if keyword == "失败词":
            return {
                "rows": [row("job-partial", "示例一号科技", "boss-01")],
                "load_error": "加载更多超时或失败：列表没有新增岗位",
            }
        return {"rows": [row("job-ok", "示例二号科技", "boss-02")]}

    def open_job(self, job):
        self.opened = job
        return {}

    def card_state(self):
        return self.opened | {
            "boss_name": "招聘者",
            "description": "负责后端服务。",
            "can_add_friend": True,
        }


class LoadFailureTests(unittest.TestCase):
    def test_failed_keyword_records_anomaly_and_round_continues(self):
        config = config_module.parse(
            dict(support.BASE_CONFIG) | {"keywords": ["失败词", "正常词"], "collect_per_keyword": 7}
        )
        browser = TwoKeywordBrowser()
        snapshot = sources.EgoSource(browser).collect(
            config, datetime.fromisoformat("2026-03-02T10:00:00+08:00")
        )
        self.assertEqual(browser.calls, ["失败词", "正常词"])
        self.assertEqual(browser.limits, [7, 7])
        self.assertEqual({job["source_keyword"] for job in snapshot["jobs"]}, {"失败词", "正常词"})
        self.assertEqual([item["reason"] for item in snapshot["anomalies"]], ["load_more_failed"])
        anomaly = snapshot["anomalies"][0]
        self.assertEqual(anomaly["source_keyword"], "失败词")
        self.assertIn("加载更多超时或失败", anomaly["detail"])


if __name__ == "__main__":
    unittest.main()
