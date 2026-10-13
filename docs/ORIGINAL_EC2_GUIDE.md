# SafeVault Lite — complete the working demonstration today

**For Ak and group · Existing bucket: `s3-bucket-fcc-project` · Region: Mumbai `ap-south-1`**

## What “completed today” means

Target a working prototype: scheduled compressed backups, private versioned retained storage, restricted source credentials, successful historical recovery on a clean EC2 instance, integrity/usability checks, monitoring, safe corruption and permission tests, and a dashboard with your real evidence.

**Not achievable today:** observing an automatic transition that requires an object to age for 30 days. Configure the rule today; preserve an eligible object and observe its actual transition later. Similarly, email delivery and missing-job detection need real execution and elapsed time. Never mark an untested item as passed.

Nothing has been configured in your AWS account by this chat. The package has been tested offline; your live AWS results must still be checked. Allow roughly 4–7 focused hours for a first setup, but account permissions, internet access, AWS provisioning and errors can extend this. Finish cloud recovery before website polish. Selection for a certificate cannot be guaranteed.

## The simple explanation

1. **Back up:** Ubuntu compresses synthetic files and records SHA-256 hashes.
2. **Protect:** private S3 stores retained historical versions; source credentials can upload but cannot read or delete backups.
3. **Recover:** a separate EC2 role retrieves the explicitly selected clean version, verifies its original hashes, and checks the CSV contents.

Console setup is manual and visible. Routine backups use cron. The website is a read-only evidence snapshot, not another cloud application or a live dashboard.

---

# Part A — put the files in Ubuntu correctly

## 1. Download and extract the NEW Lite ZIP in Windows

Keep both older projects unchanged. Extract the new ZIP using **File Explorer → right-click → Extract All**. Find the inner folder named **`safevault-lite`**. It contains `lite.py`, `safevault.py`, `scripts`, `tests`, and `website`.

Do not copy the ZIP itself into a folder and assume the program is installed. Copy the extracted inner folder.

## 2. Open your Ubuntu home folder in File Explorer

Open **Ubuntu**, not PowerShell, and run:

```bash
cd "$HOME"
explorer.exe .
```

The final dot matters. Windows File Explorer should open your Linux home. Copy the extracted **`safevault-lite`** folder into this window.

Alternative: enter `\\wsl$` in File Explorer's address bar, choose your actual Ubuntu distribution, open `home`, then your Linux username. Distribution names can differ; do not guess your username or edit WSL files through Windows AppData.

Your target is:

```text
Ubuntu home / safevault-lite / lite.py
Ubuntu home / safevault-lite / safevault.py
```

Keep the working project in the Linux home filesystem, not Windows Downloads or `/mnt/c`.

## 3. Verify the folder and run the local demonstration

In **local Ubuntu / WSL**:

```bash
cd "$HOME/safevault-lite"
pwd
ls
python3 --version
python3 lite.py local-demo
```

Use Python 3.10 or newer. Expected: tests pass and `LOCAL DEMO PASSED`. The local demonstration creates only synthetic fixture data and compares original hashes after restoration. It does not prove cloud IAM or Object Lock.

Directories used by this edition:

| Location | Purpose |
|---|---|
| `~/safevault-lite` | Code and blank dashboard template |
| `~/safevault-lite-lab/restored-good` | Clean synthetic source |
| `~/safevault-lite-lab/known-good-hashes.json` | Original baseline outside the backed-up directory |
| `~/safevault-lite-policies` | Reviewable IAM/TLS/lifecycle JSON |
| `~/.config/safevault-lite/uploader.json` | Application settings, not secret keys |
| `~/.local/state/safevault-lite` | Private receipts and test evidence |
| `~/safevault-lite-results` | Private handoff/reports; sanitized website is in its `website` subfolder |

The demo refuses incomplete or changed pre-existing lab data rather than deleting it. If it stops, share the error.

## 4. Install/check AWS CLI v2

```bash
aws --version
```

If missing or version 1, run the optional installer:

```bash
python3 scripts/install_cli.py
```

It requests `INSTALL CLI` approval before installing Ubuntu prerequisites with sudo and using the official AWS CLI installer. Internet access is required on your machine. Then:

```bash
export PATH="/usr/local/bin:$PATH"
aws --version
```

**Never create root access keys. Never send credentials, passwords, MFA codes or private SSH keys into chat.** Use an authorized Console administrator for resource setup; keep source CLI credentials restricted. If your institution limits permissions or a service requires a paid-plan upgrade, stop rather than bypassing the restriction.

---

# Part B — protect the existing bucket and create the source identity

## 5. Generate policies for YOUR bucket

In **local Ubuntu / WSL**:

```bash
cd "$HOME/safevault-lite"
python3 lite.py policies
explorer.exe "$HOME/safevault-lite-policies"
```

These files are generated only. They have not been deployed. No CloudFormation, Terraform or remote-command framework is used in Lite.

## 6. Inspect and configure the EXISTING backup bucket in AWS Console

Use your trusted authorized Console login. Check MFA, current account plan, credit balance/expiry, and any already-running resources. Create a small monthly cost budget, for example USD 5 with 50%, 80% and 100% email alerts. A budget is an alert, not a spending cap; track gross usage before credits.

Open **S3 → `s3-bucket-fcc-project`**. Confirm the actual region is **Mumbai / `ap-south-1`**. Do not create a second backup bucket.

Check/configure:

| Setting | Required lab value |
|---|---|
| Block Public Access | All four enabled |
| Object Ownership | Bucket owner enforced; ACLs disabled |
| Versioning | Enabled |
| Default encryption | SSE-S3 |
| Object Lock | Enabled |
| Default retention | Fixed, Governance, 7 days |

**Enabling Object Lock is irreversible; you cannot then disable it or suspend versioning.** Governance is the short development setting. Do not choose Compliance. If existing encryption/retention/settings differ from these, do not blindly replace or shorten them—ask for review.

If the bucket is not empty or has an existing policy/configuration from another deployment, inspect it before changing anything. Do not delete versions to make it look empty.

### HTTPS-only bucket policy

In Ubuntu:

```bash
cat "$HOME/safevault-lite-policies/bucket-tls.json"
```

In **bucket → Permissions → Bucket policy**, paste this policy only if there is no existing policy. Otherwise merge its statement with existing required statements; do not replace the whole policy blindly. Keep Block Public Access enabled.

### 30-day lifecycle rule

Open **bucket → Management → Lifecycle rules → Create**:

- Name: `SafeVaultLiteArchiveIA30Days`.
- Prefix: `backups/source-01/`.
- Transition current versions to **Standard-IA after 30 days**.
- Transition noncurrent versions to **Standard-IA after 30 noncurrent days**.
- Keep the normal small-object transition restriction; the synthetic compressed archive is larger than 128 KB.
- Do not add expiration/deletion rules today.

The generated `lifecycle.json` shows a size-filtered configuration for reference. Small completion records should remain in Standard. Seven-day retention expiry permits deletion; it does not automatically delete objects. Configure lifecycle now and capture its settings, but mark actual automatic transition as **pending elapsed time**.

## 7. Create a dedicated upload-only IAM user

In **IAM → Users → Create user**:

- Exact name: **`safevault-lite-uploader`**.
- No AWS Console access.
- Do not attach broad managed policies.

Add an inline JSON policy using:

```bash
cat "$HOME/safevault-lite-policies/uploader.json"
```

Name it `SafeVaultLiteUploaderPolicy`.

Add another inline JSON policy using:

```bash
cat "$HOME/safevault-lite-policies/optional-metrics.json"
```

Name it `SafeVaultLiteMetricsPolicy`. This permits metric writes only to the SafeVault namespace, not CloudWatch administration.

In **user → Security credentials → Create access key**, create an approved student-lab CLI key. Keep its values private and local. Long-lived keys are a lab fallback, not the preferred production authentication design. Revoke them after the project.

In **local Ubuntu / WSL**:

```bash
aws configure --profile safevault-lite-uploader
```

Enter your actual uploader key and secret locally; region `ap-south-1`; output `json`.

```bash
aws sts get-caller-identity --profile safevault-lite-uploader
```

The ARN must identify `safevault-lite-uploader`, not root or an administrator. The helper checks this exact identity as well.

If a demonstration is repeated after the selected backup's 7-day retention expires, do not pretend its old retention is still active. On the unchanged clean synthetic source, make and explicitly select a fresh clean backup with `python3 lite.py backup --select-clean`, then repeat the corruption/version test, clock, handoff and recovery. Old reports no longer match the new selected version and must not be reused.

## 8. Make the first real cloud backup

```bash
cd "$HOME/safevault-lite"
python3 lite.py configure-source --monitoring
python3 lite.py backup
```

Expected: `UPLOAD COMPLETED`. This is not yet independently verified.

Using the trusted Console login, inspect the S3 backup prefix. A run contains `data.tar.gz` and `complete.json`. View **versions** and inspect retention on both exact uploaded versions: Governance mode and a future retain-until date. Keep screenshots with unnecessary account details redacted.

Do not try to list S3 using the uploader and then grant full permissions to fix the expected denial. The first clean backup receipt is preserved as the selected recovery point.

---

# Part C — monitoring and a safe simulated incident

## 9. Create SNS and CloudWatch alerts in Console

Open **SNS in Mumbai → Topics → Create Standard topic** named `safevault-lite-alerts`. Add an email subscription using `your-email@example.com`, or another address you choose. **Confirm the subscription email.**

Open **CloudWatch in Mumbai → Alarms → Create alarm**. The first monitored backup should have published SafeVault metrics with dimension `SourceId=source-01`.

Create:

| Alarm | Metric and setting |
|---|---|
| Failed backup | `BackupFailed`, Sum, 5-minute period, threshold ≥ 1, 1 breaching point; missing data not breaching |
| Missing hourly backup | `BackupSucceeded`, Sum, 1-hour period, threshold < 1, 2 consecutive breaching periods; missing data breaching |

Use the SNS topic for both ALARM actions. These settings are not instant detection. Preserve actual alarm/email timestamps.

Induce a safe failure:

```bash
cd "$HOME/safevault-lite"
python3 lite.py failure-test
```

Type `TEST FAILURE` only when ready. It points one run at a nonexistent synthetic directory; it does not modify your real source. Check the real CloudWatch alarm and actual received SNS email. The program deliberately does not claim delivery.

For a controlled shorter missing-metric demonstration, you may temporarily use a **5-minute period with 2 breaching points** on the missing alarm, then stop emitting success metrics and measure the actual alert. This is a demonstration configuration, not appropriate for normal hourly jobs. Restore the normal 1-hour / 2-point configuration afterwards. Do not claim an exact 10-minute delivery guarantee. If using the normal setting, allow multiple hours while you work on recovery and the website.

## 10. Upload a changed synthetic backup

Before installing cron:

```bash
python3 lite.py corrupt-demo
```

Type `SIMULATE` when ready. It changes only two package-generated synthetic files, uploads a newer changed backup, then restores their original local bytes. It uses no malware and deletes no source files. The first clean cloud receipt remains selected.

This shows why “latest uploaded” is not necessarily “known good.” Your next EC2 restoration must use the earlier clean completion record and its explicit archive VersionId. Source hashes are a transfer/integrity check, not ransomware detection.

### Also prove actual same-key version history

```bash
python3 lite.py same-key-test
```

Type `TEST VERSION` only after reading the warning. This writes a tiny deliberately invalid NEW version at the original **synthetic** archive key. It does not delete or change the retained earlier version, and the selected clean completion record still references that earlier VersionId. The next EC2 recovery must retrieve the original version and pass its original hashes. In Console, Show versions can display both versions at that same key. This proves more than just timestamped folders. Do not use this test on real institutional backups.

---

# Part D — clean EC2 recovery and actual measurements

## 11. Create the read-only recovery role

In Ubuntu:

```bash
cat "$HOME/safevault-lite-policies/restore.json"
```

In Console:

1. **IAM → Policies → Create → JSON**: paste `restore.json`; name `SafeVaultLiteRestorePolicy`.
2. **IAM → Roles → Create**: AWS service → EC2.
3. Attach only `SafeVaultLiteRestorePolicy`.
4. Exact role name: **`SafeVaultLiteRestoreRole`**.

The role reads relevant backup versions and retention metadata; it cannot upload/delete/change retained backups. No SSM role, Lambda or extra artifact bucket is required.

## 12. Start the recovery exercise clock and launch EC2

In **local Ubuntu / WSL**, immediately before launching:

```bash
python3 lite.py begin-recovery
```

Then in **EC2 Console, Mumbai → Launch instance**:

| Setting | Value |
|---|---|
| Name | `safevault-lite-recovery-test` |
| Image | Official Ubuntu Server LTS, compatible x86_64 image |
| Type | Small supported type such as `t3.micro`; review actual eligibility/cost |
| Root disk | Small encrypted gp3, e.g. 8 GiB if the image supports it; delete on termination |
| Network | Existing public subnet with an Internet Gateway route and public IPv4 |
| Security group | SSH port 22 from **My IP only**, not 0.0.0.0/0 |
| Key pair | New RSA `.pem` key pair; download privately |
| IAM instance profile | `SafeVaultLiteRestoreRole` |
| Instance metadata | Require IMDSv2 |

No NAT gateway, load balancer, database or always-running website EC2 is needed. If your account lacks public networking, required IAM permissions or compatible instance types, stop and share the error. Do not assume EC2, EBS or public IPv4 are free.

## 13. Copy code and the PRIVATE recovery handoff

Back in **local Ubuntu / WSL**:

```bash
cd "$HOME/safevault-lite"
python3 lite.py handoff
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
read -r -p "Full Ubuntu path to the downloaded .pem key: " KEY_FROM
```

The key may be in Windows Downloads, accessible through `/mnt/c/...`; enter its actual full path, not an invented username. Copy it to the Linux filesystem without replacing an existing key:

```bash
cp -n "$KEY_FROM" "$HOME/.ssh/safevault-lite.pem"
chmod 400 "$HOME/.ssh/safevault-lite.pem"
KEY="$HOME/.ssh/safevault-lite.pem"
read -r -p "Recovery instance public IPv4 address: " EC2_IP
scp -i "$KEY" -r "$HOME/safevault-lite" "ubuntu@$EC2_IP:/home/ubuntu/"
scp -i "$KEY" "$HOME/safevault-lite-results/handoff.json" "ubuntu@$EC2_IP:/home/ubuntu/handoff.json"
ssh -i "$KEY" "ubuntu@$EC2_IP"
```

If the saved `.pem` already exists, verify it corresponds to the instance's key pair; `cp -n` does not replace it. On first SSH connection, compare the host-key fingerprint with the EC2 system log before accepting.

**Copy only code and handoff. Never copy `~/.aws`, uploader keys, administrator credentials, or your private SSH key onto EC2.** The handoff contains the selected S3 object keys/VersionIds and original hashes, not AWS credentials. It must never be uploaded to the website bucket.

## 14. Run full recovery on EC2

**You are now on EC2**, not the local laptop:

```bash
cd "$HOME/safevault-lite"
python3 --version
aws --version
```

If CLI v2 is missing:

```bash
python3 scripts/install_cli.py
export PATH="/usr/local/bin:$PATH"
```

Do not run `aws configure` on EC2. The attached role supplies temporary credentials.

```bash
aws sts get-caller-identity
```

The ARN must contain `assumed-role/SafeVaultLiteRestoreRole/`.

```bash
python3 lite.py recover --handoff "$HOME/handoff.json"
```

The helper audits retention on both selected versions, restores to a NEW unique destination, compares restored files to the independent pre-corruption baseline, and validates the expected synthetic attendance CSV. Expected: `RECOVERY PASSED: full`.

The printed private report is `~/safevault-lite-recovery/latest-full-report.json`. The full result includes download/extract/verify duration. The **first** full recovery on this fresh instance also records the launch-clock-to-usable exercise duration, including manual provisioning/copy/install time. That is a measured exercise scope, not a guaranteed institutional RTO.

You can inspect:

```bash
cat "$HOME/safevault-lite-recovery/latest-full-report.json"
```

Use the printed actual destination to inspect recovered CSV contents. Do not insert unknown destinations or keys into arbitrary commands. The CSV usability check has already executed.

## 15. Save three separate trial reports

Keep the EC2 SSH session open. Open a **second local Ubuntu / WSL terminal**. Enter the project folder and re-enter the IP if needed:

```bash
cd "$HOME/safevault-lite"
KEY="$HOME/.ssh/safevault-lite.pem"
read -r -p "Recovery instance public IPv4 address: " EC2_IP
mkdir -p "$HOME/safevault-lite-results"
scp -i "$KEY" "ubuntu@$EC2_IP:/home/ubuntu/safevault-lite-recovery/latest-full-report.json" "$HOME/safevault-lite-results/recovery-1.json"
```

In the **EC2 terminal**, run a second trial:

```bash
python3 lite.py recover --handoff "$HOME/handoff.json"
```

In the **local terminal**, copy it before running the next trial:

```bash
scp -i "$KEY" "ubuntu@$EC2_IP:/home/ubuntu/safevault-lite-recovery/latest-full-report.json" "$HOME/safevault-lite-results/recovery-2.json"
```

In the **EC2 terminal**, run a third trial:

```bash
python3 lite.py recover --handoff "$HOME/handoff.json"
```

In the **local terminal**:

```bash
scp -i "$KEY" "ubuntu@$EC2_IP:/home/ubuntu/safevault-lite-recovery/latest-full-report.json" "$HOME/safevault-lite-results/recovery-3.json"
```

These are three restore trials on one clean instance—not three separate fresh instance launches. Do not copy the same report three times; the dashboard generator refuses duplicate timestamps.

### Single historical file recovery

In the **EC2 terminal**:

```bash
python3 lite.py recover --handoff "$HOME/handoff.json" --file records/attendance.csv
```

This verifies the selected file against the original baseline. The archive still has to be downloaded; single-file restore is not a random-access compressed archive service. Its separate report is `latest-file-report.json`; it does not count as a full recovery trial.

Copy it back if needed:

```bash
scp -i "$KEY" "ubuntu@$EC2_IP:/home/ubuntu/safevault-lite-recovery/latest-file-report.json" "$HOME/safevault-lite-results/single-file.json"
```

That copy command runs in the **local** terminal.

## 16. Terminate recovery EC2

Save evidence first, then in EC2 Console terminate **only your recovery-test instance**. Verify it is terminated, its root EBS disk was deleted, and there are no leftover allocated addresses or volumes you created. Stop is not the same as terminate; disks can remain billable. Use a fresh instance again for the professor's demonstration if needed, and terminate it afterwards.

---

# Part E — separate IAM and Object Lock tests

## 17. Test source permissions

In **local Ubuntu / WSL**:

```bash
cd "$HOME/safevault-lite"
python3 lite.py iam-test
```

It uploads a NEW disposable test object under a test subprefix and then expects read, list, version-delete and current-delete requests to fail under the uploader. Only real `(AccessDenied)` errors count—not network failures. It does not attempt deletion of your clean backup archive.

Expected: `SOURCE IAM TEST PASSED`. This proves IAM restrictions, not Object Lock enforcement. The disposable retained version remains for later cleanup.

## 18. Independently test Governance lock enforcement

Use the trusted Console administrator to create a TEMPORARY user:

- Exact name: **`safevault-lite-lock-test`**.
- No Console access.
- No broad managed policies.
- Add only the inline policy printed by:

```bash
cat "$HOME/safevault-lite-policies/lock-test.json"
```

This identity can create/read retention/delete versions only under the disposable `lock-test` prefix, never your archives. It can set short retention only on a control object and is explicitly denied Governance bypass.

Create one temporary CLI key; enter its values locally:

```bash
aws configure --profile safevault-lite-lock-test
aws sts get-caller-identity --profile safevault-lite-lock-test
python3 lite.py lock-test
```

Use region `ap-south-1`, output `json`. Type `TEST LOCK` to approve two NEW disposable objects.

The script:

1. Creates a retained object with the actual bucket default.
2. Creates a separate control with 30-second explicit Governance retention.
3. Waits 40 seconds.
4. Deletes the expired control under the test identity—establishing effective version-delete permission.
5. Confirms the other version still has active Governance retention.
6. Requires deletion of that retained version to fail under the SAME identity.

Expected: `OBJECT LOCK TEST PASSED`. This is stronger than merely showing uploader denial. Unknown extra bucket-policy denies could confound attribution; keep the backup bucket policy limited to reviewed HTTPS enforcement and necessary known statements. If the control cannot be deleted, no lock-enforcement success is claimed.

**Immediately deactivate/delete the temporary key and user in IAM after saving evidence.** No Governance bypass is used by the script. Its retained disposable object remains until expiry and still needs deliberate later deletion. Do not include the temporary key in screenshots or publish credentials.

---

# Part F — scheduling and dashboard

## 19. Install hourly backups only after recovery works

In **local Ubuntu / WSL**:

```bash
cd "$HOME/safevault-lite"
python3 lite.py cron-entry
```

Copy the ONE printed line. If `crontab` is missing and AWS CLI v2 was already installed, install only Ubuntu's cron package with sudo before continuing:

```bash
sudo apt-get update
sudo apt-get install -y cron
```

These commands change local Ubuntu packages, not AWS resources. If cron is already installed, skip them.

```bash
crontab -e
```

Paste the printed line **once**, preserving existing unrelated jobs. Save and exit. Verify/start cron:

```bash
crontab -l
sudo service cron start
sudo service cron status
```

WSL and Windows must remain awake. A sleeping/shut-down laptop cannot run cron. The cron line includes the AWS CLI path and runs only the restricted uploader helper. No administrator profile is scheduled. Verify at least one actual scheduled receipt in the private uploader state's `receipts` directory.

For a controlled scheduling demonstration you may temporarily change only the cron timing to every 5 minutes on this tiny synthetic dataset, observe a real scheduled run, then restore hourly. Never leave minute-by-minute backups running for normal use. Document which schedule was tested.

To test the normal missing-hourly alarm, temporarily remove/comment only your SafeVault cron line and stop manual successful backup runs. Keep other users' jobs unchanged. Observe and record the actual email time, then restore the line.

## 20. Record only observed facts

```bash
python3 lite.py record-checks
```

Type `RECORD FACTS`. Answer yes only if you personally checked the saved lifecycle rule or actually received the specified SNS email. Leave gross AWS cost blank if billing data is not yet available. AWS billing is not necessarily immediate; a zero payable bill after credits is not zero service usage.

The program always marks actual 30-day transition as pending on day one. Operator-reported facts are distinguished from programmatically checked recovery evidence.

## 21. Generate the dashboard with YOUR actual reports

```bash
python3 lite.py dashboard --reports \
  "$HOME/safevault-lite-results/recovery-1.json" \
  "$HOME/safevault-lite-results/recovery-2.json" \
  "$HOME/safevault-lite-results/recovery-3.json"
```

If only one trial passed, supply only its one actual path. Do not provide nonexistent or copied duplicate reports. If recovery has not passed, generate the unverified dashboard without report arguments:

```bash
python3 lite.py dashboard
```

The command generates:

- `~/safevault-lite-results/results.json`: sanitized evidence, suitable for the dashboard's **Load real results** button.
- `~/safevault-lite-results/website/index.html`: self-contained dashboard with the evidence already embedded.

Open the generated website folder in File Explorer:

```bash
explorer.exe "$HOME/safevault-lite-results/website"
```

Double-click `index.html`. It works offline because it has no backend or external library requirement. Sample mode is clearly labeled; return to real results for the professor. If the earlier raw dashboard file is open, use **Load real results** and choose sanitized `results.json`, not a raw private recovery report.

## 22. Host the evidence page separately

In S3 create a NEW website-only general-purpose bucket with a unique name. Keep **all Block Public Access enabled**, ACLs disabled/bucket owner enforced, and SSE-S3. No Object Lock is required. Leave S3 static website hosting disabled.

Upload **only the generated `index.html` from the `website` subfolder**. Do not upload the project ZIP, handoff, raw reports, source records, `.aws`, `.pem`, policy/test evidence or backup archives.

In CloudFront:

1. Create a standard **pay-as-you-go** distribution if available to your account; do not subscribe to a flat-rate paid plan.
2. Choose the new website bucket's regular regional S3 origin—not its website endpoint.
3. Create/use OAC and **always sign** origin requests.
4. Apply the generated CloudFront read policy to the WEBSITE bucket only.
5. Viewer protocol: redirect HTTP to HTTPS.
6. Default root object: `index.html` without a leading slash.
7. Use the generated CloudFront hostname; do not buy a domain.
8. Wait for deployment, then open the HTTPS URL.

If the Console requires an account upgrade or unavailable service, stop. Present the same evidence page locally today and report hosting as not completed; do not make the backup bucket public as a workaround. Free Tier accounts cannot use CloudFront flat-rate plans, so “Free flat-rate plan” is not the same as your AWS account plan.

The page is a read-only public evidence snapshot, not live monitoring. After later tests, regenerate and re-upload `index.html`. If CloudFront serves the older copy, create a targeted `/index.html` and `/` invalidation or wait for cache expiry; review invalidation usage limits/costs. Keep the page's evidence timestamp visible.

---

# Completion checklist and professor demonstration

| Evidence | Day-one target |
|---|---|
| Local integrity tests/demo | Passed |
| Actual cloud archive and completion record | Uploaded, exact VersionIds saved |
| Private bucket, versioning, Governance retention | Configured and inspected |
| Earlier clean cloud recovery after changed upload | Passed on clean EC2 |
| Full file hashes and usable CSV | Passed |
| Single historical file restore | Passed |
| Source IAM negative tests | Passed |
| Independent Object Lock control/retained test | Passed |
| Scheduled execution | At least one actual run observed; restore hourly afterwards |
| Failed-job alarm and actual SNS email | Observed, timestamp saved |
| Missing-job alarm/email | Observed or explicitly pending elapsed-time test |
| 30-day lifecycle rule | Configured; actual transition pending 30 days |
| Recovery trials | Actual reports retained; no duplicated/fabricated results |
| Before-credit cost | Observed when available, otherwise pending |
| Dashboard | Real evidence imported; HTTPS if available |
| EC2 and test identities | Cleaned up after evidence capture |

Suggested 7-minute demonstration:

1. Explain the three-step architecture.
2. Show the original synthetic attendance CSV and clean upload versions.
3. Show source identity restrictions and retained-version evidence.
4. Show the safe changed upload, not real malware.
5. Restore the explicitly selected earlier clean version on EC2.
6. Show passing original hashes and usable CSV, plus single-file recovery.
7. Show IAM and independent lock test evidence separately.
8. Present real timings, email evidence, cost status, and dashboard.
9. State the limitations honestly.

Team workstreams: source/backup; S3/IAM/protection; EC2/recovery/testing; monitoring/dashboard/documentation. Combine as needed for your actual group size. **Every member should be able to explain all three architecture steps and perform one recovery.**

## Important limitations

This is a student prototype on static synthetic files, not an atomic database backup. A matching checksum does not prove source data was clean. A compromised uploader can upload new bad versions and forge source metrics. Governance is bypassable by appropriately authorized administrators. Root compromise of the same laptop can expose recovery keys; a real deployment needs a separate trusted recovery workstation. No full 3-2-1 implementation or production-grade ransomware immunity is claimed.

No automatic expiration is configured. Retention expiry does not delete old backups or test versions. Stop cron when not needed, revoke lab keys, review stored versions and gross billing, and clean up only after evidence has been exported and retention rules reviewed.

## Troubleshooting: stop at the failed step

- `lite.py` not found: folder was copied one level too high/low; run `pwd` and `ls`.
- AWS CLI not found/version 1: run the optional installer and reopen/export PATH.
- `AccessDenied` during backup: inspect bucket/user policies and checksum/lock settings; do not grant S3FullAccess.
- SSH timeout: instance running, public IPv4, correct public subnet/IGW, current My IP rule.
- SSH key denied: correct key pair, copied Linux key mode 400, username `ubuntu`.
- Wrong STS identity on EC2: attach correct role; remove unexpected credential files/environment values, never copy source keys.
- Restore/hash/retention failure: preserve raw error and selected versions; do not mark dashboard verified.
- Dashboard 403: website origin, OAC signing, website-bucket policy and default root object—not backup bucket permissions.

Send sanitized error output when something fails. Never share secret access keys, private SSH keys, passwords or MFA codes.

## Official references

- [Microsoft: WSL files and File Explorer](https://learn.microsoft.com/en-us/windows/wsl/filesystems)
- [AWS CLI installation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [S3 Object Lock configuration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-configure.html)
- [Object Lock and Governance behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [S3 lifecycle transitions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html)
- [CloudWatch missing-data handling](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html)
- [CloudFront OAC for private S3](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
- [CloudFront flat-rate plan restrictions](https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plans.html)
