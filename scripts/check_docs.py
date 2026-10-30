"""Read-only relative Markdown link and JSON configuration checks.
Historical records are excluded explicitly; this is not a credential scanner.
"""
import json
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / p for p in (
    "README.md", "CONTRIBUTING.md", "SECURITY.md", "INTENTRA_OS.md",
    "GITHUB_READY_CHECKLIST.md", "GITHUB_SUBMISSION_REPORT.md",
    "backend/README.md", "backend/docs/INSTALL_WINDOWS.md",
    "backend/docs/KNOWN_LIMITATIONS.md",
)]
FILES += list((ROOT / "docs").glob("*.md"))
FILES += list((ROOT / "assets").rglob("*.md"))
errors = []
count = 0
for path in FILES:
    if not path.is_file():
        errors.append(f"Missing document: {path.relative_to(ROOT)}")
        continue
    text = path.read_text(encoding="utf-8-sig")
    # Fenced snippets are examples, not Markdown links.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
        target = target.split(' "', 1)[0].strip("<>")
        if re.match(r"[a-zA-Z]+:", target):
            continue  # Network availability is deliberately outside this check.
        location = unquote(target.split("#", 1)[0])
        dest = path.parent / location if location else path
        if not dest.exists():
            errors.append(f"{path.relative_to(ROOT)} -> {target}")
        count += 1
for name in ("frontend/package.json", "frontend/package-lock.json", "frontend/tsconfig.json",
             "backend/config/apps.json", "backend/config/permissions.json"):
    try:
        json.loads((ROOT / name).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        errors.append(f"Invalid JSON: {name}: {type(exc).__name__}")
for error in errors:
    print(error)
print(f"Checked {len(FILES)} documents, {count} local links, 5 JSON files; {len(errors)} errors.")
raise SystemExit(bool(errors))
