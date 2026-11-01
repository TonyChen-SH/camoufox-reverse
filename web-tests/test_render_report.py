import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('render_report', Path(__file__).with_name('render_report.py'))
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class ReportContracts(unittest.TestCase):
    def test_renders_known_failure_unknown_coverage_and_escaped_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = {'scenario': 'demo', 'mode': 'engine', 'passed': False,
                    'capture': {'integrity': 'incomplete', 'event_count': 3,
                                'statuses': ['vv8_record_gap'],
                                'first_loss_issue': {'status': 'vv8_record_gap', 'line': 17, 'pid': 1,
                                                     'reason': 'nonconsecutive VV8 record counter: pid=1 tid=2 expected=3 observed=4 missing=1'}},
                    'page': {'page_checks_passed': True, 'actions': [{'event': 'click', 't_ms': 22}],
                             'requests': [], 'responses': [], 'failures': [], 'scripts': []}}
            (root / 'report.json').write_text(json.dumps({'cases': [case], 'passed': False}))
            target = root / 'demo/engine'; target.mkdir(parents=True)
            (target / 'mcp-observations.json').write_text(json.dumps({
                'coverage': {'events_received': 3, 'google_source_events': 2,
                             'observation_groups': 2, 'exported_observation_groups': 2,
                             'omitted_observation_groups': 0},
                'observations': [
                    {'api': '=SECRET()', 'source_url': '<script>alert(1)</script>', 'count': 1,
                     'category': 'browser_environment', 'source_offset': 2, 'pid': 1},
                    {'api': None, 'api_sha256': 'a' * 64, 'count': 1, 'category': 'other_observed_api'}]}))
            result = renderer.render_report(root)
            html = (root / 'report.html').read_text()
            self.assertIn('incomplete', html)
            self.assertIn('vv8_record_gap', html)
            self.assertIn('&quot;expected&quot;: 3', html)
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
            self.assertNotIn('<script>alert(1)</script>', html)
            self.assertIn('未通过', html)
            self.assertIn('API 次数不等于风控规则数', html)
            self.assertEqual(result['observation_rows'], 2)
            with (root / 'observations.csv').open(newline='', encoding='utf-8-sig') as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]['api'], "'=SECRET()")
            self.assertEqual(rows[1]['api_sha256'], 'a' * 64)

    def test_script_comparison_uses_network_hashes_and_keeps_unknowns(self):
        def case(mode, sha):
            return {'scenario': 'demo', 'mode': mode, 'page': {'scripts': [
                {'url': 'https://www.gstatic.com/recaptcha/a.js', 'sha256': sha}]}}
        result = renderer.script_comparisons([case('off', 'a'), case('binding', 'b'), case('engine', None)])
        self.assertEqual(result[0]['state'], 'different')
        self.assertFalse(result[0]['complete'])
        equal = renderer.script_comparisons([case('off', 'a'), case('engine', 'a')])
        self.assertEqual(equal[0]['state'], 'same_observed_set')

    def test_missing_mcp_output_is_visible_and_path_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report.json').write_text(json.dumps({'cases': [{'scenario': 'demo', 'mode': 'off'}]}))
            renderer.render_report(root)
            self.assertIn('缺少 MCP 观察报告', (root / 'report.html').read_text())
            (root / 'report.json').write_text(json.dumps({'cases': [{'scenario': '../private', 'mode': 'off'}]}))
            with self.assertRaises(ValueError):
                renderer.render_report(root)


if __name__ == '__main__':
    unittest.main()
