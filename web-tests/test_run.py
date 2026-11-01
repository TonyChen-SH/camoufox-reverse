import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('web_run', Path(__file__).with_name('run.py'))
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class Contracts(unittest.TestCase):
    def test_public_urls_omit_credentials_query_and_fragment(self):
        url = 'https://user:secret@www.google.com:443/recaptcha/api2/anchor?k=SECRET#TOKEN'
        self.assertEqual(web.clean_url(url), 'https://www.google.com:443/recaptcha/api2/anchor')
        self.assertEqual(web.clean_url('data:text/plain,SECRET'), 'data:')
        self.assertEqual(web.clean_url('blob:https://example.test/SECRET'), 'blob:')
        self.assertFalse(web.google_source('https://www.google.com.attacker.test/recaptcha/a'))
        self.assertTrue(web.google_source('https://www.gstatic.com/recaptcha/releases/version/recaptcha__en.js'))

    def test_public_error_messages_are_not_exported(self):
        page = {'checks': {'ok': True}, 'errors': [{'type': 'Error', 'message': 'TOKEN'}],
                'page_errors': ['SECRET'], 'activation_frame_text': 'PRIVATE CHALLENGE TEXT',
                'scripts': [{'read_error': 'Error', 'read_error_message': 'PRIVATE SIGNED URL'}]}
        result = web.safe_page(page)
        self.assertNotIn('PRIVATE SIGNED URL', json.dumps(result))
        self.assertNotIn('TOKEN', json.dumps(result))
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('PRIVATE CHALLENGE TEXT', json.dumps(result))
        self.assertEqual(result['checks'], page['checks'])

    def test_current_reader_and_mode_contracts(self):
        collection = {'collector_schema': 1, 'event': 'collection', 'termination': 'normal_exit',
                      'returncode': 0, 'output_limited': False, 'stderr_drain_incomplete': False, 'raw_stderr': {'status': 'complete'}}
        records = ['~a', '@"https\\://www.google.com/recaptcha/api2/anchor":"https\\://www.google.com"',
                   '$1:"https\\://www.gstatic.com/recaptcha/test.js":navigator.userAgent;',
                   '!1', 'g0:{1,Navigator}:"userAgent"']
        envelopes = [{'schema': 1, 'event': 'vv8', 'pid': 7, 'tid': 1, 'record': i,
                      'part': 0, 'final': True, 'data': value} for i, value in enumerate(records, 1)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = root / 'private', root / 'public'
            web.write_json(private / 'page.json', {'page_checks_passed': True, 'browser_closed': True, 'native_stderr': {'status': 'complete'}})
            trace = private / 'trace.jsonl'
            trace.write_text(json.dumps(collection) + '\n')
            off = web.analyze_case(trace, 'off', 'demo', private, public, collection)
            self.assertTrue(off['passed'])
            trace.write_text(''.join(json.dumps(record) + '\n' for record in envelopes + [collection]))
            engine = web.analyze_case(trace, 'engine', 'demo', private, public, collection)
            self.assertTrue(engine['passed'])
            self.assertEqual(engine['google_attributed_events'], 1)
            self.assertEqual(engine['engine_script_count'], 1)
            wrong_mode = web.analyze_case(trace, 'binding', 'demo', private, public, collection)
            self.assertFalse(wrong_mode['passed'])
            loss = {'schema': 1, 'event': 'limit', 'pid': 7, 'channel': 'engine',
                    'emitted': 1, 'reason': 'engine_record_cap'}
            trace.write_text(''.join(json.dumps(record) + '\n' for record in envelopes + [loss, collection]))
            incomplete = web.analyze_case(trace, 'engine', 'demo', private, public, collection)
            self.assertFalse(incomplete['passed'])
            self.assertEqual(incomplete['capture']['integrity'], 'incomplete')

    def test_missing_transport_receipts_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = root / 'private', root / 'public'
            web.write_json(private / 'page.json', {'page_checks_passed': True, 'browser_closed': True})
            trace = private / 'trace.jsonl'
            collection = {'collector_schema': 1, 'event': 'collection',
                          'termination': 'normal_exit', 'returncode': 0}
            trace.write_text(json.dumps(collection) + '\n')
            self.assertFalse(web.analyze_case(trace, 'off', 'demo', private, public, collection)['passed'])

    def test_evidence_encryption_roundtrip_and_plain_archive_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'private'
            private.mkdir()
            (private / 'trace.txt').write_text('PRIVATE TEST TOKEN')
            key, cert = root / 'key.pem', root / 'cert.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(key), '-out', str(cert), '-subj', '/CN=Test', '-days', '1'],
                check=True, capture_output=True)
            destination = root / 'encrypted.cms'
            web.encrypt_private(private, cert, destination)
            self.assertFalse((root / 'private.tar.gz').exists())
            self.assertNotIn(b'PRIVATE TEST TOKEN', destination.read_bytes())
            clear = subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER',
                '-in', str(destination), '-inkey', str(key), '-recip', str(cert)],
                check=True, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(clear), mode='r:gz') as tar:
                self.assertEqual(tar.extractfile('private-evidence/trace.txt').read(), b'PRIVATE TEST TOKEN')

    def test_missing_binary_and_nested_private_output_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(binary=root / 'missing', recipient=root / 'cert',
                source_baseline=root / 'baseline', output=root / 'public', private_output=root / 'public/private')
            with self.assertRaises(FileNotFoundError):
                web.matrix(args)
            args.binary = Path('/bin/true')
            args.recipient.touch()
            args.source_baseline.touch()
            with self.assertRaisesRegex(ValueError, 'outside'):
                web.matrix(args)
            self.assertFalse(args.output.exists())


if __name__ == '__main__':
    unittest.main()
