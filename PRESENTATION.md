# SafeVault: 8-minute presentation plan (4 members)

**One-line pitch:** *Ransomware encrypts your files and then tries to delete your backups. SafeVault
backs up automatically to Amazon S3, makes old clean versions undeletable, and proves it can recover them.
Every result appears live on our dashboard.*

Before you start: the laptop is on, the Ubuntu window is open, the cron schedule has been running at least
10 minutes, the dashboard is open in a browser tab, and the AWS Console tab shows **only** the dashboard bucket.
Never show `.aws`, access keys, the Groq key, billing or IAM key pages.

### 1. Member 1: Problem & architecture (1.5 min)
Problem statement: ransomware targets backups too. Walk through the flow: **cron → Python engine → upload-only IAM user → private S3 bucket (Versioning + Object Lock + SSE-S3 + HTTPS-only) → read-only recovery user → verified restore → sanitized JSON → Netlify dashboard**. Why S3: very high durability, versioning, WORM lock, pay-per-use.

### 2. Member 2: Automation (2 min)
Run `python3 lite.py status` to show the unattended scheduled runs with their times. On the dashboard, show the **Scheduled backup runs** table and **Last successful backup**. Explain the no-overlap lock, the run log, and that failures stay visible without hiding the last success. Mention the WSL limit honestly.

### 3. Member 3: Protection & recovery (2 min)
Run `python3 lite.py verify-protection`. It reads Versioning, Object Lock (Governance 7 days), encryption and public-access block from AWS. Then run `python3 lite.py cloud-restore`. It downloads the **selected clean version** from S3, restores it into a new folder, and matches 5/5 SHA-256 hashes against an independent baseline. Show the protection panel.

### 4. Member 4: Live ransomware drill + dashboard (2.5 min)
Run `python3 lite.py ransomware-drill` (type DRILL). Narrate the 4 steps: files encrypted, 0/5 readable, the stolen key gets **AccessDenied** 4 times, then recovery of 5/5 verified. Click **Refresh from S3**: the drill panel and checklist update. Ask the chatbot "Did the drill pass?"

### Close: everyone (30 s)
Limitations, in one breath: synthetic files, laptop must be on, Governance can be bypassed by an authorised admin, recovery runs on the same laptop. "Ransomware-resilient under our tested threat model, not ransomware-proof."

## Likely viva questions (everyone should know these)

- **Why not just upload timestamped files?** Without Versioning and Object Lock, anyone with delete access can wipe them. Here each version is retained and the uploader cannot delete at all.
- **IAM deny vs Object Lock?** IAM stops *this identity*. Object Lock stops deletion of a retained version by *any* identity that lacks governance bypass. We show both separately.
- **Why restore an older version and not the latest?** The latest upload may already contain encrypted files. We pin the clean VersionId.
- **Governance vs Compliance?** Governance can be bypassed with a special permission (good for a lab). Compliance cannot be shortened by anyone, even root, so we didn't use it on a student account.
- **How do you know the restore is correct?** The archive SHA-256 is checked against the completion record, every file is checked against the manifest, and everything is compared with a baseline saved separately before any damage.
- **Is the dashboard live?** It refreshes every 60 seconds from S3 (polling, not streaming). Each backup or test republishes the JSON automatically.
- **Where is the Groq key?** In a Netlify environment variable, used only by a server-side function. The browser never sees it.
- **Cost?** A few small objects in S3 in Mumbai, plus free-tier Netlify and Groq. Well under a dollar for the project period.

## What makes this stand out

1. It **proves** recovery instead of just claiming backups exist.
2. A **live attack drill** on the projector, with real AccessDenied results from AWS.
3. **Least privilege**: two narrow identities, no admin keys on the laptop job.
4. An **honest dashboard** that shows failures, stale data and limitations, plus an AI assistant grounded in real results.
