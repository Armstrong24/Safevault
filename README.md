# SafeVault

**Automated, Ransomware-Resilient Backup of Institutional Data to Cloud Storage**

Cloud Computing group project (4 members). SafeVault takes automatic backups of a college department's data, checks every file with SHA-256 hashes, stores the backups in a locked and versioned Amazon S3 bucket, and proves that a clean copy can be restored, even if the computer that made the backup is attacked.

**Live dashboard:** https://dancing-gumdrop-8b4c47.netlify.app

![SafeVault architecture](report/architecture.png)

---

## The problem

Department data is often backed up by hand to pen drives or shared folders connected to the same computers that hold the original data. Ransomware encrypts or deletes those backups too. Backups are rarely tested, and there is no easy way to prove a backup is complete, unchanged and restorable.

## What SafeVault does

| Feature | Command | What it proves |
|---|---|---|
| Scheduled backups (cron on Ubuntu/WSL) | `python3 lite.py schedule --every 5 --install` | Run log with start, finish, outcome; no overlapping runs |
| One backup | `python3 lite.py run-backup` | Compressed archive + SHA-256 manifest uploaded to S3 (versioned, Object Lock) |
| Verify protection | `python3 lite.py verify-protection` | Versioning, Object Lock, retention, encryption and public-access block, read live from AWS |
| Cloud recovery | `python3 lite.py cloud-restore` | A chosen clean version is downloaded, restored to a new folder and matched 5/5 by hash against a separate baseline |
| Safe ransomware drill | `python3 lite.py ransomware-drill` | A disposable copy is encrypted; the uploader key is denied read, list, delete and unlock; recovery is verified |
| Status | `python3 lite.py status` | Summary of runs and checks in the terminal |
| Publish results | automatic, or `python3 lite.py publish` | Sanitized `dashboard-data.json` for the dashboard (no file names, keys or private data) |
| Dashboard + chatbot | `website/index.html`, `netlify/functions/chat.mjs` | Netlify page refreshing every 60 s; Groq chatbot behind a server-side function |

## Architecture

```
cron (Ubuntu/WSL) -> lite.py run-backup -> safevault.py engine -> AWS CLI (uploader: upload only)
      -> private S3 backup bucket  [Versioning, Object Lock Governance 7 days, SSE-S3, HTTPS-only, Block Public Access]

lite.py cloud-restore / ransomware-drill -> AWS CLI (recovery user: read backups only)
      -> download exact versions -> validate -> extract to a NEW folder -> compare hashes with clean baseline

every command -> sanitized results -> public dashboard bucket (one JSON file)
      -> Netlify dashboard (polls every 60 s) + chatbot (Netlify Function -> Groq, key kept server-side)
```

There is no conventional database. The dashboard is static HTML that reads one sanitized JSON file from S3. The only backend is the Netlify Function for the chatbot, and its API key lives in Netlify environment variables, never in the browser.

## Security design

- **Off-site copies** in Amazon S3, Mumbai region (`ap-south-1`).
- **Versioning + Object Lock (Governance, 7 days):** old versions cannot be overwritten or deleted during retention.
- **Least-privilege IAM:** the uploader can only upload (it cannot read, list, delete or unlock backups); a separate recovery user can only read.
- **Integrity:** SHA-256 manifest for every file, checked again after restore.
- **Encryption:** SSE-S3 at rest, HTTPS-only bucket policy in transit.
- **Privacy:** the dashboard shows only sanitized results.

## Tech stack

Python 3 · Ubuntu on WSL · cron · AWS CLI · Amazon S3 · AWS IAM · Netlify (static hosting + Functions, Node.js) · Groq API

## Repository structure

```
Safevault/
├── safevault.py            backup/restore engine: snapshot, SHA-256 manifest, versioned upload, safe extract
├── lite.py                 lab commands (entry point for all commands)
├── automation.py           scheduling, cloud restore, protection check, ransomware drill, dashboard publisher
├── website/index.html      dashboard
├── netlify/functions/chat.mjs + netlify.toml   chatbot proxy
├── scripts/install_cli.py  AWS CLI install helper
├── tests/                  53 offline tests + simulated AWS CLI (tests/fakeaws/aws)
├── SETUP_TODAY.md          step-by-step setup
├── PRESENTATION.md         demo running order
├── docs/                   group explanation, validation, test output, original EC2 guide
│   └── presentation/       presentation script and setup PDFs
└── report/                 project report (.docx), architecture diagram and their generator scripts
```

## Getting started

```bash
# Ubuntu / WSL, Python 3 and AWS CLI configured with the SafeVault IAM users
git clone https://github.com/Armstrong24/Safevault.git
cd Safevault
python3 -m unittest discover -s tests     # 53 offline tests, no AWS needed
sudo service cron start
python3 lite.py schedule --every 5 --install
python3 lite.py status
```

Full setup steps are in [`SETUP_TODAY.md`](SETUP_TODAY.md) and the demo order is in [`PRESENTATION.md`](PRESENTATION.md).

## Testing

- 53 automated offline tests, including an end-to-end run against a simulated AWS CLI.
- Real runs on our AWS account: protection verified, cloud restore matched 5/5 files by hash, ransomware drill passed.

## Honest scope

A tested prototype using **synthetic institutional data only**. Scheduling works only while the laptop and WSL are on. Governance-mode retention can be bypassed by a specially authorised administrator. Recovery runs on the same laptop using a separate read-only identity; a production system would restore on a separate clean machine. SafeVault is ransomware-resilient under the threat model we tested, **not ransomware-proof**.

## Future scope

- Run backups on AWS Lambda or EC2 so they work when the laptop is off
- CloudWatch and SNS alerts when a backup fails
- Compliance-mode Object Lock for critical data
- Copies in another region or account (3-2-1 rule)
- S3 Glacier for old backups
- Database (MySQL) backups
- Detect suspicious file changes before backup
- Restore on a separate clean VM with MFA
- Multiple departments with separate dashboards

## Team

Cloud Computing group project led by Arnav Kale (4 members).
