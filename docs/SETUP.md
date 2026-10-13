# SafeVault setup guide

Prerequisites: Ubuntu on WSL (or Linux) with Python 3 and AWS CLI, an S3 backup bucket created with
Versioning and Object Lock (`s3-bucket-fcc-project`, region `ap-south-1`), the `safevault-lite-uploader`
IAM user with its CLI profile, and a Netlify site with a public `dashboard-data.json` bucket.
No EC2, CloudWatch, SNS or CloudFront is needed.

Run every `python3 ...` command in **Ubuntu (WSL)**, not PowerShell.

---

## Step 1: Get the code and run the offline tests

```bash
git clone https://github.com/Armstrong24/Safevault.git
cd Safevault
python3 -m unittest discover -s tests
python3 lite.py local-demo      # first time only: creates synthetic data and the clean hash baseline
```
Expected: `OK` (53 tests).

## Step 2: Create ONE new IAM user (5 min, AWS Console)

```bash
python3 lite.py policies
cat ~/safevault-lite-policies/recovery-user.json
```

In **IAM → Users → Create user**:
- Name: `safevault-lite-recovery`. No Console access. No managed policies.
- **Permissions → Add permissions → Create inline policy → JSON**: paste the printed JSON. Name it `SafeVaultLiteRecoveryPolicy`.
- **Security credentials → Create access key → Command Line Interface (CLI)**.

Back in Ubuntu:
```bash
aws configure --profile safevault-lite-recovery     # paste the key + secret, region ap-south-1, output json
aws sts get-caller-identity --profile safevault-lite-recovery
```
The ARN must end with `user/safevault-lite-recovery`.

What this user can do: **read** backup versions and protection settings, and **write only** `dashboard-data.json`.
It cannot upload, delete or unlock backups. The uploader still can't read, list or delete anything.

## Step 3: Check the dashboard bucket stays public for one file (2 min)

In **S3 → safevault-dashboard-8b4c47 → Permissions → Bucket policy**, there should be a statement
allowing public `s3:GetObject` on `arn:aws:s3:::safevault-dashboard-8b4c47/dashboard-data.json`.
Compare it with `~/safevault-lite-policies/dashboard-bucket-policy-reference.json`.
If you made the file public with an **object ACL** instead, switch to this bucket policy. Each upload
replaces the object, which would reset an ACL. Keep your CORS rule for the Netlify site.

## Step 4: First real backup and verified recovery (5 min)

```bash
python3 lite.py configure-source          # skip if it says the same config already exists
python3 lite.py run-backup --select-clean
python3 lite.py verify-protection
python3 lite.py cloud-restore
```
Expected lines:
- `BACKUP SUCCEEDED ... selected as clean recovery point`
- `PROTECTION VERIFIED`, which shows Versioning, Object Lock, retention, encryption and public access, all read from AWS
- `RECOVERY PASSED from S3: 5/5 files match the independent clean SHA-256 baseline`
- `DASHBOARD UPDATED ... publicly readable.` after each command

Open https://dancing-gumdrop-8b4c47.netlify.app. You'll see the real results once you finish Step 7 (the new page), or straight away if your current page already reads `data_mode: measured`.

If `verify-protection` says Object Lock is not enabled, your bucket was created without it. Either note this, or create a new bucket with Object Lock and change `BUCKET` in `lite.py`.

## Step 5: Turn on automatic backups (3 min + 10 min waiting)

```bash
sudo apt-get update && sudo apt-get install -y cron    # only if cron is missing
sudo service cron start
python3 lite.py schedule --every 5 --install           # type SCHEDULE
```
**Keep the Ubuntu window open and the laptop awake.** After about 10 minutes:
```bash
python3 lite.py status
```
You need **at least 2 scheduled runs** marked `success`. Each run updates the dashboard by itself.

To slow it down or stop it later: Only the SafeVault line is touched:
```bash
python3 lite.py schedule --every 60 --install    # hourly
python3 lite.py schedule --remove                # stop
```

## Step 6: Ransomware drill (2 min)

```bash
python3 lite.py ransomware-drill      # type DRILL
```
It scrambles a **disposable copy** (no malware) and tries to read, list, delete and unlock backups with the
uploader key. All four must say `AccessDenied`. Then it restores the clean backup from S3 and verifies 5/5 files.
Your real source folder and your cloud backups are not modified.

## Step 7: Deploy the new dashboard + chatbot (10 to 15 min)

1. Get a free API key at https://console.groq.com → API Keys.
2. In **Netlify → your project → Project configuration → Environment variables → Add a variable**:
   key `GROQ_API_KEY`, value = your key, scope **Functions**, mark it secret.
   Never put the key in `index.html` or anywhere in the code.
3. Deploy from Ubuntu. This needs Node 18 or newer. Check with `node -v`. If it's missing or old:
   ```bash
   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
   ```
   Then:
   ```bash
   cd Safevault
   npx netlify-cli@latest login            # opens a browser link
   npx netlify-cli@latest link             # choose your existing site dancing-gumdrop-8b4c47
   npx netlify-cli@latest deploy --prod    # publishes website/ + the chat function (netlify.toml)
   ```
4. Open the site and click the chat button in the bottom-right corner. The header says **AI answers · Groq** once it works.

**Short on time?** Drag the `website` folder onto your site's **Deploys** page in Netlify. The dashboard
works, and the chatbot still answers from built-in SafeVault knowledge and your live results. Only the
Groq AI mode needs the CLI deploy, because drag-and-drop doesn't deploy functions.

---

## If something fails

| Message | Fix |
|---|---|
| `Wrong AWS identity` | The profile name is wrong, or `aws configure` was run without `--profile`. |
| `AccessDenied` on `run-backup` | Check the uploader inline policy and that the bucket allows checksum uploads. Never give it S3FullAccess. |
| `DASHBOARD PUBLISH FAILED` | Check the recovery user policy and the dashboard bucket name. Your backup still succeeded and was recorded. |
| `public read-back check did not match` / 403 | Step 3, the bucket policy. |
| Scheduled runs not appearing | `sudo service cron start`, keep WSL open, then `cat ~/.local/state/safevault-lite/scheduled.log`. |
| `Retention NOT verified` | Object Lock isn't on for that version, or the clean backup is older than 7 days. Run `run-backup --select-clean` again. |

Never paste access keys, the Groq key or `.aws` files into chat, screenshots or public places.
