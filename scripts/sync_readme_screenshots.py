"""
For each GitHub repo in ## Open Source Examples, fetch the repo README from
raw.githubusercontent.com (default branch via HEAD) and inspect it for images.

If README has no usable image, the Screenshot cell is left unchanged.
If it does, the cell is set to the first non-badge image (markdown or <img>),
as <img src="ABSOLUTE_URL" width="200" alt="">.

Badge images (shields.io, etc.) are skipped so npm/build badges are not used
as the screenshot.

Re-run after rate limits: py -3 scripts/sync_readme_screenshots.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com"
HTTP_UA = {"User-Agent": "CesiumJSResource-readme-screenshots"}
API_ROOT = "https://api.github.com"
API_UA = {"User-Agent": "CesiumJSResource-readme-screenshots", "Accept": "application/vnd.github+json"}


def raw_get_text(url: str) -> str | None:
    req = urllib.request.Request(url, headers=HTTP_UA)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        sys.stderr.write(f"HTTP {e.code} for {url}: {e.reason}\n")
        return None
    except Exception as ex:
        sys.stderr.write(f"Error GET {url}: {ex}\n")
        return None


def api_get_readme(owner: str, repo: str) -> tuple[str, str] | None:
    """Optional GitHub API fallback when GITHUB_TOKEN is set (returns md, branch)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    headers = dict(API_UA)
    headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_ROOT}/repos/{owner}/{repo}/readme", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None
    branch = "main"
    download_url = data.get("download_url") or ""
    m = re.search(r"githubusercontent\.com/[^/]+/[^/]+/([^/]+)/", download_url, re.I)
    if m:
        branch = urllib.parse.unquote(m.group(1))
    content = data.get("content")
    if not content:
        return None
    md = base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="replace")
    return (md, branch)


def owner_repo_from_github_url(url: str) -> tuple[str, str] | None:
    url = url.strip()
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/?", url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def blob_to_raw(url: str) -> str:
    m = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)",
        url,
        re.I,
    )
    if m:
        o, r, br, path = m.groups()
        path = urllib.parse.unquote(path)
        return f"https://raw.githubusercontent.com/{o}/{r}/{br}/{path}"
    return url


def resolve_img_src(src: str, owner: str, repo: str, branch: str) -> str:
    src = src.strip().strip("<>").strip()
    if not src:
        return src
    if src.startswith(("http://", "https://")):
        return blob_to_raw(src)
    if src.startswith("//"):
        return blob_to_raw("https:" + src)
    path = src.strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    path = urllib.parse.unquote(path)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def is_badge_like(url: str) -> bool:
    u = url.lower()
    if "shields.io" in u or "img.shields.io" in u:
        return True
    if "badgen.net" in u:
        return True
    if "travis-ci.com" in u or "travis-ci.org" in u:
        return True
    if "codecov.io" in u:
        return True
    if "badge.svg" in u and ("github.com" in u or "gitlab.com" in u):
        return True
    if "sonarcloud.io" in u or "deepsource.io" in u:
        return True
    return False


def collect_readme_image_urls(readme_md: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", readme_md):
        url = m.group(1).strip().split()[0].strip('"').strip("'")
        if url and not url.lower().startswith("data:"):
            candidates.append((m.start(), url))
    for m in re.finditer(
        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']',
        readme_md,
        re.I,
    ):
        url = m.group(1).strip()
        if url and not url.lower().startswith("data:"):
            candidates.append((m.start(), url))
    candidates.sort(key=lambda x: x[0])
    return candidates


def first_readme_image(readme_md: str, owner: str, repo: str, branch: str) -> str | None:
    """First non-badge image URL, resolved. None if no images or only badges / unusable."""
    candidates = collect_readme_image_urls(readme_md)
    if not candidates:
        return None
    good = [(pos, u) for pos, u in candidates if not is_badge_like(u)]
    if not good:
        return None
    _, url = good[0]
    return resolve_img_src(url, owner, repo, branch)


def fetch_readme_md(owner: str, repo: str) -> tuple[str | None, str]:
    """README text via raw.githubusercontent.com (HEAD); optional API if token + raw miss."""
    for name in ("README.md", "Readme.md", "readme.md"):
        url = f"{RAW}/{owner}/{repo}/HEAD/{name}"
        text = raw_get_text(url)
        if text:
            return text, "HEAD"
    api_result = api_get_readme(owner, repo)
    if api_result:
        return api_result
    return None, "HEAD"


def parse_open_source_table(text: str) -> tuple[str, list[dict], str] | None:
    marker = "## Open Source Examples"
    start = text.find(marker)
    if start == -1:
        return None
    rest_from_marker = text[start:]
    end_rel = rest_from_marker.find("\n### ")
    if end_rel == -1:
        section = rest_from_marker
        after = ""
    else:
        section = rest_from_marker[:end_rel]
        after = rest_from_marker[end_rel:]

    lines = section.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("| Link") and "Screenshot" in line:
            header_idx = i
            break
    if header_idx is None:
        return None

    rows: list[dict] = []
    for line in lines[header_idx + 2 :]:
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        link = parts[1]
        if not link.startswith("http"):
            continue
        rows.append(
            {
                "line": line,
                "link": link,
                "accessed": parts[2],
                "star": parts[3],
                "screenshot": parts[4],
            }
        )

    before = text[:start]
    return before, rows, after


def pad_screenshot_cell(inner: str, min_width: int) -> str:
    inner = inner.rstrip()
    if len(inner) < min_width:
        return inner + " " * (min_width - len(inner))
    return inner


def format_row(link: str, accessed: str, star: str, screenshot: str, col_widths: tuple[int, int, int, int]) -> str:
    w_link, w_acc, w_star, w_sh = col_widths
    return (
        f"| {link.ljust(w_link)} | {accessed.ljust(w_acc)} | {star.ljust(w_star)} | "
        f"{pad_screenshot_cell(screenshot, w_sh)} |"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    readme_path = repo_root / "README.md"
    with open(readme_path, encoding="utf-8") as f:
        text = f.read()

    parsed = parse_open_source_table(text)
    if not parsed:
        print("Could not find Open Source Examples table", file=sys.stderr)
        return 1
    before, rows, after = parsed

    w_link = max(len(r["link"]) for r in rows)
    w_acc = max(len(r["accessed"]) for r in rows)
    w_star = max(len(r["star"]) for r in rows)
    w_sh = 70

    updated_rows: list[tuple[str, str, str, str]] = []
    for r in rows:
        link = r["link"]
        shot = r["screenshot"]
        or_ = owner_repo_from_github_url(link)
        if or_:
            owner, repo = or_
            time.sleep(0.12)
            md, branch = fetch_readme_md(owner, repo)
            if md:
                img = first_readme_image(md, owner, repo, branch)
                if img:
                    shot = f'<img src="{img}" width="200" alt="">'
                    print(f"OK  {owner}/{repo} -> {img[:80]}...")
                elif collect_readme_image_urls(md):
                    shot = ""
                    print(f"SKIP {owner}/{repo} (README images are badges only)")
                else:
                    print(f"SKIP {owner}/{repo} (no image in README)")
            else:
                print(f"SKIP {owner}/{repo} (no README)")
        updated_rows.append((link, r["accessed"], r["star"], shot))

    w_sh = max(w_sh, max(len(s[3]) for s in updated_rows))

    new_body_lines = [
        "## Open Source Examples",
        "",
        "| Link                                                             | Accessed | Star | Screenshot                                                               |",
        "| ---------------------------------------------------------------- | -------- | ---- | ------------------------------------------------------------------------ |",
    ]
    for link, acc, star, shot in updated_rows:
        new_body_lines.append(format_row(link, acc, star, shot, (w_link, w_acc, w_star, w_sh)))

    new_text = before.rstrip() + "\n\n" + "\n".join(new_body_lines) + "\n" + after

    with open(readme_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)

    print("Updated", readme_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
