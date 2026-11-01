"""Capture bounded native stderr evidence before Playwright's log forwarding."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time


def create_wrapper(binary: Path, private: Path, max_bytes: int = 64 * 1024 * 1024) -> Path:
    """Return a private executable launcher; the native process retains its PID."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('The native stderr budget must be a positive integer')
    if any(c.isspace() for c in sys.executable):
        raise ValueError('The launcher interpreter path cannot contain whitespace')
    binary, private = Path(binary).resolve(), Path(private).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise FileNotFoundError('The native executable is required')
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    wrapper = private / 'native-launcher'
    source = ('#!' + sys.executable + '\nimport sys\n'
        + 'sys.path.insert(0, ' + repr(str(Path(__file__).resolve().parent)) + ')\n'
        + 'from native_stderr import launch\n'
        + 'launch(' + repr(str(binary)) + ', ' + repr(str(private)) + ', '
        + repr(max_bytes) + ', sys.argv[1:])\n')
    fd = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, 'w') as stream:
        stream.write(source)
    return wrapper


def _write_all(fd: int, data: bytes) -> None:
    while data:
        written = os.write(fd, data)
        if written == 0:
            raise OSError('Zero-length write')
        data = data[written:]


def _mirror(read_fd: int, raw_fd: int, receipt_fd: int, max_bytes: int) -> None:
    # The helper must not hold Juggler's inherited protocol descriptors open.
    keep = sorted({2, read_fd, raw_fd, receipt_fd})
    lower = 0
    for fd in keep:
        os.closerange(lower, fd)
        lower = fd + 1
    maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    os.closerange(lower, int(maximum if maximum != resource.RLIM_INFINITY else 1048576))
    observed, captured = 0, 0
    observed_hash, captured_hash = hashlib.sha256(), hashlib.sha256()
    errors = {}
    while True:
        try:
            block = os.read(read_fd, 65536)
        except OSError as error:
            errors['read_error_errno'] = error.errno
            break
        if not block:
            break
        observed += len(block)
        observed_hash.update(block)
        if 'write_error_errno' not in errors:
            pending = memoryview(block)[:max(0, max_bytes - captured)]
            try:
                while pending:
                    written = os.write(raw_fd, pending)
                    if written == 0:
                        raise OSError('Zero-length evidence write')
                    captured_hash.update(pending[:written])
                    captured += written
                    pending = pending[written:]
            except OSError as error:
                errors['write_error_errno'] = error.errno
        if 'forward_error_errno' not in errors:
            try:
                _write_all(2, block)
            except OSError as error:
                errors['forward_error_errno'] = error.errno
    try:
        os.fsync(raw_fd)
    except OSError as error:
        errors.setdefault('write_error_errno', error.errno)
    status = ('read_error' if 'read_error_errno' in errors else
              'write_error' if 'write_error_errno' in errors else
              'forward_error' if 'forward_error_errno' in errors else
              'byte_limit' if captured < observed else 'complete')
    receipt = {'schema': 1, 'status': status, 'observed_bytes': observed,
               'captured_bytes': captured, 'max_bytes': max_bytes,
               'sha256': captured_hash.hexdigest(), 'observed_sha256': observed_hash.hexdigest(),
               **errors}
    _write_all(receipt_fd, (json.dumps(receipt, sort_keys=True) + '\n').encode())
    os.fsync(receipt_fd)
    os.close(raw_fd)
    os.close(receipt_fd)
    os.close(read_fd)


def launch(binary: str, private: str, max_bytes: int, arguments: list[str]) -> None:
    """Exec the native process with a separate byte-preserving stderr consumer."""
    raw_fd = os.open(Path(private) / 'native-stderr.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        receipt_fd = os.open(Path(private) / 'native-stderr.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except BaseException:
        os.close(raw_fd)
        raise
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            _mirror(read_fd, raw_fd, receipt_fd, max_bytes)
        except BaseException:
            os._exit(74)
        os._exit(0)
    os.close(read_fd)
    os.close(raw_fd)
    os.close(receipt_fd)
    os.dup2(write_fd, 2)
    os.close(write_fd)
    os.execv(binary, [binary, *arguments])


def wait_receipt(private: Path, timeout: float = 5) -> dict:
    """Wait for EOF evidence without calling a missing receipt complete."""
    deadline = time.monotonic() + timeout
    path = Path(private) / 'native-stderr.json'
    while True:
        try:
            receipt = json.loads(path.read_text())
            if isinstance(receipt, dict) and receipt.get('schema') == 1 and 'status' in receipt:
                return receipt
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if time.monotonic() >= deadline:
            return {'schema': 1, 'status': 'receipt_missing'}
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
