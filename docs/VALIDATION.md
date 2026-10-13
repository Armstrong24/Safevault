# Validation and scope (v2)

Executed offline by the package author: 53 unit tests (the original 46 plus 7 new automation tests), including an
end-to-end flow against `tests/fakeaws/aws`, a **simulated** AWS CLI that enforces the same permission
boundaries as the IAM policies: local demo → configure → manual and scheduled backups → protection check →
cloud restore → ransomware drill → publish → failed scheduled run kept visible with the last success preserved.
The overlap lock, error scrubbing, public-data guard (no VersionIds, account IDs, keys, bucket names or paths)
and cron line generation are also tested. The dashboard and chatbot JavaScript were exercised in jsdom
(empty, measured and demonstration states; built-in chat answers). The Netlify function was tested with
a mocked Groq API (answer, wrong origin rejected, missing key).

NOT executed by the author, and must be done by the group in its own AWS/Netlify accounts: real
uploads, real IAM denials, real Object Lock retention, real S3 recovery, cron on WSL, Netlify deploy,
Groq answers. Report only what you actually observe.
