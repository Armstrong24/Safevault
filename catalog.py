#!/usr/bin/env python3
"""SafeVault file catalog: identify the TYPE / CATEGORY of every file and FETCH files by category.

1. classify()      : decides a file's category from its extension AND its first bytes
                     ("magic numbers"), so a renamed or encrypted file is caught.
2. categories      : `python3 lite.py categories` scans a folder (default: the clean synthetic
                     department data) and prints how many files and bytes are in each category.
                     Type mismatches (for example a .csv whose bytes are no longer text) and
                     ransomware-style names (.locked, .encrypted) are flagged.
3. fetch           : `python3 lite.py fetch --category records` downloads the selected clean
                     backup from Amazon S3 with the read-only recovery identity, validates the
                     WHOLE archive, but writes only the files of that category into a NEW folder,
                     then checks each one against the independent clean SHA-256 baseline.
                     `--file PATH` fetches one file, `--list` only shows what the backup holds.

Every file's category is also written into the backup manifest at backup time, so the cloud copy
itself records what type of data it contains.
"""
import json
from pathlib import Path
import sys
import time

HEAD_BYTES = 4096

# key -> (display name, extensions)
CATEGORIES = {
    'records':   ('Records / spreadsheets', {'.csv', '.tsv', '.xls', '.xlsx', '.ods', '.json', '.xml'}),
    'documents': ('Documents',              {'.pdf', '.doc', '.docx', '.odt', '.rtf', '.ppt', '.pptx'}),
    'text':      ('Text files',             {'.txt', '.md', '.log'}),
    'web':       ('Web pages',              {'.html', '.htm', '.css', '.js'}),
    'images':    ('Images',                 {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}),
    'archives':  ('Archives',               {'.zip', '.tar', '.gz', '.tgz', '.7z', '.rar'}),
    'binary':    ('Binary / other data',    set()),
    'empty':     ('Empty files',            set()),
    'suspicious': ('Suspicious (ransomware-style name)', {'.locked', '.encrypted', '.crypt', '.enc', '.crypted'}),
}
TEXT_CATEGORIES = {'records', 'text', 'web'}
OFFICE_ZIP = {'.docx', '.xlsx', '.pptx', '.odt', '.ods'}
MAGIC = [  # (first bytes, category)
    (b'%PDF', 'documents'),
    (b'\x89PNG\r\n\x1a\n', 'images'),
    (b'\xff\xd8\xff', 'images'),
    (b'GIF87a', 'images'), (b'GIF89a', 'images'),
    (b'PK\x03\x04', 'archives'),
    (b'\x1f\x8b', 'archives'),
    (b'7z\xbc\xaf\x27\x1c', 'archives'),
]


def label(category):
    return CATEGORIES.get(category, (category, set()))[0]


def by_extension(path):
    ext = Path(path).suffix.lower()
    for key, (_, extensions) in CATEGORIES.items():
        if ext in extensions:
            return key
    return None


def looks_like_text(head):
    if b'\x00' in head:
        return False
    try:
        head.decode('utf-8')
        return True
    except UnicodeDecodeError as e:
        return e.start >= len(head) - 3  # a multi-byte character cut off at the end of the sample


def classify(path, head=None, size=None):
    """Return {'category', 'detected_by', 'warning'} for one file.

    head/size can be given (e.g. from a manifest); otherwise the file is read (first 4 KB only)."""
    path = Path(path)
    if size is None:
        size = path.stat().st_size
    ext_category = by_extension(path)
    if ext_category == 'suspicious':
        return {'category': 'suspicious', 'detected_by': 'extension',
                'warning': 'ransomware-style file name (' + path.suffix + ')'}
    if size == 0:
        return {'category': 'empty', 'detected_by': 'size', 'warning': None}
    if head is None:
        with open(path, 'rb') as f:
            head = f.read(HEAD_BYTES)
    magic = next((cat for sig, cat in MAGIC if head.startswith(sig)), None)
    if magic == 'archives' and Path(path).suffix.lower() in OFFICE_ZIP:
        magic = ext_category  # .docx/.xlsx are ZIP containers by design
    text = looks_like_text(head)
    if ext_category in TEXT_CATEGORIES and not text:
        return {'category': ext_category, 'detected_by': 'extension',
                'warning': 'type mismatch: named ' + path.suffix + ' but contents are not readable text (possibly encrypted or corrupted)'}
    if magic and ext_category and magic != ext_category:
        return {'category': magic, 'detected_by': 'content',
                'warning': 'type mismatch: named ' + path.suffix + ' but contents look like ' + label(magic)}
    if magic:
        return {'category': magic, 'detected_by': 'content', 'warning': None}
    if ext_category:
        return {'category': ext_category, 'detected_by': 'extension', 'warning': None}
    return {'category': 'text' if text else 'binary', 'detected_by': 'content', 'warning': None}


def classify_name(path, size):
    """Name-only fallback for older backups whose manifest has no category."""
    if by_extension(path) == 'suspicious':
        return 'suspicious'
    if size == 0:
        return 'empty'
    return by_extension(path) or 'binary'


def scan(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError('Folder not found: ' + str(folder))
    files = []
    for p in sorted(folder.rglob('*')):
        if p.is_symlink() or not p.is_file():
            continue
        info = classify(p)
        files.append({'path': p.relative_to(folder).as_posix(), 'size': p.stat().st_size, **info})
    return files


def summarise(files):
    out = {}
    for f in files:
        s = out.setdefault(f['category'], {'category': f['category'], 'label': label(f['category']), 'files': 0, 'bytes': 0})
        s['files'] += 1
        s['bytes'] += f['size']
    return sorted(out.values(), key=lambda s: (-s['files'], s['category']))


def print_table(files):
    width = max([len(f['path']) for f in files] + [4])
    print('{:<{w}}  {:<26} {:>9}  {}'.format('File', 'Category', 'Bytes', 'Detected by', w=width))
    print('-' * (width + 52))
    for f in files:
        print('{:<{w}}  {:<26} {:>9}  {}'.format(f['path'], label(f['category']), f['size'], f.get('detected_by', 'manifest'), w=width))
    print()
    for s in summarise(files):
        print('  {:<36} {:>3} file(s) {:>10} bytes'.format(s['label'] + ' [' + s['category'] + ']', s['files'], s['bytes']))
    warnings = [f for f in files if f.get('warning')]
    for f in warnings:
        print('  WARNING ' + f['path'] + ': ' + f['warning'])
    return warnings


# ---------------------------------------------------------------- CLI commands
def categories_cmd(args):
    import lite
    folder = Path(args.folder).expanduser() if args.folder else lite.LAB / 'restored-good'
    print('\n== File categories in ' + str(folder))
    files = scan(folder)
    if not files:
        print('No files found.')
        return 0
    warnings = print_table(files)
    lite.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    lite.save(lite.STATE / 'categories.json', {'scanned_utc': lite.stamp(), 'folder': str(folder),
                                              'summary': summarise(files), 'warnings': len(warnings)})
    print('\n' + str(len(files)) + ' files in ' + str(len(summarise(files))) + ' categories' +
          (' · ' + str(len(warnings)) + ' warning(s)' if warnings else ' · no type mismatches'))
    return 0


def fetch_cmd(args):
    import uuid
    import contextlib
    import io
    import datetime as dt
    import lite
    import automation
    import safevault as sv
    if not args.list and not args.category and not args.file:
        raise lite.LabError('Choose --category NAME, --file PATH or --list. Categories: ' + ', '.join(CATEGORIES))
    if args.category and args.category not in CATEGORIES:
        raise lite.LabError('Unknown category. Choose one of: ' + ', '.join(CATEGORIES))
    folder = lite.STATE
    if not (folder / 'clean-receipt.json').exists():
        raise lite.LabError('No selected clean backup yet. Run: python3 lite.py run-backup')
    receipt = lite.read(folder / 'clean-receipt.json')
    baseline = lite.read(lite.LAB / 'known-good-hashes.json')

    def entry_category(entry):
        return entry.get('category') or classify_name(entry['path'], entry['size'])

    if args.list:
        selected = lambda entry: False            # validate everything, write nothing
    elif args.file:
        sv.safe_relative(args.file)
        selected = args.file
    else:
        selected = lambda entry: entry_category(entry) == args.category
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = lite.HOME / 'safevault-lite-recovery' / ('fetch-' + (args.category or 'file' if not args.list else 'list') + '-' + stamp + '-' + uuid.uuid4().hex[:6])
    print('\n== Fetch from the selected clean backup in Amazon S3 (run ' + receipt['run_id'] + ')')
    t0 = time.monotonic()
    c = automation.restore_config()
    with lite.credentials_from_profile_or_role():
        cloud = sv.AWSStore(c)
        automation.restore_identity(cloud)
        with contextlib.redirect_stdout(io.StringIO()):
            result = sv.restore(c, receipt['record_key'], receipt['record_version_id'], destination,
                                selected=selected, cloud=cloud)
    catalog = sorted((dict(e, category=entry_category(e)) for e in result['catalog']), key=lambda e: e['path'])
    if args.list:
        print_table(catalog)
        try:
            destination.rmdir()
        except OSError:
            pass
        print('\nWhole archive downloaded and validated (' + str(result['validated_files']) + ' files); nothing was written.')
        return 0
    fetched = [e for e in catalog if (e['path'] == args.file if args.file else e['category'] == args.category)]
    matched = [e for e in fetched if (destination / e['path']).is_file() and sv.digest(destination / e['path']) == baseline.get(e['path'])]
    record = {'fetched_utc': lite.stamp(), 'backup_run_id': receipt['run_id'], 'category': args.category,
              'file': args.file, 'files_fetched': len(fetched), 'files_matched': len(matched),
              'seconds': round(time.monotonic() - t0, 3)}
    record['outcome'] = 'passed' if fetched and len(matched) == len(fetched) else ('empty' if not fetched else 'failed')
    automation.append_jsonl(folder / 'fetches.jsonl', record)
    if not fetched:
        try:
            destination.rmdir()
        except OSError:
            pass
        print('The backup holds no files in category "' + args.category + '". Try: python3 lite.py fetch --list')
        return 0
    for e in fetched:
        ok = e in matched
        print('  ' + ('OK   ' if ok else 'FAIL ') + e['path'] + '  [' + label(e['category']) + ', ' + str(e['size']) + ' bytes]')
    if record['outcome'] != 'passed':
        raise lite.LabError('Fetched file(s) do not match the clean baseline; recorded as failed.')
    print('FETCH PASSED: ' + str(len(matched)) + '/' + str(len(fetched)) + ' ' +
          (label(args.category) if args.category else 'file') + ' verified against the clean SHA-256 baseline.')
    print('Archive fully validated (' + str(result['validated_files']) + ' files); only the requested files were written.')
    print('Fetched into NEW folder: ' + str(destination))
    return 0


def register(subs):
    p = subs.add_parser('categories', help='Identify the type/category of every file (extension + content check)')
    p.add_argument('--folder', help='Folder to scan (default: the clean synthetic department data)')
    p.set_defaults(func=categories_cmd)
    p = subs.add_parser('fetch', help='Fetch files of one category (or one file) from the clean S3 backup')
    p.add_argument('--category', help='One of: ' + ', '.join(CATEGORIES))
    p.add_argument('--file', help='One file path inside the backup, e.g. records/attendance.csv')
    p.add_argument('--list', action='store_true', help='Only list the files and categories inside the backup')
    p.set_defaults(func=fetch_cmd)


if __name__ == '__main__':
    for f in scan(sys.argv[1] if len(sys.argv) > 1 else '.'):
        print(json.dumps(f))
