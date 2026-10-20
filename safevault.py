#!/usr/bin/env python3
"""SafeVault student prototype: standard library + AWS CLI v2 on Ubuntu."""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import catalog  # file type / category detection (catalog.py)

VERSION = '1.0.0'
MAX_RECORD = 2 * 1024 * 1024
MAX_MANIFEST = 16 * 1024 * 1024

class VaultError(Exception):
    pass

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.safevault-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def load_json(path, limit=MAX_MANIFEST):
    if Path(path).stat().st_size > limit:
        raise VaultError('JSON exceeds configured safety limit')
    with open(path) as f:
        return json.load(f)

def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise VaultError('Invalid relative path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ('', '.', '..') for x in value.split('/')):
        raise VaultError('Unsafe path: ' + repr(value))
    if str(p) != value:
        raise VaultError('Non-canonical path')
    return value

def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents

def validate_config(c):
    if c.get('backend') not in ('local', 'aws'):
        raise VaultError('backend must be local or aws')
    for field in ('source_directory', 'state_directory'):
        if not isinstance(c.get(field), str) or not Path(c[field]).is_absolute():
            raise VaultError(field + ' must be an absolute path')
    src = Path(c['source_directory']).resolve()
    state = Path(c['state_directory']).resolve()
    if src == Path('/') or overlaps(src, state):
        raise VaultError('Source must not be root or overlap private state')
    prefix = c.get('prefix', '').rstrip('/')
    safe_relative(prefix)
    c['prefix'] = prefix
    if c['backend'] == 'local':
        if not Path(c.get('local_store', '')).is_absolute():
            raise VaultError('local_store must be absolute')
        store = Path(c['local_store']).resolve()
        if overlaps(src, store) or overlaps(state, store):
            raise VaultError('Local store must be separate from source and state')
    else:
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', c.get('bucket', '')):
            raise VaultError('Invalid general-purpose bucket name')
        if not c.get('region'):
            raise VaultError('region required')
        if c.get('expected_bucket_owner') and not re.fullmatch(r'\d{12}', c['expected_bucket_owner']):
            raise VaultError('expected_bucket_owner must be a 12-digit account ID')
    for k, default in [('max_source_bytes', 256 * 1024 * 1024),
                       ('max_archive_bytes', 300 * 1024 * 1024),
                       ('max_files', 10000)]:
        c.setdefault(k, default)
        if not isinstance(c[k], int) or c[k] <= 0:
            raise VaultError(k + ' must be positive')
    return c

def config(path):
    return validate_config(load_json(path))

class LocalStore:
    """Offline versioned filesystem, NOT Object Lock, IAM, or cloud isolation."""
    def __init__(self, c):
        self.c = c
        self.root = Path(c['local_store'])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    def _folder(self, key):
        safe_relative(key)
        return self.root / hashlib.sha256(key.encode()).hexdigest()
    def put(self, key, path):
        version = uuid.uuid4().hex
        folder = self._folder(key)
        folder.mkdir(exist_ok=True, mode=0o700)
        shutil.copyfile(path, folder / (version + '.blob'))
        os.chmod(folder / (version + '.blob'), 0o600)
        info = {'Key': key, 'VersionId': version, 'LastModified': utc(),
                'Size': Path(path).stat().st_size, 'ChecksumSHA256':
                base64.b64encode(bytes.fromhex(digest(path))).decode()}
        save_json(folder / (version + '.json'), info)
        return info
    def get(self, key, version, dest, max_bytes=None):
        if not re.fullmatch(r'[a-f0-9]{32}', version):
            raise VaultError('Invalid local version ID')
        source = self._folder(key) / (version + '.blob')
        if not source.is_file():
            raise VaultError('Local version not found')
        if max_bytes is not None and source.stat().st_size > max_bytes:
            raise VaultError('Object exceeds download safety limit')
        shutil.copyfile(source, dest)
    def list_records(self):
        items = []
        for p in self.root.glob('*/*.json'):
            data = load_json(p, MAX_RECORD)
            if data['Key'].startswith(self.c['prefix'] + '/') and data['Key'].endswith('/complete.json'):
                items.append(data)
        return sorted(items, key=lambda x: x['LastModified'], reverse=True)
    def metric(self, values):
        return None

class AWSStore:
    def __init__(self, c):
        self.c = c
    def _run(self, args):
        command = ['aws', '--region', self.c['region'], '--output', 'json',
                   '--no-cli-pager', '--no-cli-auto-prompt']
        if self.c.get('profile'):
            command += ['--profile', self.c['profile']]
        env = dict(os.environ, AWS_PAGER='', AWS_RETRY_MODE='standard', AWS_MAX_ATTEMPTS='3')
        try:
            result = subprocess.run(command + args, capture_output=True, text=True,
                                    timeout=600, env=env, check=False)
        except FileNotFoundError:
            raise VaultError('AWS CLI v2 not found; install it on Ubuntu first') from None
        except subprocess.TimeoutExpired:
            raise VaultError('AWS command exceeded 600 seconds; upload may exist: inspect with trusted role') from None
        if result.returncode:
            # Never echo commands with credential arguments: this app uses profiles/roles only.
            raise VaultError('AWS operation failed: ' + result.stderr.strip()[-1600:])
        try:
            return json.loads(result.stdout or '{}')
        except json.JSONDecodeError:
            raise VaultError('Unexpected AWS CLI response; use a current CLI v2') from None
    def _owner(self):
        return ['--expected-bucket-owner', self.c['expected_bucket_owner']] if self.c.get('expected_bucket_owner') else []
    def put(self, key, path):
        checksum = base64.b64encode(bytes.fromhex(digest(path))).decode()
        response = self._run(['s3api', 'put-object', '--bucket', self.c['bucket'], '--key', key,
                             '--body', str(path), '--checksum-algorithm', 'SHA256',
                             '--checksum-sha256', checksum, '--server-side-encryption', 'AES256'] + self._owner())
        version = response.get('VersionId')
        if not version or version == 'null':
            raise VaultError('Upload did not return a non-null VersionId; require S3 Versioning')
        if response.get('ChecksumSHA256') != checksum:
            raise VaultError('S3 response SHA256 missing/mismatched; do not mark run complete')
        return response
    def get(self, key, version, dest, max_bytes=None):
        if not isinstance(version, str) or not version or version == 'null':
            raise VaultError('An explicit non-null VersionId is required')
        if max_bytes is not None:
            meta = self._run(['s3api', 'head-object', '--bucket', self.c['bucket'], '--key', key,
                              '--version-id', version] + self._owner())
            size = meta.get('ContentLength')
            if not isinstance(size, int) or size < 0 or size > max_bytes:
                raise VaultError('Object exceeds download limit or has invalid size')
        self._run(['s3api', 'get-object', '--bucket', self.c['bucket'], '--key', key,
                   '--version-id', version, '--checksum-mode', 'ENABLED'] + self._owner() + [str(dest)])
    def list_records(self):
        result = self._run(['s3api', 'list-object-versions', '--bucket', self.c['bucket'],
                            '--prefix', self.c['prefix'] + '/'] + self._owner())
        items = [v for v in result.get('Versions', []) if v['Key'].endswith('/complete.json')]
        return sorted(items, key=lambda x: x['LastModified'], reverse=True)
    def metric(self, values):
        if not self.c.get('monitoring', False):
            return
        data = []
        for name, value in values.items():
            unit = 'Seconds' if name.endswith('Seconds') else ('Bytes' if name.endswith('Bytes') else 'Count')
            data.append({'MetricName': name, 'Value': value, 'Unit': unit,
                         'Dimensions': [{'Name': 'SourceId', 'Value': self.c.get('source_id', 'source-01')}]})
        self._run(['cloudwatch', 'put-metric-data', '--namespace', 'SafeVault',
                   '--metric-data', json.dumps(data)])

def store(c):
    return AWSStore(c) if c['backend'] == 'aws' else LocalStore(c)

def log(c, event, **fields):
    folder = Path(c['state_directory'])
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = dict(time=utc(), event=event, **fields)
    with open(folder / 'events.jsonl', 'a') as f:
        f.write(json.dumps(payload, sort_keys=True) + '\n')
    print(json.dumps(payload, sort_keys=True))

@contextlib.contextmanager
def job_lock(c):
    folder = Path(c['state_directory'])
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(folder / 'backup.lock', 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise VaultError('Another backup is running; overlapping run refused') from None
        yield

def snapshot(c, stage):
    src = Path(c['source_directory'])
    if not src.is_dir() or src.is_symlink():
        raise VaultError('Source must be an existing real directory, not a symlink')
    files, directories, total = [], [], 0
    for base, dirs, names in os.walk(src, followlinks=False):
        dirs.sort(); names.sort()
        basep = Path(base)
        for name in dirs:
            p = basep / name
            if not stat.S_ISDIR(p.lstat().st_mode) or p.is_symlink():
                raise VaultError('Source links or special directories are not supported')
            rel = safe_relative(p.relative_to(src).as_posix())
            (stage / rel).mkdir(parents=True, exist_ok=True, mode=0o700)
            directories.append(rel)
        for name in names:
            p = basep / name
            rel = safe_relative(p.relative_to(src).as_posix())
            before = p.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise VaultError('Source links/special files refused: ' + rel)
            if len(files) >= c['max_files'] or total + before.st_size > c['max_source_bytes']:
                raise VaultError('Source exceeds configured size/file-count safety limit')
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # O_NOFOLLOW avoids following a final-component link swapped during the read.
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as inp, open(target, 'wb') as out:
                opened = os.fstat(inp.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise VaultError('Source changed during snapshot')
                shutil.copyfileobj(inp, out, 1024 * 1024)
                after_fd = os.fstat(inp.fileno())
            after = p.lstat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(before) != signature(after) or signature(before) != signature(after_fd):
                raise VaultError('Source changed while copying; pause writers and retry')
            total += target.stat().st_size
            if total > c['max_source_bytes']:
                raise VaultError('Source grew beyond safety limit')
            files.append({'path': rel, 'size': target.stat().st_size, 'sha256': digest(target),
                          'category': catalog.classify(target)['category']})
    if not files:
        raise VaultError('Empty source refused: investigate accidental deletion before backing up')
    return files, directories, total

def backup(c, cloud=None):
    cloud = cloud or store(c)
    start = time.monotonic()
    with job_lock(c):
        run = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        cutoff = utc()
        try:
            with tempfile.TemporaryDirectory(dir=c['state_directory'], prefix='stage-') as tmp:
                tmp = Path(tmp)
                data = tmp / 'data'; data.mkdir(mode=0o700)
                files, directories, total = snapshot(c, data)
                manifest = {'schema': 1, 'run_id': run, 'source_id': c.get('source_id', 'source-01'),
                            'data_cutoff_utc': cutoff, 'capture_finished_utc': utc(),
                            'capture_method': 'sequential-copy; operator must pause writers for consistent point',
                            'files': files, 'directories': directories}
                raw = json.dumps(manifest, sort_keys=True).encode()
                if len(raw) > MAX_MANIFEST:
                    raise VaultError('Manifest too large')
                archive = tmp / 'data.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    m = tarfile.TarInfo('manifest.json'); m.size = len(raw); m.mode = 0o600
                    tar.addfile(m, io.BytesIO(raw))
                    for name in directories:
                        m = tarfile.TarInfo('data/' + name); m.type = tarfile.DIRTYPE; m.mode = 0o700
                        tar.addfile(m)
                    for entry in files:
                        tar.add(data / entry['path'], arcname='data/' + entry['path'], recursive=False)
                size = archive.stat().st_size
                if size > c['max_archive_bytes']:
                    raise VaultError('Compressed archive exceeds configured limit')
                with tarfile.open(archive, 'r:gz') as tar:
                    if len(tar.getmembers()) != len(files) + len(directories) + 1:
                        raise VaultError('Archive validation failed')
                archive_key = c['prefix'] + '/' + run + '/data.tar.gz'
                archive_hash = digest(archive)
                uploaded = cloud.put(archive_key, archive)
                record = {'schema': 1, 'run_id': run, 'source_id': manifest['source_id'],
                          'backend': c['backend'], 'archive_key': archive_key,
                          'archive_version_id': uploaded['VersionId'], 'archive_sha256': archive_hash,
                          'archive_bytes': size, 'source_bytes': total, 'file_count': len(files),
                          'data_cutoff_utc': cutoff, 'uploaded_utc': utc(),
                          'status': 'uploaded-not-independently-verified'}
                completion = tmp / 'complete.json'; save_json(completion, record)
                record_key = c['prefix'] + '/' + run + '/complete.json'
                done = cloud.put(record_key, completion)
                receipt = dict(record, record_key=record_key, record_version_id=done['VersionId'])
                save_json(Path(c['state_directory']) / 'receipts' / (run + '.json'), receipt)
                duration = time.monotonic() - start
                log(c, 'backup_uploaded', run_id=run, duration_seconds=duration,
                    record_key=record_key, record_version_id=done['VersionId'], archive_bytes=size)
                try:
                    cloud.metric({'BackupSucceeded': 1, 'BackupDurationSeconds': duration, 'BackupBytes': size})
                except VaultError as e:
                    log(c, 'monitoring_failed', error=str(e), backup_upload_completed=True)
                    raise VaultError('Backup uploaded but metrics failed; see receipt and inspect monitoring') from e
                return receipt
        except Exception as e:
            log(c, 'backup_job_failed', run_id=run, error=str(e))
            try:
                cloud.metric({'BackupFailed': 1})
            except Exception:
                pass
            raise

def verify_record(c, r, record_key):
    if r.get('schema') != 1 or not isinstance(r.get('run_id'), str):
        raise VaultError('Unsupported completion record')
    if not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{12}', r['run_id']):
        raise VaultError('Invalid run ID')
    expected = c['prefix'] + '/' + r['run_id']
    if record_key != expected + '/complete.json' or r.get('archive_key') != expected + '/data.tar.gz':
        raise VaultError('Record references an unexpected key/prefix')
    if not isinstance(r.get('archive_bytes'), int) or not 0 < r['archive_bytes'] <= c['max_archive_bytes']:
        raise VaultError('Archive size outside configured bound')
    if not re.fullmatch(r'[a-f0-9]{64}', r.get('archive_sha256', '')):
        raise VaultError('Invalid archive hash')
    if not r.get('archive_version_id') or r['archive_version_id'] == 'null':
        raise VaultError('Missing archive VersionId')

def restore(c, record_key, record_version, destination, selected=None, cloud=None):
    cloud = cloud or store(c)
    start = time.monotonic()
    safe_relative(record_key)
    if not record_key.startswith(c['prefix'] + '/') or not record_key.endswith('/complete.json'):
        raise VaultError('Select a completion record within configured source prefix')
    if not record_version or record_version == 'null':
        raise VaultError('Explicit completion VersionId required')
    # selected: None = every file, a path string = one file, a function(entry) -> bool = a category
    if isinstance(selected, str):
        safe_relative(selected)
        wanted = lambda entry, name=selected: entry['path'] == name
    elif callable(selected):
        wanted = selected
    else:
        wanted = None
    dest = Path(destination).expanduser().absolute()
    if dest.exists() or dest.is_symlink():
        raise VaultError('Destination already exists; choose a new empty destination path')
    for protected in [Path(c['source_directory']).resolve(), Path(c['state_directory']).resolve()] + (
            [Path(c['local_store']).resolve()] if c['backend'] == 'local' else []):
        if overlaps(dest.resolve(), protected):
            raise VaultError('Restore destination overlaps source/state/store')
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix='.safevault-restore-') as work:
        work = Path(work)
        record_path = work / 'complete.json'
        cloud.get(record_key, record_version, record_path, max_bytes=MAX_RECORD)
        r = load_json(record_path, MAX_RECORD)
        verify_record(c, r, record_key)
        archive = work / 'data.tar.gz'
        cloud.get(r['archive_key'], r['archive_version_id'], archive, max_bytes=c['max_archive_bytes'])
        if archive.stat().st_size != r['archive_bytes'] or digest(archive) != r['archive_sha256']:
            raise VaultError('Archive integrity check failed; no files restored')
        output = work / 'restored'; output.mkdir(mode=0o700)
        with tarfile.open(archive, 'r:gz') as tar:
            members = []
            names = set()
            expanded = 0
            for m in tar:
                safe_relative(m.name)
                if m.name in names or not (m.isfile() or m.isdir()):
                    raise VaultError('Duplicate entry/link/special file in archive')
                names.add(m.name); members.append(m)
                expanded += m.size
                if len(members) > 2 * c['max_files'] + 1000 or expanded > c['max_source_bytes'] + MAX_MANIFEST:
                    raise VaultError('Expanded archive exceeds configured safety limit')
            by_name = {m.name: m for m in members}
            mm = by_name.get('manifest.json')
            if not mm or not mm.isfile() or mm.size > MAX_MANIFEST:
                raise VaultError('Missing or oversized manifest')
            with tar.extractfile(mm) as f:
                manifest = json.loads(f.read())
            if manifest.get('schema') != 1 or manifest.get('run_id') != r['run_id'] or manifest.get('data_cutoff_utc') != r['data_cutoff_utc']:
                raise VaultError('Manifest/record mismatch')
            entries = manifest.get('files')
            directories = manifest.get('directories')
            if not isinstance(entries, list) or not isinstance(directories, list) or len(entries) > c['max_files']:
                raise VaultError('Invalid manifest file/directory list')
            if len(entries) != r['file_count']:
                raise VaultError('File count mismatch')
            expected_files, expected_dirs = {}, set()
            for d in directories:
                safe_relative(d)
                if d in expected_dirs:
                    raise VaultError('Duplicate manifest directory')
                expected_dirs.add(d)
            for entry in entries:
                rel = safe_relative(entry['path'])
                if rel in expected_files or rel in expected_dirs:
                    raise VaultError('Duplicate/conflicting manifest path')
                if not isinstance(entry.get('size'), int) or entry['size'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')):
                    raise VaultError('Invalid manifest size/hash')
                expected_files[rel] = entry
            allowed = {'manifest.json'} | {'data/' + p for p in expected_files} | {'data/' + p for p in expected_dirs}
            if names != allowed:
                raise VaultError('Archive has missing or undeclared entries')
            if isinstance(selected, str) and selected not in expected_files:
                raise VaultError('Requested file is absent from selected backup')
            # Validate every file even for single-file restore; do not extract arbitrary tar entries.
            written = 0
            for rel, entry in expected_files.items():
                m = by_name['data/' + rel]
                if not m.isfile() or m.size != entry['size']:
                    raise VaultError('Archive/manifest entry mismatch')
                target = output / rel
                write_it = wanted is None or bool(wanted(entry))
                written += 1 if write_it else 0
                if write_it:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                h = hashlib.sha256()
                with tar.extractfile(m) as inp:
                    with (open(target, 'wb') if write_it else contextlib.nullcontext(None)) as out:
                        for block in iter(lambda: inp.read(1024 * 1024), b''):
                            h.update(block)
                            if out:
                                out.write(block)
                if h.hexdigest() != entry['sha256']:
                    raise VaultError('Restored file hash mismatch: ' + rel)
                if write_it:
                    os.chmod(target, 0o600)
            for rel in expected_dirs:
                if not by_name['data/' + rel].isdir():
                    raise VaultError('Declared directory is not a directory')
                if wanted is None:
                    (output / rel).mkdir(parents=True, exist_ok=True, mode=0o700)
        # Dedicated unprivileged operator and private parent required; refuse rather than merge.
        if dest.exists() or dest.is_symlink():
            raise VaultError('Destination appeared during restore; refusing overwrite')
        os.rename(output, dest)
    result = {'run_id': r['run_id'], 'record_key': record_key, 'record_version_id': record_version,
              'destination': str(dest), 'restored_files': written,
              'validated_files': r['file_count'], 'selected_file': selected if isinstance(selected, str) else None,
              'data_cutoff_utc': r['data_cutoff_utc'], 'verified_utc': utc(),
              'restore_seconds': time.monotonic() - start,
              'scope': 'download/extract/verify; excludes EC2 provisioning and application readiness',
              'status': 'bytes-verified; operator must confirm application usability'}
    audit = Path(c['state_directory']) / 'restore-reports' / (uuid.uuid4().hex + '.json')
    save_json(audit, result)
    log(c, 'restore_verified', **result)
    result['catalog'] = [{'path': e['path'], 'size': e['size'], 'category': e.get('category')}
                         for e in expected_files.values()]
    return result

def audit_retention(c, record_key, record_version, cloud=None):
    if c['backend'] != 'aws':
        raise VaultError('Retention audit requires AWS: offline local storage is NOT immutable')
    safe_relative(record_key)
    if not record_key.startswith(c['prefix'] + '/') or not record_key.endswith('/complete.json'):
        raise VaultError('Record must be within configured prefix')
    if not record_version or record_version == 'null':
        raise VaultError('Explicit record VersionId required')
    cloud = cloud or AWSStore(c)
    state = Path(c['state_directory']); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=state, prefix='audit-') as tmp:
        path = Path(tmp) / 'record.json'
        cloud.get(record_key, record_version, path, max_bytes=MAX_RECORD)
        r = load_json(path, MAX_RECORD); verify_record(c, r, record_key)
    checks = []
    for key, version in [(record_key, record_version), (r['archive_key'], r['archive_version_id'])]:
        answer = cloud._run(['s3api', 'get-object-retention', '--bucket', c['bucket'],
                             '--key', key, '--version-id', version] + cloud._owner())
        retention = answer.get('Retention', {})
        until = retention.get('RetainUntilDate', '')
        try:
            expiry = dt.datetime.fromisoformat(until.replace('Z', '+00:00'))
        except ValueError:
            raise VaultError('Missing/invalid retain-until date: ' + key) from None
        if retention.get('Mode') not in ('GOVERNANCE', 'COMPLIANCE') or expiry <= dt.datetime.now(dt.timezone.utc):
            raise VaultError('Version has no active fixed retention: ' + key)
        checks.append({'key': key, 'version_id': version, 'mode': retention['Mode'], 'retain_until': until})
    result = {'run_id': r['run_id'], 'checked_utc': utc(), 'checks': checks,
              'scope': 'Active fixed retention metadata only; deletion/IAM enforcement tests must be run separately'}
    save_json(state / 'retention-reports' / (uuid.uuid4().hex + '.json'), result)
    print(json.dumps(result, indent=2))
    return result

def init_config(args):
    destination = Path(args.config).expanduser().absolute()
    if destination.exists():
        raise VaultError('Config exists; edit it deliberately rather than overwrite')
    c = {'backend': args.backend, 'source_directory': str(Path(args.source).expanduser().absolute()),
         'state_directory': str(Path(args.state).expanduser().absolute()),
         'source_id': 'source-01', 'prefix': args.prefix, 'monitoring': False,
         'max_source_bytes': 256 * 1024 * 1024, 'max_archive_bytes': 300 * 1024 * 1024,
         'max_files': 10000}
    if args.backend == 'aws':
        c.update(bucket=args.bucket, region=args.region)
        if args.profile: c['profile'] = args.profile
        if args.owner: c['expected_bucket_owner'] = args.owner
    else:
        c['local_store'] = str(Path(args.local_store).expanduser().absolute())
    validate_config(c)
    save_json(destination, c)
    print('Config created with private permissions: ' + str(destination))

def demo(directory):
    root = Path(directory).expanduser().absolute()
    if root.exists():
        raise VaultError('Demo directory already exists; choose a NEW path')
    root.mkdir(parents=True, mode=0o700)
    source = root / 'source'; source.mkdir(mode=0o700)
    (source / 'records').mkdir(); (source / 'website').mkdir(); (source / 'empty').mkdir()
    (source / 'records' / 'attendance.csv').write_text('student_id,present\nDEMO001,yes\nDEMO002,no\n')
    (source / 'website' / 'index.html').write_text('<h1>SafeVault synthetic department</h1>\n')
    (source / 'file with spaces.txt').write_text('Synthetic project data only.\n')
    (source / 'empty-file.txt').touch()
    (source / 'sample.bin').write_bytes(os.urandom(256 * 1024))
    c = validate_config({'backend': 'local', 'source_directory': str(source),
                         'state_directory': str(root / 'state'), 'local_store': str(root / 'local-store'),
                         'prefix': 'backups/source-01', 'source_id': 'source-01', 'monitoring': False})
    save_json(root / 'config.local.json', c)
    # Independent hash evidence is saved BEFORE any corruption.
    original = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
    save_json(root / 'known-good-hashes.json', original)
    good = backup(c)
    incident = utc()
    # Only the two synthetic files just created by this function are changed.
    (source / 'records' / 'attendance.csv').write_text('SIMULATED UNREADABLE SYNTHETIC CONTENT\n')
    (source / 'website' / 'index.html').write_text('SIMULATED CORRUPTION: no real malware\n')
    bad = backup(c)
    result = restore(c, good['record_key'], good['record_version_id'], root / 'restored-good')
    actual = {p.relative_to(root / 'restored-good').as_posix(): digest(p) for p in (root / 'restored-good').rglob('*') if p.is_file()}
    if actual != original:
        raise VaultError('Offline demonstration hash comparison failed')
    summary = {'backend': 'local-offline', 'cloud_security_validated': False,
               'clean_run': good['run_id'], 'corrupt_run': bad['run_id'],
               'incident_utc': incident, 'all_original_hashes_matched': True,
               'observed_recovery_point_age_seconds': (dt.datetime.fromisoformat(incident) - dt.datetime.fromisoformat(good['data_cutoff_utc'])).total_seconds(),
               'restore': result,
               'warning': 'Local filesystem is not IAM or Object Lock. AWS security/retention/lifecycle tests remain unexecuted.'}
    save_json(root / 'demo-results.json', summary)
    print(json.dumps(summary, indent=2))
    return summary

def write_policies(bucket, prefix, output):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise VaultError('Invalid bucket name')
    safe_relative(prefix)
    out = Path(output).expanduser().absolute()
    if out.exists():
        raise VaultError('Policy output already exists; choose a new directory')
    out.mkdir(parents=True, mode=0o700)
    arn = 'arn:aws:s3:::' + bucket
    objs = arn + '/' + prefix + '/*'
    uploader = {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'UploadOwnPrefixOnly', 'Effect': 'Allow', 'Action': 's3:PutObject', 'Resource': objs},
        {'Sid': 'DenyReadDeleteRetention', 'Effect': 'Deny', 'Action': [
            's3:GetObject', 's3:GetObjectVersion', 's3:DeleteObject', 's3:DeleteObjectVersion',
            's3:PutObjectRetention', 's3:PutObjectLegalHold', 's3:BypassGovernanceRetention'], 'Resource': arn + '/*'},
        {'Sid': 'DenyBucketAdministrationAndListing', 'Effect': 'Deny', 'Action': [
            's3:ListBucket', 's3:ListBucketVersions', 's3:DeleteBucket', 's3:PutBucketPolicy',
            's3:DeleteBucketPolicy', 's3:PutBucketVersioning', 's3:PutBucketObjectLockConfiguration',
            's3:PutLifecycleConfiguration', 's3:PutBucketAcl', 's3:PutBucketPublicAccessBlock',
            's3:PutEncryptionConfiguration'], 'Resource': arn}
    ]}
    read = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:ListBucket', 's3:ListBucketVersions'], 'Resource': arn,
         'Condition': {'StringLike': {'s3:prefix': [prefix, prefix + '/*']}}},
        {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:GetObjectVersion', 's3:GetObjectRetention'], 'Resource': objs},
        {'Effect': 'Deny', 'Action': ['s3:PutObject', 's3:DeleteObject', 's3:DeleteObjectVersion',
                                    's3:PutObjectRetention', 's3:PutObjectLegalHold', 's3:BypassGovernanceRetention'], 'Resource': arn + '/*'}
    ]}
    tls = {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'DenyNonTLS', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
         'Resource': [arn, arn + '/*'], 'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}
    ]}
    metrics = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': 'cloudwatch:PutMetricData', 'Resource': '*',
         'Condition': {'StringEquals': {'cloudwatch:namespace': 'SafeVault'}}}
    ]}
    lifecycle = {'Rules': [{'ID': 'ArchiveStandardIA30Days', 'Status': 'Enabled',
                           'Filter': {'And': {'Prefix': prefix + '/', 'ObjectSizeGreaterThan': 131071}},
                           'Transitions': [{'Days': 30, 'StorageClass': 'STANDARD_IA'}],
                           'NoncurrentVersionTransitions': [{'NoncurrentDays': 30, 'StorageClass': 'STANDARD_IA'}]}]}
    for name, data in [('uploader.json', uploader), ('restore.json', read), ('bucket-tls.json', tls),
                       ('optional-metrics.json', metrics), ('lifecycle.json', lifecycle)]:
        save_json(out / name, data)
    print('Generated reviewable AWS configuration; NOTHING deployed: ' + str(out))

def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('demo', help='Create a NEW synthetic offline lab, corrupt demo files, restore clean version')
    p.add_argument('--directory', required=True)
    p = sub.add_parser('init', help='Create private config; never asks for secrets')
    p.add_argument('--config', required=True); p.add_argument('--backend', choices=['local', 'aws'], required=True)
    p.add_argument('--source', required=True); p.add_argument('--state', required=True)
    p.add_argument('--prefix', default='backups/source-01'); p.add_argument('--local-store')
    p.add_argument('--bucket'); p.add_argument('--region', default='ap-south-1'); p.add_argument('--profile'); p.add_argument('--owner')
    for action in ('backup', 'list', 'restore', 'audit'):
        p = sub.add_parser(action); p.add_argument('--config', required=True)
        if action in ('restore', 'audit'):
            p.add_argument('--record-key', required=True); p.add_argument('--record-version', required=True)
        if action == 'restore':
            p.add_argument('--destination', required=True); p.add_argument('--file', help='Relative file within source; omit for full restore')
    p = sub.add_parser('policies', help='Generate policies/lifecycle for your bucket; does not deploy')
    p.add_argument('--bucket', required=True); p.add_argument('--prefix', default='backups/source-01'); p.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo': demo(args.directory)
        elif args.command == 'init':
            if args.backend == 'aws' and not args.bucket: raise VaultError('--bucket required for AWS')
            if args.backend == 'local' and not args.local_store: raise VaultError('--local-store required for local')
            init_config(args)
        elif args.command == 'policies': write_policies(args.bucket, args.prefix, args.output)
        else:
            c = config(args.config)
            if args.command == 'backup': print(json.dumps(backup(c), indent=2))
            elif args.command == 'audit': audit_retention(c, args.record_key, args.record_version)
            elif args.command == 'list':
                print(json.dumps({'notice': 'Listings indicate uploaded records, not clean/recoverable guarantees. Select an explicitly trusted version.',
                                  'records': store(c).list_records()}, indent=2))
            else: restore(c, args.record_key, args.record_version, args.destination, args.file)
        return 0
    except (VaultError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as e:
        print('SafeVault error: ' + str(e), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
