import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('mcp_observations', Path(__file__).with_name('mcp_observations.py'))
observations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observations)


class ObservationContracts(unittest.TestCase):
    def test_public_output_excludes_values_and_dynamic_property_names(self):
        events = [{'api': 'Navigator.userAgent', 'op': 'get', 'channel': 'engine',
                   'pid': 1, 'tid': 2, 'script_id': 3, 'source_offset': 4,
                   'source': {'url': 'https://www.gstatic.com/recaptcha/a.js?token=SECRET', 'line': 2, 'column': 3},
                   'origin': 'https://user:SECRET@www.google.com/recaptcha/api2/anchor?k=SECRET',
                   'args': ['SECRET'], 'result': {'value': 'SECRET'}, 'security_token': 'SECRET'},
                  {'api': 'Object.SECRET', 'source': {'url': 'https://www.gstatic.com/recaptcha/a.js'}, 'op': 'get'}]
        result = observations.build_report({'integrity': 'incomplete', 'statuses': ['vv8_record_gap'],
            'event_count': 2, 'issue_counts': {'vv8_record_gap': 1}}, events, [])
        public = json.dumps(result)
        self.assertNotIn('SECRET', public)
        self.assertIn('Navigator.userAgent', public)
        self.assertEqual(result['integrity'], 'incomplete')
        self.assertEqual(result['coverage']['google_source_events'], 2)
        self.assertFalse(result['server_risk_cause_confirmed'])

    def test_inherited_receivers_keep_their_observed_names(self):
        events = [{'api': api, 'op': 'get', 'source': {'url': 'https://www.gstatic.com/recaptcha/a.js'}}
                  for api in ('HTMLDocument.cookie', 'WorkerNavigator.userAgent', 'PointerEvent.get isTrusted')]
        report = observations.build_report({'event_count': 3}, events, [])
        actual = {row['api']: row['category'] for row in report['observations']}
        self.assertEqual(actual, {'HTMLDocument.cookie': 'storage_and_session',
                                 'WorkerNavigator.userAgent': 'browser_environment',
                                 'PointerEvent.get isTrusted': 'interaction_and_focus'})

    def test_contexts_and_offsets_are_distinct(self):
        events = [{'api': 'Navigator.language', 'op': 'get', 'pid': p, 'tid': 1,
                   'source_offset': n, 'source': {'url': 'https://www.gstatic.com/recaptcha/a.js'}}
                  for p, n in ((1, 10), (1, 20), (2, 10))]
        report = observations.build_report({'event_count': 3}, events, [])
        self.assertEqual(len(report['observations']), 3)
        self.assertEqual(report['coverage']['exported_observation_groups'], 3)

    def test_paginated_contract_rejects_missing_records(self):
        with self.assertRaisesRegex(ValueError, 'matched'):
            observations.validate_page({'matched': 4, 'offset': 0, 'next_offset': None,
                                        'events': [1]}, 'events', 0, None)
        with self.assertRaisesRegex(ValueError, 'offset'):
            observations.validate_page({'matched': 2, 'offset': 0, 'next_offset': 0,
                                        'events': [1]}, 'events', 0, None)

    def test_real_mcp_stdio_paginates_scripts_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = root / 'private', root / 'public'
            private.mkdir()
            records = ['~a', '@"https\\://www.google.com/recaptcha/api2/anchor":"https\\://www.google.com"']
            for index in range(105):
                records.extend([f'${index}:"https\\://www.gstatic.com/recaptcha/a.js":navigator.userAgent;',
                                f'!{index}', 'g0:{1,Navigator}:"userAgent"'])
            trace = private / 'trace.jsonl'
            trace.write_text(''.join(json.dumps({'schema': 1, 'event': 'vv8', 'pid': 1, 'tid': 1,
                'record': index, 'part': 0, 'final': True, 'data': value}) + '\n'
                for index, value in enumerate(records, 1)))
            result = observations.analyze_with_mcp(trace, private, public, page_size=100)
            self.assertTrue(result['passed'])
            report = json.loads((public / 'mcp-observations.json').read_text())
            self.assertEqual(report['coverage']['events_received'], 105)
            self.assertEqual(report['coverage']['scripts_received'], 105)
            self.assertEqual(len(report['scripts']), 105)
            receipts = [json.loads(line) for line in (private / 'mcp-tools.jsonl').read_text().splitlines()]
            self.assertEqual(sum(row.get('tool') == 'get_trace_events' for row in receipts), 2)
            self.assertEqual(sum(row.get('tool') == 'get_trace_scripts' for row in receipts), 2)


if __name__ == '__main__':
    unittest.main()
