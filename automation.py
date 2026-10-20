#!/usr/bin/env python3
"""SafeVault automation layer (v2).

Adds four things on top of the unchanged backup engine (safevault.py):

1. run-backup        : one backup attempt that is safe to run from cron. It records start,
                       finish, outcome and errors in a private run log, refuses overlapping
                       runs, then publishes sanitized results to the dashboard.
2. cloud-restore     : downloads the selected clean backup FROM S3 (explicit VersionIds)
                       using a separate read-only identity, restores it into a NEW folder and
                       compares every file with the independent clean SHA-256 baseline.
3. ransomware-drill  : a safe simulation (no malware). A disposable copy of the synthetic
                       data is "encrypted", the uploader key tries to read/delete backups
                       (expected AccessDenied), and the clean cloud backup is recovered.
4. publish           : builds dashboard-data.json from REAL execution records only and
                       uploads it with an identity that can write that one object only.

Nothing here creates AWS resources or embeds credentials. AWS profiles are configured by
the operator with `aws configure --profile ...`.
"""
import contextlib
import datetime as dt
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

import lite
import safevault as sv

DASHBOARD_BUCKET = 'safevault-dashboard-8b4c47'
DASHBOARD_KEY = 'dashboard-data.json'
DASHBOARD_URL = 'https://safevault-dashboard-8b4c47.s3.ap-south-1.amazonaws.com/dashboard-data.json'
# ONE extra IAM user keeps setup short: it can READ backup versions (never write/delete them)
# and WRITE only the single public dashboard-data.json object. It is separate from the uploader.
RESTORE_PROFILE = 'safevault-lite-recovery'
PUBLISH_PROFILE = 'safevault-lite-recovery'
CRON_MARK = '# SAFEVAULT_LITE_SCHEDULE'
SHOWN_RUNS = 12
DASHBOARD_VERSION = 2


# ---------------------------------------------------------------- small helpers
def state():
    return lite.STATE


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, 'a') as f:
        f.write(json.dumps(record, sort_keys=True) + '\n')


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    items = []
    for line in path.read_text().splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue  # a half-written line never stops the dashboard
        if isinstance(value, dict):
            items.append(value)
    return items


def scrub(message):
    """Make an error safe for a PUBLIC page: no ARNs, account IDs, keys, bucket names or home paths."""
    s = str(message)
    s = re.sub(r'arn:aws[\w:/+=,.@-]*', '[arn]', s)
    s = re.sub(r'\b(AKIA|ASIA)[A-Z0-9]{12,}\b', '[key]', s)
    s = re.sub(r'\b\d{12}\b', '[account]', s)
    for name in (lite.BUCKET, DASHBOARD_BUCKET):
        s = s.replace(name, '[bucket]')
    s = s.replace(str(lite.HOME), '~')
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:220]


def expected_identity(cloud, allowed_users=(), allowed_roles=()):
    arn = cloud._run(['sts', 'get-caller-identity'])['Arn']
    if ':user/' in arn and arn.split('/')[-1] in allowed_users:
        return arn
    if any(':assumed-role/' + r + '/' in arn for r in allowed_roles):
        return arn
    raise lite.LabError('Wrong AWS identity for this step. Expected ' + ' or '.join(list(allowed_users) + list(allowed_roles)) + '. Never use an administrator here.')


def restore_config():
    owner = sv.config(lite.CFG)['expected_bucket_owner']
    return sv.validate_config({
        'backend': 'aws', 'bucket': lite.BUCKET, 'region': lite.REGION, 'profile': RESTORE_PROFILE,
        'expected_bucket_owner': owner, 'prefix': lite.PREFIX, 'source_id': 'source-01',
        # The real source is passed only so the engine REFUSES to restore over it.
        'source_directory': str(lite.LAB / 'restored-good'),
        'state_directory': str(state() / 'restore'), 'monitoring': False})


def restore_identity(cloud):
    return expected_identity(cloud, allowed_users=(RESTORE_PROFILE,), allowed_roles=('SafeVaultLiteRestoreRole',))


def csv_usable(folder):
    path = Path(folder) / 'records' / 'attendance.csv'
    try:
        import csv
        with path.open(newline='') as f:
            reader = csv.DictReader(f)
            return reader.fieldnames == ['student_id', 'present'] and list(reader) == [
                {'student_id': 'DEMO001', 'present': 'yes'}, {'student_id': 'DEMO002', 'present': 'no'}]
    except (OSError, UnicodeDecodeError):
        return False


def compare(baseline, restored):
    missing = sorted(set(baseline) - set(restored))
    unexpected = sorted(set(restored) - set(baseline))
    changed = sorted(k for k in baseline if k in restored and restored[k] != baseline[k])
    return missing, changed, unexpected


def say(title):
    print('\n== ' + title + ' ' + '=' * max(3, 60 - len(title)))


# ---------------------------------------------------------------- A. scheduled backup
def run_backup(args):
    folder = state()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = lite.stamp()
    t0 = time.monotonic()
    record = {'started_utc': started, 'trigger': args.trigger}
    failure = None
    with open(folder / 'run-backup.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record.update(outcome='skipped-overlap', finished_utc=lite.stamp(), duration_seconds=0.0,
                          error='Previous backup still running; this run was skipped.')
            append_jsonl(folder / 'runs.jsonl', record)
            print(started + ' SKIPPED: previous backup still running (no overlap allowed).')
            return 0 if args.no_publish else (0 if publish_dashboard(quiet=True) else 2)
        try:
            with lite.credentials_from_profile_or_role():
                c = lite.source()
                if not args.monitoring:
                    c['monitoring'] = False  # CloudWatch metrics are optional and not part of this build
                receipt = lite.perform_backup(c)
            clean = lite.hashes(c['source_directory']) == lite.read(lite.LAB / 'known-good-hashes.json')
            selected = clean and (args.select_clean or not (folder / 'clean-receipt.json').exists())
            if selected:
                lite.save(folder / 'clean-receipt.json', receipt)
            record.update(outcome='success', run_id=receipt['run_id'], archive_bytes=receipt['archive_bytes'],
                          file_count=receipt['file_count'], source_bytes=receipt['source_bytes'],
                          data_cutoff_utc=receipt['data_cutoff_utc'], matches_clean_baseline=clean,
                          selected_as_clean=selected)
        except Exception as e:  # recorded honestly, then re-raised below
            failure = e
            record.update(outcome='failed', error=scrub(e))
        finally:
            record['finished_utc'] = lite.stamp()
            record['duration_seconds'] = round(time.monotonic() - t0, 3)
            append_jsonl(folder / 'runs.jsonl', record)
    if failure is None:
        print(record['finished_utc'] + ' BACKUP SUCCEEDED (' + args.trigger + '): run ' + record['run_id'] +
              ', ' + str(record['file_count']) + ' files, ' + str(record['archive_bytes']) + ' bytes, ' +
              str(record['duration_seconds']) + ' s' + (' · selected as clean recovery point' if record['selected_as_clean'] else ''))
        if args.select_clean and not record['matches_clean_baseline']:
            print('WARNING: source differs from the clean baseline, so it was NOT selected as the clean recovery point.')
    else:
        print(record['finished_utc'] + ' BACKUP FAILED (' + args.trigger + '): ' + record['error'], file=sys.stderr)
    published = True if args.no_publish else publish_dashboard(quiet=True)
    if failure is not None:
        raise lite.LabError('Backup failed and was recorded as failed: ' + record['error'])
    return 0 if published else 2


# ---------------------------------------------------------------- B. real cloud recovery
def do_cloud_restore(context):
    folder = state()
    if not (folder / 'clean-receipt.json').exists():
        raise lite.LabError('No selected clean backup yet. Run: python3 lite.py run-backup')
    receipt = lite.read(folder / 'clean-receipt.json')
    baseline = lite.read(lite.LAB / 'known-good-hashes.json')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = lite.HOME / 'safevault-lite-recovery' / ('cloud-restore-' + stamp + '-' + uuid.uuid4().hex[:6])
    record = {'attempted_utc': lite.stamp(), 'context': context, 'backup_run_id': receipt['run_id'],
              'backup_cutoff_utc': receipt['data_cutoff_utc'], 'files_expected': len(baseline),
              'downloaded_from': 'Amazon S3, explicitly selected object versions'}
    t0 = time.monotonic()
    try:
        c = restore_config()
        with lite.credentials_from_profile_or_role():
            cloud = sv.AWSStore(c)
            restore_identity(cloud)
            with contextlib.redirect_stdout(io.StringIO()):
                result = sv.restore(c, receipt['record_key'], receipt['record_version_id'], destination, cloud=cloud)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    audit = sv.audit_retention(c, receipt['record_key'], receipt['record_version_id'], cloud=cloud)
                record['retention'] = [{'object': 'completion record' if x['key'].endswith('complete.json') else 'archive',
                                        'mode': x['mode'], 'retain_until': x['retain_until']} for x in audit['checks']]
                record['retention_ok'] = True
            except sv.VaultError as e:
                record['retention_ok'] = False
                record['retention_error'] = scrub(e)
        restored = lite.hashes(destination)
        missing, changed, unexpected = compare(baseline, restored)
        usable = csv_usable(destination)
        record.update(destination=str(destination), restore_seconds=round(result['restore_seconds'], 3),
                      files_restored=len(restored), files_matched=len(baseline) - len(missing) - len(changed),
                      missing=missing, changed=changed, unexpected=unexpected, csv_usable=usable)
        ok = not (missing or changed or unexpected) and usable
        record['outcome'] = 'passed' if ok else 'failed'
        if not ok:
            record['error'] = 'Restored files differ from the independent clean baseline.'
    except Exception as e:
        record.update(outcome='failed', error=scrub(e))
    record['total_seconds'] = round(time.monotonic() - t0, 3)
    append_jsonl(folder / 'recoveries.jsonl', record)
    lite.save(lite.HOME / 'safevault-lite-recovery' / ('report-' + stamp + '-' + uuid.uuid4().hex[:6] + '.json'), record)
    return record


def print_recovery(r):
    if r['outcome'] == 'passed':
        print('RECOVERY PASSED from S3: ' + str(r['files_matched']) + '/' + str(r['files_expected']) +
              ' files match the independent clean SHA-256 baseline; attendance.csv usable.')
        print('Download + validate + extract time: ' + str(r['restore_seconds']) + ' s')
        print('Restored into NEW folder: ' + r['destination'])
    else:
        print('RECOVERY FAILED: ' + r.get('error', 'unknown error'), file=sys.stderr)
        for k in ('missing', 'changed', 'unexpected'):
            if r.get(k):
                print('  ' + k + ': ' + ', '.join(r[k]), file=sys.stderr)
    if r.get('retention_ok'):
        for x in r['retention']:
            print('Object Lock on ' + x['object'] + ' version: ' + x['mode'] + ' until ' + x['retain_until'])
    elif 'retention_ok' in r:
        print('Retention NOT verified: ' + r.get('retention_error', ''))


def cloud_restore(args):
    say('Restore the selected clean backup from Amazon S3')
    r = do_cloud_restore('cloud-restore')
    print_recovery(r)
    published = args.no_publish or publish_dashboard(quiet=True)
    if r['outcome'] != 'passed':
        raise lite.LabError('Cloud recovery failed; the failure was recorded and published as failed.')
    return 0 if published else 2


# ---------------------------------------------------------------- C. verify protection settings
def verify_protection(args):
    say('Check the real protection settings of the backup bucket')
    c = restore_config()
    result = {'checked_utc': lite.stamp(), 'errors': []}
    with lite.credentials_from_profile_or_role():
        cloud = sv.AWSStore(c)
        restore_identity(cloud)
        b = ['--bucket', lite.BUCKET] + cloud._owner()

        def ask(name, command):
            try:
                return cloud._run(command + b)
            except sv.VaultError as e:
                result['errors'].append(name + ': ' + scrub(e))
                return None
        v = ask('versioning', ['s3api', 'get-bucket-versioning'])
        result['versioning'] = (v or {}).get('Status', 'Not enabled') if v is not None else 'Unknown'
        lock = ask('object lock', ['s3api', 'get-object-lock-configuration'])
        if lock is not None:
            conf = lock.get('ObjectLockConfiguration', {})
            result['object_lock'] = conf.get('ObjectLockEnabled', 'Not enabled')
            rule = conf.get('Rule', {}).get('DefaultRetention', {})
            if rule:
                result['default_retention'] = rule.get('Mode', '?') + ' ' + str(rule.get('Days') or (str(rule.get('Years')) + ' years')) + (' days' if rule.get('Days') else '')
            else:
                result['default_retention'] = 'None'
        else:
            result['object_lock'] = 'Unknown'
            result['default_retention'] = 'Unknown'
        enc = ask('encryption', ['s3api', 'get-bucket-encryption'])
        if enc is not None:
            rules = enc.get('ServerSideEncryptionConfiguration', {}).get('Rules', [])
            algo = rules[0].get('ApplyServerSideEncryptionByDefault', {}).get('SSEAlgorithm') if rules else None
            result['encryption'] = {'AES256': 'SSE-S3 (AES-256)', 'aws:kms': 'SSE-KMS'}.get(algo, algo or 'None')
        else:
            result['encryption'] = 'Unknown'
        pab = ask('public access block', ['s3api', 'get-public-access-block'])
        if pab is not None:
            flags = pab.get('PublicAccessBlockConfiguration', {})
            result['public_access_blocked'] = all(flags.get(k) is True for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'))
        else:
            result['public_access_blocked'] = None
        if (state() / 'clean-receipt.json').exists():
            receipt = lite.read(state() / 'clean-receipt.json')
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    audit = sv.audit_retention(c, receipt['record_key'], receipt['record_version_id'], cloud=cloud)
                result['clean_backup_retention'] = [{'object': 'completion record' if x['key'].endswith('complete.json') else 'archive',
                                                     'mode': x['mode'], 'retain_until': x['retain_until']} for x in audit['checks']]
            except sv.VaultError as e:
                result['errors'].append('clean backup retention: ' + scrub(e))
    result['all_ok'] = (result['versioning'] == 'Enabled' and result['object_lock'] == 'Enabled'
                        and result['default_retention'].startswith(('GOVERNANCE', 'COMPLIANCE'))
                        and result['encryption'] != 'None' and result['public_access_blocked'] is True
                        and bool(result.get('clean_backup_retention')) and not result['errors'])
    lite.save(state() / 'protection.json', result)
    for label, key in [('S3 Versioning', 'versioning'), ('Object Lock', 'object_lock'), ('Default retention', 'default_retention'),
                       ('Default encryption', 'encryption'), ('All public access blocked', 'public_access_blocked')]:
        print('{:<28}{}'.format(label, result.get(key)))
    for x in result.get('clean_backup_retention', []):
        print('{:<28}{} until {}'.format('Clean ' + x['object'], x['mode'], x['retain_until']))
    for e in result['errors']:
        print('COULD NOT CHECK ' + e, file=sys.stderr)
    print('PROTECTION VERIFIED' if result['all_ok'] else 'PROTECTION NOT FULLY VERIFIED (see lines above)')
    published = args.no_publish or publish_dashboard(quiet=True)
    return (0 if published else 2) if result['all_ok'] else 1


# ---------------------------------------------------------------- D. safe ransomware drill
def ransomware_drill(args):
    folder = state()
    if not (folder / 'clean-receipt.json').exists():
        raise lite.LabError('Make the clean cloud backup first: python3 lite.py run-backup')
    baseline = lite.read(lite.LAB / 'known-good-hashes.json')
    if lite.hashes(lite.LAB / 'restored-good') != baseline:
        raise lite.LabError('Synthetic source differs from the clean baseline; nothing was changed.')
    if not args.yes:
        lite.confirm('SAFE DRILL: copy the synthetic files into a NEW disposable folder and scramble ONLY that copy. '
                     'No malware. Your real source folder and your cloud backups are not modified.', 'DRILL')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    victim = lite.LAB / ('drill-' + stamp) / 'department-share'
    drill = {'started_utc': lite.stamp()}

    say('Step 1/4  Disposable copy of the department share')
    shutil.copytree(lite.LAB / 'restored-good', victim)
    print('Created ' + str(victim) + ' with ' + str(len(baseline)) + ' synthetic files (all hashes match baseline).')

    say('Step 2/4  Simulated ransomware encrypts the copy')
    key = os.urandom(32)
    files = sorted(p for p in victim.rglob('*') if p.is_file())
    for p in files:
        data = p.read_bytes()
        scrambled = bytes(b ^ key[i % 32] for i, b in enumerate(data))
        p.with_name(p.name + '.locked').write_bytes(b'SIMULATED-RANSOMWARE-NO-MALWARE\n' + scrambled)
        p.unlink()  # only inside the disposable drill copy
    (victim / 'READ_ME_SIMULATED_RANSOM_NOTE.txt').write_text(
        'This is a SAFE classroom simulation for SafeVault. No real malware was used.\n')
    readable = sum(1 for rel, h in baseline.items() if (victim / rel).is_file() and sv.digest(victim / rel) == h)
    drill['encrypted_utc'] = lite.stamp()
    drill['files_encrypted'] = len(files)
    drill['readable_after_attack'] = readable
    print(str(len(files)) + ' files replaced by .locked copies. Readable originals left: ' + str(readable) + '/' + str(len(baseline)))
    print('attendance.csv now: ' + str((victim / 'records/attendance.csv.locked').read_bytes()[:40]) + ' ...')

    say('Step 3/4  Attacker uses the stolen laptop key against the backups')
    if args.skip_attack:
        print('Skipped (--skip-attack). Run python3 lite.py iam-test separately for this evidence.')
        drill['attacker_attempts'] = None
    else:
        receipt = lite.read(folder / 'clean-receipt.json')
        attempts = {}
        with lite.credentials_from_profile_or_role():
            c = lite.source()
            cloud = sv.AWSStore(c)
            canary_file = folder / 'drill-canary.txt'
            canary_file.write_text('Disposable SafeVault drill canary.\n')
            canary_key = lite.PREFIX + '/drill-canary/' + uuid.uuid4().hex + '/canary.txt'
            try:
                canary_version = cloud.put(canary_key, canary_file)['VersionId']
            finally:
                canary_file.unlink(missing_ok=True)
            plan = [
                ('read the clean backup', ['s3api', 'get-object', '--bucket', lite.BUCKET, '--key', receipt['archive_key'], '--version-id', receipt['archive_version_id'], str(folder / 'drill-should-not-download.bin')]),
                ('list backup versions', ['s3api', 'list-object-versions', '--bucket', lite.BUCKET, '--prefix', lite.PREFIX + '/']),
                ('permanently delete a backup version', ['s3api', 'delete-object', '--bucket', lite.BUCKET, '--key', canary_key, '--version-id', canary_version]),
                ('shorten Object Lock retention', ['s3api', 'put-object-retention', '--bucket', lite.BUCKET, '--key', canary_key, '--version-id', canary_version,
                                                  '--retention', json.dumps({'Mode': 'GOVERNANCE', 'RetainUntilDate': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}),
                                                  '--bypass-governance-retention']),
            ]
            for label, command in plan:
                try:
                    lite.expected_denial(cloud, (command[:-1] + cloud._owner() + command[-1:]) if command[1] == 'get-object' else command + cloud._owner())
                    attempts[label] = 'denied'
                    print('  Try to ' + label + ' ... AccessDenied  (blocked)')
                except lite.LabError as e:
                    attempts[label] = 'NOT DENIED' if 'UNEXPECTED' in str(e) else 'error'
                    print('  Try to ' + label + ' ... ' + attempts[label] + ': ' + scrub(e), file=sys.stderr)
        drill['attacker_attempts'] = attempts
        drill['attacker_blocked'] = all(v == 'denied' for v in attempts.values())
        print('The delete test used a disposable canary object, never the real clean backup.')

    say('Step 4/4  Recover the clean backup from S3 and verify')
    detected = time.monotonic()
    r = do_cloud_restore('ransomware-drill')
    print_recovery(r)
    drill.update(recovered=r['outcome'] == 'passed', files_matched=r.get('files_matched', 0),
                 files_expected=r['files_expected'], restore_seconds=r.get('restore_seconds'),
                 time_to_recover_seconds=round(time.monotonic() - detected, 3),
                 backup_cutoff_utc=r['backup_cutoff_utc'], finished_utc=lite.stamp(),
                 disposable_folder=str(victim.parent))
    drill['outcome'] = 'passed' if drill['recovered'] and drill.get('attacker_blocked', True) is not False else 'failed'
    lite.save(folder / 'drill.json', drill)
    append_jsonl(folder / 'drills.jsonl', drill)
    print('\nDRILL ' + drill['outcome'].upper() + ': ' + str(drill['readable_after_attack']) + '/' + str(len(baseline)) +
          ' files readable after attack -> ' + str(drill['files_matched']) + '/' + str(len(baseline)) + ' verified after recovery.')
    print('The scrambled disposable copy is kept at ' + str(victim.parent) + ' (delete it yourself later if you want).')
    published = args.no_publish or publish_dashboard(quiet=True)
    if drill['outcome'] != 'passed':
        raise lite.LabError('Drill did not fully pass; it was recorded as failed.')
    return 0 if published else 2


# ---------------------------------------------------------------- E. dashboard data + publishing
def _retention_public(items):
    return [{'object': x['object'], 'mode': x['mode'], 'retain_until': x['retain_until']} for x in (items or [])]


def build_dashboard_data():
    folder = state()
    data = lite.sanitized_evidence([])  # keeps the existing schema-1 fields the current page already reads
    runs = read_jsonl(folder / 'runs.jsonl')
    recoveries = read_jsonl(folder / 'recoveries.jsonl')
    clean = lite.read(folder / 'clean-receipt.json') if (folder / 'clean-receipt.json').exists() else None

    # Backup history
    successes = [r for r in runs if r.get('outcome') == 'success']
    last = runs[-1] if runs else None
    last_ok = successes[-1] if successes else None
    data['backup'] = {
        'last_attempt_utc': last and last.get('finished_utc'),
        'last_attempt_outcome': last and last.get('outcome'),
        'last_attempt_trigger': last and last.get('trigger'),
        'last_attempt_error': last and last.get('error'),
        'last_success_utc': last_ok and last_ok.get('finished_utc'),
        'last_success_archive_bytes': last_ok and last_ok.get('archive_bytes'),
        'last_success_file_count': last_ok and last_ok.get('file_count'),
        'last_success_trigger': last_ok and last_ok.get('trigger'),
        'total_attempts': len(runs),
        'total_successes': len(successes),
        'total_failures': sum(1 for r in runs if r.get('outcome') == 'failed'),
        'scheduled_successes': sum(1 for r in successes if r.get('trigger') == 'scheduled'),
        'clean_recovery_point_utc': clean and clean.get('data_cutoff_utc'),
    }
    data['runs'] = [{k: r.get(k) for k in ('started_utc', 'finished_utc', 'trigger', 'outcome', 'archive_bytes',
                                           'file_count', 'duration_seconds', 'matches_clean_baseline', 'selected_as_clean', 'error')}
                    for r in reversed(runs[-SHOWN_RUNS:])]

    # Upload table (existing page): selected clean point first, then recent successful runs
    uploads = []
    if clean:
        uploads.append({'data_cutoff_utc': clean['data_cutoff_utc'], 'archive_bytes': clean['archive_bytes'],
                        'file_count': clean['file_count'], 'type': 'Clean'})
    for r in reversed(successes):
        if clean and r.get('run_id') == clean.get('run_id'):
            continue
        if len(uploads) >= 10:
            break
        uploads.append({'data_cutoff_utc': r['data_cutoff_utc'], 'archive_bytes': r['archive_bytes'], 'file_count': r['file_count'],
                        'type': ('Scheduled' if r.get('trigger') == 'scheduled' else 'Manual') + ('' if r.get('matches_clean_baseline') else ' · changed')})
    data['uploads'] = uploads

    # Recovery: passing full trials (existing schema) + latest attempt including failures
    passing = [r for r in recoveries if r.get('outcome') == 'passed']
    data['recoveries'] = [{'verified_utc': r['attempted_utc'], 'files': r['files_matched'], 'restore_seconds': r['restore_seconds'],
                           'baseline_matched': True, 'csv_usable': True, 'context': r.get('context')} for r in passing[-10:]]
    latest = recoveries[-1] if recoveries else None
    if latest:
        data['recovery'] = {'status': latest['outcome'], 'attempted_utc': latest['attempted_utc'], 'context': latest.get('context'),
                            'source': latest.get('downloaded_from'), 'backup_cutoff_utc': latest.get('backup_cutoff_utc'),
                            'files_expected': latest.get('files_expected'), 'files_matched': latest.get('files_matched'),
                            'missing': len(latest.get('missing') or []), 'changed': len(latest.get('changed') or []),
                            'unexpected': len(latest.get('unexpected') or []), 'restore_seconds': latest.get('restore_seconds'),
                            'retention': _retention_public(latest.get('retention')), 'error': latest.get('error')}
    else:
        data['recovery'] = {'status': 'not-tested'}

    # Protection settings (read with the read-only restore identity)
    if (folder / 'protection.json').exists():
        p = lite.read(folder / 'protection.json')
        data['protection'] = {k: p.get(k) for k in ('checked_utc', 'versioning', 'object_lock', 'default_retention',
                                                     'encryption', 'public_access_blocked', 'all_ok')}
        data['protection']['clean_backup_retention'] = _retention_public(p.get('clean_backup_retention'))
        data['protection']['errors'] = len(p.get('errors') or [])
    else:
        data['protection'] = None

    # Drill
    if (folder / 'drill.json').exists():
        d = lite.read(folder / 'drill.json')
        data['drill'] = {k: d.get(k) for k in ('started_utc', 'finished_utc', 'outcome', 'files_encrypted', 'readable_after_attack',
                                                'attacker_attempts', 'attacker_blocked', 'recovered', 'files_matched', 'files_expected',
                                                'restore_seconds', 'time_to_recover_seconds', 'backup_cutoff_utc')}
    else:
        data['drill'] = None

    # Checklist (each item true only from a real record)
    checks = data['checks']
    checks['scheduled_runs'] = data['backup']['scheduled_successes'] >= 2
    checks['cloud_recovery'] = bool(passing)
    checks['retention_metadata'] = bool(latest and latest.get('retention_ok')) or bool(data['protection'] and data['protection']['clean_backup_retention'])
    checks['protection_verified'] = bool(data['protection'] and data['protection'].get('all_ok'))
    checks['drill_passed'] = bool(data['drill'] and data['drill'].get('outcome') == 'passed')
    checks['attacker_blocked'] = bool(data['drill'] and data['drill'].get('attacker_blocked'))
    if checks['attacker_blocked']:
        checks['iam_denials'] = True

    pub = lite.read(folder / 'publication.json') if (folder / 'publication.json').exists() else {}
    data['publication'] = {'previous_failure_utc': pub.get('last_failure_utc'), 'previous_failure': pub.get('last_failure')}
    data['kind'] = 'results' if (runs or recoveries or data['uploads']) else 'empty'
    data['data_mode'] = 'measured'
    data['dashboard_version'] = DASHBOARD_VERSION
    data['source_note'] = 'Generated automatically by SafeVault from real execution records on the operator laptop (Ubuntu/WSL).'
    data['generated_utc'] = lite.stamp()
    return data


FORBIDDEN = [r'"VersionId"', r'version_id', r'record_key', r'archive_key', r'\b(AKIA|ASIA)[A-Z0-9]{12,}', r'aws_secret', r'\b\d{12}\b', r'arn:aws']


def assert_public_safe(text):
    for pattern in FORBIDDEN + [re.escape(lite.BUCKET), re.escape(str(lite.HOME))]:
        if re.search(pattern, text):
            raise lite.LabError('Refusing to publish: dashboard data would contain a private identifier. Nothing was uploaded.')
    if len(text) > 900_000:
        raise lite.LabError('Dashboard data too large.')


def publish_dashboard(quiet=False):
    """Returns True when the new JSON is uploaded. Failures are recorded separately from backup failures."""
    folder = state()
    record = lite.read(folder / 'publication.json') if (folder / 'publication.json').exists() else {}
    record['last_attempt_utc'] = lite.stamp()
    try:
        data = build_dashboard_data()
        text = json.dumps(data, indent=1, sort_keys=True)
        assert_public_safe(text)
        out = lite.RESULTS / 'dashboard-data.json'
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        out.write_text(text + '\n')
        c = {'region': lite.REGION, 'profile': PUBLISH_PROFILE}
        with lite.credentials_from_profile_or_role():
            cloud = sv.AWSStore(c)
            expected_identity(cloud, allowed_users=(PUBLISH_PROFILE,))
            cloud._run(['s3api', 'put-object', '--bucket', DASHBOARD_BUCKET, '--key', DASHBOARD_KEY, '--body', str(out),
                        '--content-type', 'application/json; charset=utf-8', '--cache-control', 'no-cache, no-store, max-age=0'])
        record.update(ok=True, last_success_utc=lite.stamp(), generated_utc=data['generated_utc'])
        record.pop('last_failure', None)
        record.pop('last_failure_utc', None)
        public = public_check(data['generated_utc'])
        record['public_read_verified'] = public
        lite.save(folder / 'publication.json', record)
        print('DASHBOARD UPDATED: dashboard-data.json published' + (' and publicly readable.' if public else
              '. WARNING: public read-back check did not match (see note above).'))
        return True
    except Exception as e:
        record.update(ok=False, last_failure_utc=lite.stamp(), last_failure=scrub(e))
        lite.save(folder / 'publication.json', record)
        print('DASHBOARD PUBLISH FAILED (backup/restore result is unaffected and still recorded locally): ' + scrub(e), file=sys.stderr)
        return False


def public_check(generated):
    if os.environ.get('SAFEVAULT_SKIP_PUBLIC_CHECK') == '1':
        return None
    url = os.environ.get('SAFEVAULT_DASHBOARD_URL', DASHBOARD_URL)
    try:
        request = urllib.request.Request(url + ('' if url.startswith('file:') else '?check=' + uuid.uuid4().hex[:8]), headers={'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.loads(response.read(1_000_000))
        if value.get('generated_utc') == generated:
            return True
        print('Note: public URL returned older data (generated ' + str(value.get('generated_utc')) + ').')
        return False
    except Exception as e:
        print('Note: could not read the public dashboard URL anonymously: ' + scrub(e) +
              '. If this says 403, the object is not publicly readable (see SETUP step 4).')
        return False


def publish(args):
    return 0 if publish_dashboard() else 2


# ---------------------------------------------------------------- F. scheduling (cron on WSL)
def cron_line(minutes):
    if minutes == 60:
        spec = '0 * * * *'
    elif 1 <= minutes <= 59 and 60 % minutes == 0:
        spec = '*/' + str(minutes) + ' * * * *'
    else:
        raise lite.LabError('Use --every with 1, 2, 3, 5, 10, 15, 20, 30 or 60 minutes.')
    for p in (lite.ROOT, state(), Path(sys.executable)):
        if any(x in str(p) for x in ['%', '\n', '\r']):
            raise lite.LabError('Unsafe path for cron.')
    cmd = ' '.join(shlex.quote(x) for x in [sys.executable, str(lite.ROOT / 'lite.py'), 'run-backup', '--trigger', 'scheduled'])
    log = shlex.quote(str(state() / 'scheduled.log'))
    return spec + ' PATH=/usr/local/bin:/usr/bin:/bin ' + cmd + ' >> ' + log + ' 2>&1 ' + CRON_MARK


def current_crontab():
    if not shutil.which('crontab'):
        raise lite.LabError('cron is not installed. Run: sudo apt-get update && sudo apt-get install -y cron')
    p = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if p.returncode != 0 and 'no crontab' not in p.stderr.lower():
        raise lite.LabError('Could not read crontab: ' + p.stderr.strip())
    return p.stdout if p.returncode == 0 else ''


def write_crontab(text):
    p = subprocess.run(['crontab', '-'], input=text, capture_output=True, text=True)
    if p.returncode != 0:
        raise lite.LabError('Could not write crontab: ' + p.stderr.strip())


def cron_running():
    try:
        return subprocess.run(['pgrep', '-x', 'cron'], capture_output=True).returncode == 0
    except FileNotFoundError:
        return None


def schedule(args):
    if args.remove:
        old = current_crontab()
        kept = [l for l in old.splitlines() if CRON_MARK not in l and 'SAFEVAULT_LITE_HOURLY' not in l]
        if len(kept) == len(old.splitlines()):
            print('No SafeVault schedule line found. Nothing changed.')
            return 0
        if not args.yes:
            lite.confirm('Remove ONLY the SafeVault backup line(s) from your crontab? Other jobs stay.', 'REMOVE')
        write_crontab('\n'.join(kept) + ('\n' if kept else ''))
        print('SafeVault schedule removed. Other cron jobs were kept: ' + str(len(kept)))
        return 0
    line = cron_line(args.every)
    if not args.install:
        print(line)
        print('\nThis only PRINTS the line. Add --install to install it.')
        return 0
    old = current_crontab()
    kept = [l for l in old.splitlines() if CRON_MARK not in l and 'SAFEVAULT_LITE_HOURLY' not in l]
    if not args.yes:
        lite.confirm('Install this SafeVault line (replacing any older SafeVault line, keeping all other jobs)?\n' + line, 'SCHEDULE')
    write_crontab('\n'.join(kept + [line]) + '\n')
    print('INSTALLED: backups every ' + str(args.every) + ' minute(s). Log: ' + str(state() / 'scheduled.log'))
    running = cron_running()
    if running is False:
        print('cron is NOT running yet. Start it with: sudo service cron start')
    print('Reminder: cron runs only while Windows is awake and the Ubuntu (WSL) window stays open.')
    return 0


# ---------------------------------------------------------------- G. status
def status(args):
    runs = read_jsonl(state() / 'runs.jsonl')
    say('Backup runs (newest first)')
    if not runs:
        print('No runs recorded yet.')
    for r in reversed(runs[-10:]):
        print('{:<33} {:<9} {:<16} {}'.format(r.get('started_utc', '?')[:32], r.get('trigger', '?'), r.get('outcome', '?'),
              (str(r.get('archive_bytes')) + ' B' if r.get('outcome') == 'success' else r.get('error', ''))[:70]))
    scheduled = sum(1 for r in runs if r.get('trigger') == 'scheduled' and r.get('outcome') == 'success')
    print('Unattended (scheduled) successful runs: ' + str(scheduled))
    rec = read_jsonl(state() / 'recoveries.jsonl')
    say('Recovery')
    print(('Latest: ' + rec[-1]['outcome'] + ' at ' + rec[-1]['attempted_utc'] + ' (' + rec[-1].get('context', '') + ')') if rec else 'Not tested yet.')
    say('Protection')
    if (state() / 'protection.json').exists():
        p = lite.read(state() / 'protection.json')
        print('Checked ' + p['checked_utc'] + ': ' + ('all OK' if p.get('all_ok') else 'NOT fully verified'))
    else:
        print('Not checked yet. Run: python3 lite.py verify-protection')
    say('Dashboard publication')
    if (state() / 'publication.json').exists():
        p = lite.read(state() / 'publication.json')
        print('Last attempt ' + str(p.get('last_attempt_utc')) + ': ' + ('OK' if p.get('ok') else 'FAILED - ' + str(p.get('last_failure'))))
    else:
        print('Never published.')
    try:
        installed = any(CRON_MARK in l for l in current_crontab().splitlines())
    except lite.LabError:
        installed = False
    say('Schedule')
    print('SafeVault cron line installed: ' + str(installed) + ' · cron process running: ' + str(cron_running()))
    say('File categories (latest scan)')
    if (state() / 'categories.json').exists():
        cat = lite.read(state() / 'categories.json')
        print(', '.join(x['label'] + ': ' + str(x['files']) for x in cat['summary']) + ' · warnings: ' + str(cat['warnings']))
    else:
        print('Not scanned yet. Run: python3 lite.py categories')
    fetches = read_jsonl(state() / 'fetches.jsonl')
    print(('Latest fetch: ' + fetches[-1]['outcome'] + ' (' + str(fetches[-1].get('category') or fetches[-1].get('file')) + ', ' +
           str(fetches[-1]['files_matched']) + '/' + str(fetches[-1]['files_fetched']) + ' verified)') if fetches else 'No category fetch yet.')
    return 0


# ---------------------------------------------------------------- CLI registration
def register(subs):
    p = subs.add_parser('run-backup', help='One backup attempt; safe for cron; records and publishes the result')
    p.add_argument('--trigger', choices=['manual', 'scheduled'], default='manual')
    p.add_argument('--select-clean', action='store_true', help='Make this run the selected clean recovery point (only if source matches baseline)')
    p.add_argument('--monitoring', action='store_true', help='Also send CloudWatch metrics (needs the optional metrics policy)')
    p.add_argument('--no-publish', action='store_true')
    p.set_defaults(func=run_backup)
    p = subs.add_parser('cloud-restore', help='Download the selected clean backup from S3, restore to a NEW folder, verify hashes')
    p.add_argument('--no-publish', action='store_true')
    p.set_defaults(func=cloud_restore)
    p = subs.add_parser('verify-protection', help='Read Versioning / Object Lock / encryption / public-access settings and clean-version retention')
    p.add_argument('--no-publish', action='store_true')
    p.set_defaults(func=verify_protection)
    p = subs.add_parser('ransomware-drill', help='Safe simulated attack on a disposable copy, then verified S3 recovery')
    p.add_argument('--skip-attack', action='store_true', help='Skip the stolen-key AccessDenied attempts')
    p.add_argument('--yes', action='store_true', help='Skip the typed confirmation (for rehearsals)')
    p.add_argument('--no-publish', action='store_true')
    p.set_defaults(func=ransomware_drill)
    p = subs.add_parser('publish', help='Rebuild dashboard-data.json from real records and upload it')
    p.set_defaults(func=publish)
    p = subs.add_parser('schedule', help='Print, install or remove the SafeVault cron line')
    p.add_argument('--every', type=int, default=60, help='Minutes between backups (5 for the demo, 60 for normal)')
    p.add_argument('--install', action='store_true')
    p.add_argument('--remove', action='store_true')
    p.add_argument('--yes', action='store_true')
    p.set_defaults(func=schedule)
    p = subs.add_parser('status', help='Show runs, recovery, protection, publication and schedule state')
    p.set_defaults(func=status)
