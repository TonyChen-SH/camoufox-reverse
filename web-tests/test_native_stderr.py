import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_stderr', Path(__file__).with_name('native_stderr.py'))
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class NativeStderr(unittest.TestCase):
    def run_browser(self, root, payload, limit):
        binary = root / 'native browser'
        binary.write_text('#!' + sys.executable + '\nimport os, sys\n'
            + 'os.write(2, ' + repr(payload) + ')\n'
            + 'os.write(3, b"protocol-three")\nos.write(4, b"protocol-four")\n'
            + 'assert sys.argv[1:] == ["with spaces", "--flag=value"]\n')
        binary.chmod(0o700)
        private = root / 'private evidence'
        wrapper = mirror.create_wrapper(binary, private, max_bytes=limit)
        control = '''import os, sys
for expected, name in ((3, 'three'), (4, 'four')):
    fd = os.open(sys.argv[2] + '/' + name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    assert fd == expected, (fd, expected)
    os.set_inheritable(fd, True)
os.execv(sys.argv[1], [sys.argv[1], 'with spaces', '--flag=value'])
'''
        process = subprocess.run([sys.executable, '-c', control, str(wrapper), str(root)],
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, b'')
        self.assertEqual(process.stderr, payload)
        self.assertEqual((root / 'three').read_bytes(), b'protocol-three')
        self.assertEqual((root / 'four').read_bytes(), b'protocol-four')
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        return private, mirror.wait_receipt(private, timeout=2)

    def test_exact_bytes_and_inherited_protocol_descriptors(self):
        payload = b'\x00\xff\r\nCAMOUFOX_TRACE {"event":"test"}\n' * 4000
        with tempfile.TemporaryDirectory() as directory:
            private, receipt = self.run_browser(Path(directory), payload, len(payload))
            self.assertEqual((private / 'native-stderr.bin').read_bytes(), payload)
            self.assertEqual(receipt['status'], 'complete')
            self.assertEqual(receipt['observed_bytes'], len(payload))
            self.assertEqual(receipt['captured_bytes'], len(payload))
            self.assertEqual(receipt['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(receipt['observed_sha256'], receipt['sha256'])
            for name in ('native-stderr.bin', 'native-stderr.json'):
                self.assertEqual((private / name).stat().st_mode & 0o777, 0o600)

    def test_cap_preserves_forwarding_and_exposes_incomplete_capture(self):
        payload = bytes(range(256)) * 1000
        with tempfile.TemporaryDirectory() as directory:
            private, receipt = self.run_browser(Path(directory), payload, 4096)
            self.assertEqual((private / 'native-stderr.bin').read_bytes(), payload[:4096])
            self.assertEqual(receipt['status'], 'byte_limit')
            self.assertEqual(receipt['observed_bytes'], len(payload))
            self.assertEqual(receipt['captured_bytes'], 4096)
            self.assertEqual(receipt['sha256'], hashlib.sha256(payload[:4096]).hexdigest())
            self.assertEqual(receipt['observed_sha256'], hashlib.sha256(payload).hexdigest())

    def test_sink_failure_stays_visible_while_forwarding_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = """import os, sys
sys.path.insert(0, sys.argv[1])
from native_stderr import _mirror
read_fd, write_fd = os.pipe()
os.write(write_fd, b'original bytes')
os.close(write_fd)
raw_fd = os.open('/dev/full', os.O_WRONLY)
receipt_fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
_mirror(read_fd, raw_fd, receipt_fd, 1024)
"""
            process = subprocess.run([sys.executable, '-c', control,
                str(Path(__file__).parent), str(root / 'receipt.json')], capture_output=True, timeout=10)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stderr, b'original bytes')
            receipt = json.loads((root / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'write_error')
            self.assertEqual(receipt['write_error_errno'], 28)
            self.assertEqual(receipt['observed_bytes'], 14)
            self.assertEqual(receipt['captured_bytes'], 0)

    def test_missing_receipt_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(mirror.wait_receipt(Path(directory), timeout=0)['status'], 'receipt_missing')

    def test_wrapper_creation_is_exclusive_and_budget_is_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                mirror.create_wrapper(Path('/bin/true'), root / 'bad', max_bytes=0)
            mirror.create_wrapper(Path('/bin/true'), root / 'private', max_bytes=1)
            with self.assertRaises(FileExistsError):
                mirror.create_wrapper(Path('/bin/true'), root / 'private', max_bytes=1)


if __name__ == '__main__':
    unittest.main()
