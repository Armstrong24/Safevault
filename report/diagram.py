import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
fig, ax = plt.subplots(figsize=(13, 8.6), dpi=200)
ax.set_xlim(0, 130); ax.set_ylim(0, 86); ax.axis("off")
def box(x, y, w, h, title, lines, fc, ec):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5", fc=fc, ec=ec, lw=1.6))
    ax.text(x + w/2, y + h - 2.6, title, ha="center", va="top", fontsize=10.5, weight="bold", color="#111")
    ax.text(x + w/2, y + h - 6.6, "\n".join(lines), ha="center", va="top", fontsize=8.2, color="#222", linespacing=1.35)
def zone(x, y, w, h, label, ec):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5,rounding_size=2", fc="none", ec=ec, lw=1.3, ls="--"))
    ax.text(x + 1.5, y + h - 1.2, label, fontsize=9.5, weight="bold", color=ec, va="top")
def arrow(x1, y1, x2, y2, label="", c="#333", off=(0, 1.2)):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color=c, lw=1.5, mutation_scale=14))
    if label:
        ax.text((x1+x2)/2 + off[0], (y1+y2)/2 + off[1], label, ha="center", va="bottom", fontsize=7.6, color=c, style="italic",
                bbox=dict(fc="white", ec="none", pad=0.6))
ax.text(65, 84.5, "SafeVault: Proposed System Architecture", ha="center", va="top", fontsize=15, weight="bold")
# zones
zone(1, 30, 40, 50, "Institution side (Ubuntu on WSL)", "#1f5fa8")
zone(48, 30, 42, 50, "AWS Cloud (S3, ap-south-1 Mumbai)", "#d97a00")
zone(97, 30, 32, 50, "Monitoring & Presentation", "#2e8b57")
zone(1, 1, 128, 25, "Recovery path (separate least-privilege identity)", "#9b2d30")
blue, bl = "#e8f0fb", "#1f5fa8"; org, ol = "#fff3e0", "#d97a00"; grn, gl = "#e9f6ee", "#2e8b57"; red, rl = "#fbeaea", "#9b2d30"
box(4, 63, 34, 12, "Synthetic institutional data", ["Department CSV records", "(students, staff, finance)"], blue, bl)
box(4, 47, 34, 12, "Scheduler (cron)", ["lite.py schedule --every N", "job lock: no overlapping runs"], blue, bl)
box(4, 32, 34, 12, "Backup engine (safevault.py)", ["snapshot  >  SHA-256 manifest", "compress  >  checksum upload"], blue, bl)
box(52, 55, 34, 20, "Backup bucket (private)", ["Versioning ON", "Object Lock: Governance, 7 days", "SSE-S3 encryption, HTTPS-only", "Block Public Access ON", "archive + completion record"], org, ol)
box(52, 33, 34, 16, "IAM least privilege", ["safevault-lite-uploader:", "PutObject only (no read/list/delete/unlock)", "safevault-lite-recovery:", "read versions + write dashboard JSON"], org, ol)
box(100, 58, 26, 17, "Dashboard bucket", ["one public, sanitized", "dashboard-data.json", "(no keys, IDs or paths)"], grn, gl)
box(100, 33, 26, 20, "Netlify dashboard", ["polls JSON every 60 s", "runs, protection, drill", "Chatbot: Netlify Function", "> Groq API (key server-side)"], grn, gl)
box(4, 4, 36, 16, "Ransomware drill", ["encrypts a disposable copy", "tries delete/read with uploader key", "> expected: AccessDenied"], red, rl)
box(47, 4, 36, 16, "Cloud restore (automation.py)", ["select explicit clean VersionId", "download > validate > safe extract", "to a NEW folder"], red, rl)
box(90, 4, 36, 16, "Verification", ["SHA-256 vs independent baseline", "(5/5 files match)", "CSV usability check > report"], red, rl)
arrow(21, 63, 21, 59.6, "")
arrow(21, 47, 21, 44.6, "")
arrow(38.6, 38, 51.4, 62, "versioned upload\n(uploader key)", bl, off=(-1, 2))
arrow(69, 54.6, 69, 49.6, "")
arrow(86.6, 40, 99.4, 64, "publish results\n(recovery key)", gl, off=(1, 2))
arrow(113, 57.6, 113, 53.6, "")
arrow(65, 32.6, 65, 20.6, "read clean version", rl, off=(8, -6))
arrow(40.6, 12, 46.4, 12, "", rl)
arrow(83.6, 12, 89.4, 12, "", rl)
plt.savefig("architecture.png", bbox_inches="tight", facecolor="white")
