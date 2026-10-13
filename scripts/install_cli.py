#!/usr/bin/env python3
"""Optional Ubuntu prerequisite installer. Runs ONLY on your machine after approval."""
import os, pathlib, shutil, subprocess, sys, tempfile, urllib.request
if sys.platform!='linux':raise SystemExit('Run inside Ubuntu, not PowerShell.')
if shutil.which('aws'):
    p=subprocess.run(['aws','--version'],capture_output=True,text=True)
    if 'aws-cli/2.' in p.stdout+p.stderr:
        print('AWS CLI v2 already installed. No changes made.');sys.exit(0)
print('This installs Ubuntu prerequisites and AWS CLI v2 system-wide using the official AWS installer.')
print('It requires internet access and sudo. No AWS account resources or credentials are configured.')
print('Reference: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html')
if input('Type INSTALL CLI to approve: ').strip()!='INSTALL CLI':raise SystemExit('No changes approved.')
subprocess.run(['sudo','apt-get','update'],check=True)
subprocess.run(['sudo','apt-get','install','-y','ca-certificates','curl','unzip','gnupg','python3','cron'],check=True)
with tempfile.TemporaryDirectory(prefix='safevault-cli-') as temp:
    script=pathlib.Path(temp)/'install.sh'
    with urllib.request.urlopen('https://awscli.amazonaws.com/v2/install.sh',timeout=60) as r:script.write_bytes(r.read())
    print('Official installer downloaded. AWS maintains this script; it downloads and verifies the CLI package.')
    subprocess.run(['sudo','bash',str(script),'--system'],check=True)
subprocess.run(['/usr/local/bin/aws','--version'],check=True)
print('Installation finished. Reopen Ubuntu if your shell still finds an older aws command.')
