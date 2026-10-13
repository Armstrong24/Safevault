# Explain SafeVault Lite as a group

## Thirty-second introduction

“Our project protects synthetic department files against loss and source-side corruption. Ubuntu automatically compresses files and uploads backups to private S3 storage. Versioning keeps historical object versions; configured Object Lock prevents permanent deletion of retained versions by identities without bypass permission. A different identity restores a selected clean version onto EC2. We verify both original hashes and usable CSV contents, then show the actual evidence.”

Only say a test passed after running it. Replace “we verify” with “we plan to verify” until it is executed.

## What we configured versus what the program does

- Group configuration: AWS bucket settings, reviewed IAM policies, recovery role, EC2, alarms, SNS subscription, lifecycle, cron and website hosting.
- Software: captures static files, generates a manifest, compresses, calculates checksums, uploads archive/completion versions, retrieves chosen versions, validates safe archive paths, verifies files and emits reports.
- AWS service enforcement: IAM evaluates permissions; S3 stores versions/enforces retention and checksum validation; lifecycle ages eligible objects; CloudWatch evaluates metrics; SNS delivers confirmed email notifications.
- Dashboard: presents a sanitized snapshot of measured evidence, not a live recovery control panel.

Be honest about AI assistance and authorship according to the professor's rules. Demonstrate understanding and your group's actual configuration/testing contribution rather than claiming you manually wrote code you did not.

## Questions everyone should answer

**Why not make the bucket public?** Archives are private; a separate website contains only sanitized results.

**Why both Versioning and Object Lock?** Versioning preserves different versions. Retention protects the selected versions from permanent deletion while active; version awareness is necessary to recover earlier bytes after newer bad uploads.

**Why upload-only credentials?** A stolen uploader key can upload new versions but does not have backup read/delete/retention-administration permissions. Never put an administrator key on the backup source.

**Why a separate recovery role?** It can retrieve backup versions without giving the source read access. It does not modify backups.

**What does SHA-256 prove?** Exact-byte integrity against the selected reference. It does not detect whether the source was already corrupted before capture. The independently saved original baseline provides the separate known-good comparison.

**What is in a complete run?** A compressed archive plus a completion record that names its exact archive key, VersionId, hash, file count and data-cutoff/upload times. An archive without a completion record is not advertised as a complete recovery point.

**Why not select latest automatically?** A newer backup may contain already-corrupted source data. Restore an explicitly selected clean completion-record version and its referenced archive VersionId.

**How did we prove IAM separately from retention?** Source read/list/delete requests were denied. Separately, the test identity could delete an expired same-bucket control version but could not delete the actively retained version; it had no Governance bypass.

**What is RPO versus RTO?** RPO is a target acceptable data-loss window; measured incident recovery-point age is incident time minus selected clean data cutoff. RTO is a target acceptable recovery duration. We separately record transfer/extract/verify and a defined launch-to-usable exercise duration. Targets and measurements are not interchangeable.

**Why is the transition pending?** The lifecycle rule exists now, but eligible objects must actually age 30 days. A manual storage-class change would not prove automatic lifecycle.

**What stops scheduled backups?** Sleeping Windows/WSL, network failure, credential/permission issues, source problems and disk constraints. Source-side success metrics can be forged by a compromised source.

**What are the limitations?** Static synthetic files, no atomic database snapshot, temporary lab authentication, Governance bypass for authorized administrators, same-laptop trust limitations, no guaranteed cost cutoff, no full 3-2-1 or ransomware-proof claim.

## Demonstration responsibilities

Divide into source/backup, storage/protection, recovery/testing, and monitoring/dashboard/evidence workstreams. Combine based on the actual group size. Everyone rehearses the whole recovery at least once and can explain the three main boxes. Save raw output and evidence, not just a polished screenshot.
