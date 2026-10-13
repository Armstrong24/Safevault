#!/usr/bin/env python3
"""Small SafeVault lab helpers. AWS resources are configured by YOU in the Console."""
import argparse, base64, contextlib, csv, datetime as dt, hashlib, io, json, os
from pathlib import Path
import re, shlex, statistics, subprocess, sys, time, uuid
import safevault as sv
sys.modules.setdefault('lite',sys.modules[__name__])  # automation.py shares these classes/paths when run as a script
ROOT=Path(__file__).resolve().parent
BUCKET='s3-bucket-fcc-project';REGION='ap-south-1';PREFIX='backups/source-01'
HOME=Path.home();LAB=HOME/'safevault-lite-lab';STATE=HOME/'.local/state/safevault-lite'
CFG=HOME/'.config/safevault-lite/uploader.json';RESULTS=HOME/'safevault-lite-results';POLICIES=HOME/'safevault-lite-policies'
CREDENTIAL_ENV=['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_SECURITY_TOKEN','AWS_PROFILE','AWS_DEFAULT_PROFILE','AWS_ROLE_ARN','AWS_WEB_IDENTITY_TOKEN_FILE','AWS_CONTAINER_CREDENTIALS_RELATIVE_URI','AWS_CONTAINER_CREDENTIALS_FULL_URI','AWS_CONTAINER_AUTHORIZATION_TOKEN','AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE']
class LabError(Exception):pass
@contextlib.contextmanager
def credentials_from_profile_or_role():
    previous={k:os.environ[k] for k in CREDENTIAL_ENV if k in os.environ}
    for k in CREDENTIAL_ENV:os.environ.pop(k,None)
    try:yield
    finally:
        for k in CREDENTIAL_ENV:os.environ.pop(k,None)
        os.environ.update(previous)
def hashes(root):
    root=Path(root);return {p.relative_to(root).as_posix():sv.digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
def read(p):return sv.load_json(p)
def save(p,data):sv.save_json(p,data)
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def confirm(message,phrase):
    print(message)
    if input('Type '+phrase+' to continue: ').strip()!=phrase:raise LabError('Stopped without approval.')
def identity(cloud,kind):
    answer=cloud._run(['sts','get-caller-identity']);arn=answer['Arn']
    if kind=='uploader' and not (':user/' in arn and arn.split('/')[-1]=='safevault-lite-uploader'):raise LabError('Use the dedicated safevault-lite-uploader identity, not an administrator.')
    if kind=='restore' and ':assumed-role/SafeVaultLiteRestoreRole/' not in arn:raise LabError('Recovery must use the EC2 SafeVaultLiteRestoreRole. Do not copy keys onto EC2.')
    if kind=='lock-test' and not (':user/' in arn and arn.split('/')[-1]=='safevault-lite-lock-test'):raise LabError('Use the temporary lock-test identity, not the uploader or administrator.')
    return answer

def local_demo(args):
    subprocess.run([sys.executable,'-m','unittest','discover','-s',str(ROOT/'tests'),'-v'],cwd=ROOT,check=True)
    if not LAB.exists():
        with contextlib.redirect_stdout(io.StringIO()):sv.demo(LAB)
    elif not (LAB/'demo-results.json').exists():raise LabError('Incomplete existing lab. Choose another lab location deliberately; nothing is deleted.')
    if read(LAB/'demo-results.json').get('all_original_hashes_matched') is not True:raise LabError('Local demo did not pass.')
    if hashes(LAB/'restored-good')!=read(LAB/'known-good-hashes.json'):raise LabError('Original synthetic baseline has changed.')
    print('LOCAL DEMO PASSED. Clean synthetic source: '+str(LAB/'restored-good'))
    print('This is not AWS IAM or Object Lock evidence.')
def policies(args):
    if not POLICIES.exists():sv.write_policies(BUCKET,PREFIX,POLICIES)
    elif not (POLICIES/'uploader.json').exists():raise LabError('Policy folder exists but is incomplete; inspect it first.')
    # This test user has delete permission ONLY on disposable lock-test objects, never archives.
    resource='arn:aws:s3:::'+BUCKET+'/'+PREFIX+'/lock-test/*'
    test={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':['s3:PutObject','s3:GetObjectRetention','s3:DeleteObjectVersion'],'Resource':resource},{'Effect':'Allow','Action':'s3:PutObjectRetention','Resource':'arn:aws:s3:::'+BUCKET+'/'+PREFIX+'/lock-test/*/control.txt'},{'Effect':'Deny','Action':['s3:BypassGovernanceRetention','s3:PutObjectLegalHold'],'Resource':resource},{'Effect':'Deny','Action':'s3:PutObjectRetention','Resource':'arn:aws:s3:::'+BUCKET+'/'+PREFIX+'/lock-test/*/retained.txt'}]}
    arn='arn:aws:s3:::'+BUCKET
    recovery={'Version':'2012-10-17','Statement':[{'Sid':'ListOwnBackupPrefixOnly','Effect':'Allow','Action':['s3:ListBucket','s3:ListBucketVersions'],'Resource':arn,'Condition':{'StringLike':{'s3:prefix':[PREFIX,PREFIX+'/*']}}},{'Sid':'ReadProtectionSettings','Effect':'Allow','Action':['s3:GetBucketVersioning','s3:GetBucketObjectLockConfiguration','s3:GetEncryptionConfiguration','s3:GetBucketPublicAccessBlock'],'Resource':arn},{'Sid':'ReadBackupVersions','Effect':'Allow','Action':['s3:GetObject','s3:GetObjectVersion','s3:GetObjectRetention'],'Resource':arn+'/'+PREFIX+'/*'},{'Sid':'NeverWriteDeleteOrBypassBackups','Effect':'Deny','Action':['s3:PutObject','s3:DeleteObject','s3:DeleteObjectVersion','s3:PutObjectRetention','s3:PutObjectLegalHold','s3:BypassGovernanceRetention'],'Resource':arn+'/*'},{'Sid':'WriteDashboardDataObjectOnly','Effect':'Allow','Action':'s3:PutObject','Resource':'arn:aws:s3:::safevault-dashboard-8b4c47/dashboard-data.json'}]}
    dash_read={'Version':'2012-10-17','Statement':[{'Sid':'PublicReadDashboardDataOnly','Effect':'Allow','Principal':'*','Action':'s3:GetObject','Resource':'arn:aws:s3:::safevault-dashboard-8b4c47/dashboard-data.json'}]}
    dash_cors=[{'AllowedOrigins':['https://dancing-gumdrop-8b4c47.netlify.app'],'AllowedMethods':['GET','HEAD'],'AllowedHeaders':['*'],'MaxAgeSeconds':300}]
    extra={'restore-trust.json':{'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'ec2.amazonaws.com'},'Action':'sts:AssumeRole'}]},'lock-test.json':test,'recovery-user.json':recovery,'dashboard-bucket-policy-reference.json':dash_read,'dashboard-cors-reference.json':dash_cors}
    for name,data in extra.items():
        path=POLICIES/name
        if path.exists() and read(path)!=data:raise LabError('Existing '+name+' differs; will not overwrite.')
        if not path.exists():save(path,data)
    print('Policies generated for '+BUCKET+'. Nothing deployed. Folder: '+str(POLICIES))
def configure_source(args):
    if not (LAB/'known-good-hashes.json').exists():raise LabError('Run local-demo first.')
    if hashes(LAB/'restored-good')!=read(LAB/'known-good-hashes.json'):raise LabError('Synthetic baseline changed.')
    c=sv.validate_config({'backend':'aws','bucket':BUCKET,'region':REGION,'profile':'safevault-lite-uploader','prefix':PREFIX,'source_id':'source-01','source_directory':str(LAB/'restored-good'),'state_directory':str(STATE/'uploader'),'monitoring':args.monitoring})
    with credentials_from_profile_or_role():owner=identity(sv.AWSStore(c),'uploader')['Account']
    c['expected_bucket_owner']=owner
    if CFG.exists():
        old=sv.config(CFG)
        if any(old.get(k)!=c.get(k) for k in ['bucket','profile','source_directory','state_directory','expected_bucket_owner']):raise LabError('Existing configuration differs; refusing replacement.')
        c['monitoring']=args.monitoring or old.get('monitoring',False)
    save(CFG,c);print('Source configuration saved (no secret keys). Monitoring: '+str(c['monitoring']))
def source():
    c=sv.config(CFG)
    if c.get('profile')!='safevault-lite-uploader' or c['bucket']!=BUCKET or c['prefix']!=PREFIX or Path(c['source_directory']).resolve()!=(LAB/'restored-good').resolve():raise LabError('Not this isolated synthetic lab configuration.')
    identity(sv.AWSStore(c),'uploader');return c

def perform_backup(c):
    with contextlib.redirect_stdout(io.StringIO()):r=sv.backup(c)
    save(STATE/'last-upload.json',r);return r
def backup(args):
    with credentials_from_profile_or_role():
        c=source();r=perform_backup(c)
    clean=hashes(c['source_directory'])==read(LAB/'known-good-hashes.json')
    if getattr(args,'select_clean',False) and not clean:raise LabError('Upload completed, but source is not the original clean baseline; clean selection refused.')
    if clean and (getattr(args,'select_clean',False) or not (STATE/'clean-receipt.json').exists()):save(STATE/'clean-receipt.json',r)
    print('UPLOAD COMPLETED; not independently verified yet. Run: '+r['run_id'])
    print('Receipt: '+str(STATE/'last-upload.json'))
def corrupt_demo(args):
    c=sv.config(CFG)
    if Path(c['source_directory']).resolve()!=(LAB/'restored-good').resolve():raise LabError('Refusing to change any non-lab directory.')
    if not (STATE/'clean-receipt.json').exists():raise LabError('Make the clean cloud backup first.')
    root=LAB/'restored-good';known=read(LAB/'known-good-hashes.json')
    if hashes(root)!=known:raise LabError('Baseline differs; no files changed.')
    p=subprocess.run(['crontab','-l'],capture_output=True,text=True) if __import__('shutil').which('crontab') else None
    if p and ('SAFEVAULT_LITE_HOURLY' in p.stdout or 'SAFEVAULT_LITE_SCHEDULE' in p.stdout):raise LabError('Pause the SafeVault cron entry before the controlled corruption test.')
    confirm('Change only two synthetic fixture files, upload a changed backup, then put their original local bytes back? No malware or deletion.','SIMULATE')
    files=[root/'records/attendance.csv',root/'website/index.html'];original={p:p.read_bytes() for p in files};incident=stamp()
    try:
        for p in files:p.write_text('SIMULATED UNREADABLE SYNTHETIC CONTENT\n')
        with credentials_from_profile_or_role():r=perform_backup(source())
        save(STATE/'changed-receipt.json',r)
        clean=read(STATE/'clean-receipt.json')
        save(STATE/'simulation.json',{'incident_utc':incident,'clean_run_id':clean['run_id'],'recovery_point_age_seconds':(dt.datetime.fromisoformat(incident)-dt.datetime.fromisoformat(clean['data_cutoff_utc'])).total_seconds()})
    finally:
        for p,data in original.items():p.write_bytes(data)
    if hashes(root)!=known:raise LabError('Synthetic source baseline not restored; stop.')
    print('Changed backup uploaded. Local synthetic files restored. The earlier clean receipt remains selected. Now prove recovery on EC2.')
def same_key_test(args):
    if not (STATE/'clean-receipt.json').exists():raise LabError('Make a clean cloud backup first.')
    confirm('Write tiny deliberately INVALID bytes as a NEW version of the selected synthetic archive key? The original retained version remains selected. No archive version is deleted; this is only for the package-generated synthetic demonstration.','TEST VERSION')
    with credentials_from_profile_or_role():
        c=source();r=read(STATE/'clean-receipt.json');sv.verify_record(c,r,r['record_key'])
        path=STATE/'synthetic-invalid-archive.bin';path.write_bytes(b'SIMULATED BAD ARCHIVE VERSION; NO MALWARE\n')
        try:new=sv.AWSStore(c).put(r['archive_key'],path)
        finally:path.unlink(missing_ok=True)
    if new['VersionId']==r['archive_version_id']:raise LabError('No distinct new version returned.')
    save(STATE/'same-key-test.json',{'uploaded_utc':stamp(),'original_archive_version_id':r['archive_version_id'],'new_archive_version_id':new['VersionId'],'archive_key':r['archive_key']})
    print('New invalid version uploaded at the SAME archive key. The clean completion record still references the earlier retained VersionId. EC2 recovery must now prove the old bytes survive.')
def begin_recovery(args):
    if not (STATE/'clean-receipt.json').exists():raise LabError('Make the clean cloud backup first.')
    save(STATE/'recovery-clock.json',{'started_utc':stamp()});print('Recovery exercise clock started. Launch EC2 now, then create the handoff.')
def handoff(args):
    r=read(STATE/'clean-receipt.json');c=sv.config(CFG)
    result={'schema':1,'bucket':BUCKET,'region':REGION,'owner':c['expected_bucket_owner'],'receipt':r,'baseline':read(LAB/'known-good-hashes.json')}
    for key,path in [('simulation',STATE/'simulation.json'),('clock',STATE/'recovery-clock.json')]:
        if path.exists():result[key]=read(path)
    save(RESULTS/'handoff.json',result)
    print('PRIVATE handoff: '+str(RESULTS/'handoff.json'))
    print('Copy this separately to EC2. Never put it in the website bucket; it contains S3 object keys, VersionIds and a hash baseline, NOT credentials.')
def recover(args):
    bundle=read(Path(args.handoff).expanduser())
    if bundle.get('schema')!=1 or bundle.get('bucket')!=BUCKET or bundle.get('region')!=REGION:raise LabError('Unexpected handoff target.')
    if not re.fullmatch(r'\d{12}',bundle.get('owner','')):raise LabError('Invalid expected owner.')
    r=bundle['receipt'];base=HOME/'safevault-lite-recovery';destination=base/('files-'+uuid.uuid4().hex[:10])
    c=sv.validate_config({'backend':'aws','bucket':BUCKET,'region':REGION,'expected_bucket_owner':bundle['owner'],'prefix':PREFIX,'source_id':'source-01','source_directory':str(base/'unused-source'),'state_directory':str(base/'state'),'monitoring':False})
    with credentials_from_profile_or_role():
        identity(sv.AWSStore(c),'restore')
        with contextlib.redirect_stdout(io.StringIO()):
            retention=sv.audit_retention(c,r['record_key'],r['record_version_id'])
            result=sv.restore(c,r['record_key'],r['record_version_id'],destination,selected=args.file)
    if args.file:
        sv.safe_relative(args.file)
        if args.file not in bundle['baseline'] or sv.digest(destination/args.file)!=bundle['baseline'][args.file]:raise LabError('Single-file independent baseline mismatch.')
        result.update(trial_type='single-file',baseline_matched=True)
    else:
        if hashes(destination)!=bundle['baseline']:raise LabError('Independent original hashes do not match.')
        with (destination/'records/attendance.csv').open(newline='') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=['student_id','present'] or list(reader)!=[{'student_id':'DEMO001','present':'yes'},{'student_id':'DEMO002','present':'no'}]:raise LabError('CSV usability check failed.')
        result.update(trial_type='full',baseline_matched=True,csv_usable=True)
        if 'clock' in bundle and not (base/'latest-full-report.json').exists():
            elapsed=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(bundle['clock']['started_utc'])).total_seconds()
            if elapsed<0:raise LabError('Recovery clock is in the future; check machine clocks.')
            result['end_to_end_seconds']=elapsed
    result['retention_audit']=retention;result['file_count']=r['file_count'];result['archive_bytes']=r['archive_bytes']
    save(base/('report-'+uuid.uuid4().hex+'.json'),result)
    if args.file:save(base/'latest-file-report.json',result)
    else:save(base/'latest-full-report.json',result)
    print('RECOVERY PASSED: '+result['trial_type']+'; files: '+str(result['restored_files'])+'; restore_seconds: '+str(round(result['restore_seconds'],3)))
    print('Usable destination: '+str(destination));print('Private report: '+str(base/('latest-file-report.json' if args.file else 'latest-full-report.json')))

def expected_denial(cloud,args):
    try:cloud._run(args)
    except sv.VaultError as e:
        if '(AccessDenied)' in str(e):return True
        raise LabError('This was not an expected IAM AccessDenied; check the error: '+str(e))
    raise LabError('UNEXPECTED operation allowed. Stop and review the identity permissions.')
def iam_test(args):
    with credentials_from_profile_or_role():
        c=source();cloud=sv.AWSStore(c);STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=STATE/'disposable-iam-test.txt';path.write_text('Disposable synthetic permission test.\n')
        key=PREFIX+'/permission-tests/'+uuid.uuid4().hex+'/test.txt';v=cloud.put(key,path)['VersionId']
        tests={'read':['s3api','get-object','--bucket',BUCKET,'--key',key,'--version-id',v,str(STATE/'unexpected-read.txt')], 'list':['s3api','list-object-versions','--bucket',BUCKET,'--prefix',PREFIX+'/'], 'delete_version':['s3api','delete-object','--bucket',BUCKET,'--key',key,'--version-id',v], 'delete_current':['s3api','delete-object','--bucket',BUCKET,'--key',key]}
        passed={name:expected_denial(cloud,cmd) for name,cmd in tests.items()}
    save(STATE/'iam-test.json',{'checked_utc':stamp(),'key':key,'version_id':v,'tests':passed,'all_denied':all(passed.values())})
    print('SOURCE IAM TEST PASSED. This is NOT an independent Object Lock test. Disposable test version remains retained until expiry.')
def lock_test(args):
    confirm('Create two disposable lock-test objects under a separate temporary test identity? One control expires in 30 seconds; the other receives the bucket default. No backup archives are touched.','TEST LOCK')
    c={'region':REGION,'profile':'safevault-lite-lock-test'}
    with credentials_from_profile_or_role():
        cloud=sv.AWSStore(c);identity(cloud,'lock-test');STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=STATE/'disposable-lock-test.txt';path.write_text('Disposable Object Lock test only.\n')
        sha=base64.b64encode(hashlib.sha256(path.read_bytes()).digest()).decode();prefix=PREFIX+'/lock-test/'+uuid.uuid4().hex
        versions={}
        for name in ['retained.txt','control.txt']:
            opts=[]
            if name=='control.txt':opts=['--object-lock-mode','GOVERNANCE','--object-lock-retain-until-date',(dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=30)).isoformat()]
            result=cloud._run(['s3api','put-object','--bucket',BUCKET,'--key',prefix+'/'+name,'--body',str(path),'--checksum-algorithm','SHA256','--checksum-sha256',sha,'--server-side-encryption','AES256']+opts)
            if not result.get('VersionId') or result['VersionId']=='null':raise LabError('No VersionId; stop.')
            versions[name]=result['VersionId']
        save(STATE/'lock-test-objects.json',{'prefix':prefix,'versions':versions})
        print('Waiting 40 seconds for the control retention to expire...');time.sleep(40)
        cloud._run(['s3api','delete-object','--bucket',BUCKET,'--key',prefix+'/control.txt','--version-id',versions['control.txt']])
        retention=cloud._run(['s3api','get-object-retention','--bucket',BUCKET,'--key',prefix+'/retained.txt','--version-id',versions['retained.txt']])['Retention']
        if retention['Mode']!='GOVERNANCE' or dt.datetime.fromisoformat(retention['RetainUntilDate'].replace('Z','+00:00'))<=dt.datetime.now(dt.timezone.utc):raise LabError('Retained object has no active Governance retention.')
        expected_denial(cloud,['s3api','delete-object','--bucket',BUCKET,'--key',prefix+'/retained.txt','--version-id',versions['retained.txt']])
    save(STATE/'lock-test.json',{'checked_utc':stamp(),'expired_control_deleted':True,'retained_version_delete_denied':True,'retention':retention})
    print('OBJECT LOCK TEST PASSED: same identity deleted expired control, but not the actively retained version. Revoke/delete temporary test key and user now. Retained disposable object remains until expiry.')
def failure_test(args):
    with credentials_from_profile_or_role():
        c=source()
        if not c.get('monitoring'):raise LabError('Enable monitoring and attach namespace-restricted metrics policy first.')
        missing=STATE/('intentionally-missing-source-'+uuid.uuid4().hex)
        c=dict(c,source_directory=str(missing))
        confirm('Induce one backup failure using a NONEXISTENT synthetic source path? No existing files are modified.','TEST FAILURE')
        try:
            with contextlib.redirect_stdout(io.StringIO()):sv.backup(c)
        except sv.VaultError as e:
            save(STATE/'failure-test.json',{'induced_utc':stamp(),'expected_job_failure':True,'message':str(e),'email_delivery_verified':False})
            print('Expected backup failure induced. Check actual CloudWatch alarm and SNS email; delivery is NOT claimed by this command.');return
        raise LabError('Unexpected successful backup of missing source.')
def cron_entry(args):
    cfg=sv.config(CFG)
    if any(x in str(p) for p in [ROOT,CFG] for x in ['%','\n','\r']):raise LabError('Unsafe cron path.')
    cmd=' '.join(shlex.quote(x) for x in ['/usr/bin/python3',str(ROOT/'lite.py'),'backup'])
    print('0 * * * * PATH=/usr/local/bin:/usr/bin:/bin '+cmd+' >> '+shlex.quote(str(STATE/'hourly.log'))+' 2>&1 # SAFEVAULT_LITE_HOURLY')
def record_checks(args):
    confirm('Record only configuration/email/cost facts you personally checked. These fields are operator-reported, not automatic verification.','RECORD FACTS')
    questions=[('lifecycle_configured','Did you save and inspect the actual 30-day archive lifecycle rule?'),('failure_email','Did you actually receive the induced-failure SNS email?'),('missing_email','Did you actually receive a missing-backup SNS email after stopping scheduled jobs?')]
    report={k:input(q+' [y/N]: ').strip().lower()=='y' for k,q in questions}
    report['transition_observed']=False
    amount=input('Observed gross AWS service cost in USD BEFORE credits; leave blank if not known: ').strip()
    report['cost_usd']=float(amount) if amount else None
    if report['cost_usd'] is not None and (not __import__('math').isfinite(report['cost_usd']) or report['cost_usd']<0):raise LabError('Invalid cost; no facts saved.')
    report['recorded_utc']=stamp();save(STATE/'operator-checks.json',report);print('Operator-reported facts saved. Day-one lifecycle transition is always pending.')
def sanitized_evidence(reports):
    receipt=read(STATE/'clean-receipt.json') if (STATE/'clean-receipt.json').exists() else None
    trials=[]
    for path in reports:
        r=read(Path(path).expanduser())
        if not receipt or r.get('run_id')!=receipt['run_id'] or r.get('record_version_id')!=receipt['record_version_id'] or r.get('record_key')!=receipt['record_key']:raise LabError('Recovery report does not match selected clean version.')
        if r.get('trial_type')!='full' or r.get('baseline_matched') is not True or r.get('csv_usable') is not True or r.get('validated_files')!=receipt['file_count']:raise LabError('Only passing FULL independent reports can count as full recovery trials.')
        if not isinstance(r.get('restore_seconds'),(int,float)) or not __import__('math').isfinite(r['restore_seconds']) or r['restore_seconds']<0:raise LabError('Invalid recovery duration.')
        dt.datetime.fromisoformat(r['verified_utc'])
        if any(x['verified_utc']==r['verified_utc'] for x in trials):raise LabError('Duplicate report would inflate the trial count; refusing.')
        t={'verified_utc':r['verified_utc'],'files':r['validated_files'],'restore_seconds':r['restore_seconds'],'baseline_matched':True,'csv_usable':True}
        if isinstance(r.get('end_to_end_seconds'),(int,float)) and r['end_to_end_seconds']>=0:t['end_to_end_seconds']=r['end_to_end_seconds']
        trials.append(t)
    checks=read(STATE/'operator-checks.json') if (STATE/'operator-checks.json').exists() else {}
    public_checks={k:checks.get(k) is True for k in ['lifecycle_configured','failure_email','missing_email']}
    public_checks['transition_observed']=False
    if (STATE/'iam-test.json').exists():public_checks['iam_denials']=read(STATE/'iam-test.json').get('all_denied') is True
    if (STATE/'lock-test.json').exists():
        lock=read(STATE/'lock-test.json');public_checks['lock_enforcement']=lock.get('expired_control_deleted') is True and lock.get('retained_version_delete_denied') is True
    public_checks['same_key_history']=False
    if trials and (STATE/'same-key-test.json').exists():
        overwrote=read(STATE/'same-key-test.json')
        public_checks['same_key_history']=overwrote.get('original_archive_version_id')==receipt.get('archive_version_id') and any(dt.datetime.fromisoformat(t['verified_utc'])>=dt.datetime.fromisoformat(overwrote['uploaded_utc']) for t in trials)
    public_checks['retention_metadata']=bool(trials) and all(len(read(Path(p).expanduser()).get('retention_audit',{}).get('checks',[]))==2 for p in reports)
    uploads=[]
    for name,kind in [('changed-receipt.json','Changed'),('clean-receipt.json','Clean')]:
        if (STATE/name).exists():
            r=read(STATE/name);uploads.append({k:r[k] for k in ['data_cutoff_utc','archive_bytes','file_count']}|{'type':kind})
    simulation=None
    if receipt and (STATE/'simulation.json').exists():
        incident=read(STATE/'simulation.json')
        if incident.get('clean_run_id')==receipt['run_id']:simulation={'recovery_point_age_seconds':incident['recovery_point_age_seconds']}
    return {'schema':1,'kind':'results' if uploads or trials else 'empty','generated_utc':stamp(),'uploads':uploads,'recoveries':trials,'checks':public_checks,'simulation':simulation,'cost_usd':checks.get('cost_usd')}
def embed_evidence(html,data):
    encoded=json.dumps(data,ensure_ascii=True).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    pattern=r'(<script type="application/json" id="evidence">).*?(</script>)'
    result,count=re.subn(pattern,lambda m:m.group(1)+encoded+m.group(2),html,count=1,flags=re.S)
    if count!=1:raise LabError('Dashboard evidence marker missing.')
    return result
def dashboard(args):
    if len(args.reports)>30:raise LabError('At most 30 reports.')
    data=sanitized_evidence(args.reports);RESULTS.mkdir(parents=True,exist_ok=True,mode=0o700)
    save(RESULTS/'results.json',data)
    output=RESULTS/'website';output.mkdir(exist_ok=True,mode=0o700)
    (output/'index.html').write_text(embed_evidence((ROOT/'website/index.html').read_text(),data))
    print('Sanitized evidence: '+str(RESULTS/'results.json'))
    print('Upload ONLY '+str(output/'index.html')+' to your separate website bucket. Never upload raw handoff or reports.')
    print('This is an evidence snapshot, not live monitoring.')
def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    for command in ['local-demo','policies','backup','corrupt-demo','same-key-test','begin-recovery','handoff','iam-test','lock-test','failure-test','cron-entry','record-checks']:subs.add_parser(command)
    subs.choices['backup'].add_argument('--select-clean',action='store_true')
    p=subs.add_parser('configure-source');p.add_argument('--monitoring',action='store_true')
    p=subs.add_parser('recover');p.add_argument('--handoff',default=str(HOME/'handoff.json'));p.add_argument('--file')
    p=subs.add_parser('dashboard');p.add_argument('--reports',nargs='*',default=[])
    import automation;automation.register(subs)
    args=parser.parse_args();os.umask(0o077)
    if sys.platform!='linux':parser.error('Run inside Ubuntu, not PowerShell.')
    if sys.version_info<(3,10):parser.error('Python 3.10+ required.')
    try:
        fn=getattr(args,'func',None) or globals()[args.command.replace('-','_')]
        rc=fn(args)
        if rc:return rc
    except (LabError,sv.VaultError,OSError,ValueError,KeyError,subprocess.CalledProcessError) as e:
        print('STOPPED: '+str(e)+'\nDo not claim this step passed. Share the sanitized error.',file=sys.stderr);return 1
    except KeyboardInterrupt:print('Interrupted. Inspect AWS resources and any temporary test identities.',file=sys.stderr);return 130
    return 0
if __name__=='__main__':sys.exit(main())
