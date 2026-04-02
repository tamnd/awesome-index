"""
awesome-index: Generate an enriched README.md from sindresorhus/awesome.

Fetches the awesome list, extracts GitHub repo links, enriches each entry
with metadata from the GitHub API (stars, last update, commits, description),
and produces a well-organized, human-readable README.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWESOME_RAW_URL = (
    "https://raw.githubusercontent.com/sindresorhus/awesome/main/readme.md"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_FILE = PROJECT_ROOT / "README.md"
CACHE_FILE = PROJECT_ROOT / ".cache.json"
MAX_CONCURRENCY = 16
CACHE_TTL = 86400  # 24 h
GH_API = "https://api.github.com"

# ---------------------------------------------------------------------------
# GitHub token (prefer GH CLI, then env var)
# ---------------------------------------------------------------------------

import os


def _get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache))


# ---------------------------------------------------------------------------
# Parse the awesome list
# ---------------------------------------------------------------------------

REPO_RE = re.compile(
    r"-\s+\[(?P<name>[^\]]+)\]\((?P<url>https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#]+?)(?:#readme)?)\)\s*(?:-\s*(?P<desc>.+))?"
)
HEADING_RE = re.compile(r"^(?P<hashes>#{1,4})\s+(?P<title>.+)")


def _parse_awesome(markdown: str) -> list[dict]:
    """Return a list of sections, each with heading info and entries."""
    sections: list[dict] = []
    current_section: dict | None = None
    in_contents = False

    for line in markdown.splitlines():
        stripped = line.strip()

        # Skip the table-of-contents block
        if stripped.lower() == "## contents":
            in_contents = True
            continue
        if in_contents:
            if stripped.startswith("## "):
                in_contents = False
            else:
                continue

        heading_m = HEADING_RE.match(stripped)
        if heading_m:
            level = len(heading_m.group("hashes"))
            title = heading_m.group("title").strip()
            if level == 1 or title.lower() in ("contents", "related"):
                current_section = None
                continue
            current_section = {"level": level, "title": title, "entries": []}
            sections.append(current_section)
            continue

        if current_section is None:
            continue

        entry_m = REPO_RE.match(stripped)
        if entry_m:
            current_section["entries"].append(
                {
                    "name": entry_m.group("name"),
                    "url": entry_m.group("url").replace("#readme", ""),
                    "owner": entry_m.group("owner"),
                    "repo": entry_m.group("repo"),
                    "desc": (entry_m.group("desc") or "").strip().rstrip("."),
                    "meta": None,
                }
            )

    return sections


# ---------------------------------------------------------------------------
# Fetch GitHub metadata (async)
# ---------------------------------------------------------------------------


async def _fetch_repo_meta(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    cache: dict,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    key = f"{owner}/{repo}"
    now = time.time()

    if key in cache and now - cache[key].get("_ts", 0) < CACHE_TTL:
        return cache[key]

    async with semaphore:
        try:
            resp = await client.get(f"{GH_API}/repos/{owner}/{repo}", timeout=15)
            if resp.status_code == 403:
                return cache.get(key)
            if resp.status_code != 200:
                return None
            data = resp.json()

            # Commit count via Link header pagination trick
            commit_count = None
            default_branch = data.get("default_branch", "main")
            try:
                cr = await client.get(
                    f"{GH_API}/repos/{owner}/{repo}/commits",
                    params={"sha": default_branch, "per_page": 1},
                    timeout=15,
                )
                if cr.status_code == 200 and "link" in cr.headers:
                    m = re.search(r'page=(\d+)>; rel="last"', cr.headers["link"])
                    if m:
                        commit_count = int(m.group(1))
            except Exception:
                pass

            meta = {
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "description": data.get("description", ""),
                "language": data.get("language", ""),
                "license": (data.get("license") or {}).get("spdx_id", ""),
                "pushed_at": data.get("pushed_at", ""),
                "created_at": data.get("created_at", ""),
                "archived": data.get("archived", False),
                "default_branch": default_branch,
                "topics": data.get("topics", []),
                "commits": commit_count,
                "_ts": now,
            }
            cache[key] = meta
            return meta
        except Exception as exc:
            print(f"  ⚠ {owner}/{repo}: {exc}", file=sys.stderr)
            return cache.get(key)


async def _enrich_sections(
    sections: list[dict], cache: dict, token: str
) -> None:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "awesome-index/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(headers=headers) as client:
        all_entries = [e for sec in sections for e in sec["entries"]]
        total = len(all_entries)

        async def _fetch_one(entry: dict, idx: int) -> None:
            entry["meta"] = await _fetch_repo_meta(
                client, entry["owner"], entry["repo"], cache, semaphore
            )
            if (idx + 1) % 50 == 0 or idx + 1 == total:
                print(f"  [{idx + 1}/{total}] fetched", file=sys.stderr)

        await asyncio.gather(
            *(_fetch_one(e, i) for i, e in enumerate(all_entries))
        )


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _format_stars(n: int | None) -> str:
    if n is None:
        return ""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _activity_bucket(iso: str, archived: bool) -> str:
    """Classify a repo into an activity bucket based on last push date."""
    if archived:
        return "Archived"
    if not iso:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - dt).days
        if days <= 7:
            return "Updated this week"
        if days <= 30:
            return "Updated this month"
        if days <= 180:
            return "Updated in the last 6 months"
        if days <= 365:
            return "Updated in the last year"
        return "Updated more than a year ago"
    except Exception:
        return "Unknown"


# Ordered bucket labels, from most active to least.
BUCKET_ORDER = [
    "Updated this week",
    "Updated this month",
    "Updated in the last 6 months",
    "Updated in the last year",
    "Updated more than a year ago",
    "Unknown",
    "Archived",
]


def _time_ago(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        if days < 1:
            return "today"
        if days < 30:
            return f"{days}d ago"
        if days < 365:
            return f"{days // 30}mo ago"
        return f"{days // 365}y ago"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Generate README
# ---------------------------------------------------------------------------


def _generate_readme(sections: list[dict]) -> str:
    lines: list[str] = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Header with Awesome badge
    lines.append(
        "# Awesome Index "
        "[![Awesome](https://awesome.re/badge.svg)](https://awesome.re)"
    )
    lines.append("")
    lines.append(
        "An auto-generated, enriched index of the "
        "[awesome](https://github.com/sindresorhus/awesome) list "
        "with stars, activity, and commit counts from the GitHub API."
    )
    lines.append("")
    lines.append(f"*Last updated: {now_str}*")
    lines.append("")

    # Contents
    lines.append("## Contents")
    lines.append("")
    for sec in sections:
        indent = "  " * (sec["level"] - 2)
        anchor = re.sub(r"[^\w\s-]", "", sec["title"].lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        lines.append(f"{indent}- [{sec['title']}](#{anchor})")
    lines.append("")

    # Sections
    for sec in sections:
        hashes = "#" * sec["level"]
        lines.append(f"{hashes} {sec['title']}")
        lines.append("")

        if not sec["entries"]:
            continue

        # Group entries into activity buckets
        buckets: dict[str, list[dict]] = {}
        for entry in sec["entries"]:
            meta = entry.get("meta") or {}
            bucket = _activity_bucket(
                meta.get("pushed_at", ""), meta.get("archived", False)
            )
            buckets.setdefault(bucket, []).append(entry)

        # Sort each bucket alphabetically by name for stability
        for bucket_entries in buckets.values():
            bucket_entries.sort(key=lambda e: e["name"].lower())

        # Render each non-empty bucket as a sub-table
        for bucket_label in BUCKET_ORDER:
            bucket_entries = buckets.get(bucket_label)
            if not bucket_entries:
                continue

            sub_hashes = "#" * (sec["level"] + 1)
            lines.append(f"{sub_hashes} {bucket_label}")
            lines.append("")
            lines.append("| Repository | Stars | Last Push | Commits | Description |")
            lines.append("|:---|---:|:---:|---:|:---|")

            for entry in bucket_entries:
                meta = entry["meta"]
                name = entry["name"]
                url = entry["url"]

                if meta:
                    stars = _format_stars(meta.get("stars"))
                    pushed = _time_ago(meta.get("pushed_at", ""))
                    commits = f"{meta['commits']:,}" if meta.get("commits") else ""
                    desc = meta.get("description") or entry["desc"] or ""
                    desc = desc.rstrip()
                    if desc and not desc.endswith((".", "!", "?")):
                        desc += "."
                    if meta.get("archived"):
                        name = f"~~{name}~~"
                    lang = f" `{meta['language']}`" if meta.get("language") else ""
                    lines.append(
                        f"| [{name}]({url}){lang} | {stars} | {pushed} | {commits} | {desc} |"
                    )
                else:
                    desc = entry["desc"] or ""
                    if desc and not desc.endswith((".", "!", "?")):
                        desc += "."
                    lines.append(f"| [{name}]({url}) | | | | {desc} |")

            lines.append("")

    # Footnotes
    lines.append("## Footnotes")
    lines.append("")
    lines.append(
        f"This list is generated automatically from the "
        f"[sindresorhus/awesome](https://github.com/sindresorhus/awesome) "
        f"source list. Metadata is fetched from the GitHub API and refreshed daily."
    )
    lines.append("")
    lines.append(f"Last generated on {now_str}.")
    lines.append("")

    # Contributing
    lines.append("## Contributing")
    lines.append("")
    lines.append(
        "This index is auto-generated. To suggest changes to the source list, "
        "visit [sindresorhus/awesome](https://github.com/sindresorhus/awesome). "
        "To improve the generator itself, see the "
        "[source code](src/)."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    asyncio.run(_async_main())


async def _async_main() -> None:
    token = _get_github_token()

    print("⏳ Fetching awesome list…", file=sys.stderr)
    async with httpx.AsyncClient() as client:
        resp = await client.get(AWESOME_RAW_URL, timeout=30)
        resp.raise_for_status()
        markdown = resp.text

    print("📋 Parsing entries…", file=sys.stderr)
    sections = _parse_awesome(markdown)
    total_entries = sum(len(s["entries"]) for s in sections)
    print(f"   Found {len(sections)} sections, {total_entries} repos", file=sys.stderr)

    cache = _load_cache()

    print("🔍 Fetching GitHub metadata…", file=sys.stderr)
    await _enrich_sections(sections, cache, token)
    _save_cache(cache)

    print("📝 Generating README.md…", file=sys.stderr)
    readme = _generate_readme(sections)
    OUTPUT_FILE.write_text(readme)

    print(f"✅ Done! Written to {OUTPUT_FILE}", file=sys.stderr)
