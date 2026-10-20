"""Transactional, content-checked case storage independent of scheduling.

Each case has an OS-released single-writer lock. A killed or failed producer
leaves its staging directory intact; the next attempt uses a fresh directory.
Only a successfully produced, hashed directory is published under cases/.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def contained(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Artifact path must remain inside its owning directory')
    return path


@contextmanager
def writer_lock(path):
    """OS locks are released on process exit, including interrupted executions."""
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another writer is already running this case') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def artifact_hashes(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink artifacts are not supported')
        contained(directory, path)
        if path.is_file() and path != directory/'complete.json':
            result[path.relative_to(directory).as_posix()] = digest(path)
    return result


@dataclass(frozen=True)
class StoredCase:
    path: Path
    cache_hit: bool


class ExperimentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('cases', 'staging', 'locks'):
            path = contained(self.root, self.root/name)
            path.mkdir(exist_ok=True)

    @staticmethod
    def validate_key(key):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', key):
            raise ValueError('Case key must be a short local identifier')
        if key.endswith('.') or key.split('.')[0].upper() in {
                'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('Reserved case identifier')

    def run(self, key, request, producer):
        """Producer must finish all validation before returning; raises preserve evidence."""
        self.validate_key(key)
        request = json.loads(canonical(request))
        fingerprint = hashlib.sha256(canonical(request).encode('utf-8')).hexdigest()
        final = contained(self.root, self.root/'cases'/key)
        lock = contained(self.root, self.root/'locks'/f'{key}.lock')
        with writer_lock(lock):
            if final.exists():
                receipt = read_json(final/'complete.json')
                if receipt['request_sha256'] != fingerprint or read_json(final/'request.json') != request:
                    raise ValueError('Case exists with a different configuration or provenance')
                if artifact_hashes(final) != receipt['artifacts']:
                    raise ValueError('Cached artifacts are missing, added, or changed')
                return StoredCase(final, True)
            stage = contained(self.root, self.root/'staging'/f'{key}-{uuid.uuid4().hex}')
            stage.mkdir()
            write_json(stage/'request.json', request)
            try:
                producer(stage)
                if (stage/'complete.json').exists() or read_json(stage/'request.json') != request:
                    raise ValueError('Producer changed reserved lifecycle metadata')
                write_json(stage/'complete.json', {'request_sha256': fingerprint,
                    'artifacts': artifact_hashes(stage),
                    'completed_utc': datetime.now(timezone.utc).isoformat()})
                # No replacement: both paths are verified within this store and
                # the single-writer lock is held until publication completes.
                contained(self.root, stage)
                contained(self.root, final)
                if final.exists():
                    raise FileExistsError(final)
                os.rename(stage, final)
            except Exception as error:
                if stage.exists():
                    write_json(stage/'failure.json', {'type': type(error).__name__, 'message': str(error),
                        'failed_utc': datetime.now(timezone.utc).isoformat()})
                raise
            return StoredCase(final, False)
