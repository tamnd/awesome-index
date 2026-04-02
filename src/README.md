# awesome-index

Auto-generate an enriched README from [sindresorhus/awesome](https://github.com/sindresorhus/awesome) with live GitHub metadata.

## How it works

1. **Fetch** — Downloads the raw `readme.md` from `sindresorhus/awesome` on GitHub.

2. **Parse** — Walks through the markdown line by line, extracting:
   - Section headings (categories like *Platforms*, *Programming Languages*, etc.)
   - Repo entries matching the `- [Name](https://github.com/owner/repo) - Description` pattern via regex.

3. **Enrich** — For each extracted GitHub repo, fires async requests (via `httpx`) to the GitHub API:
   - `GET /repos/{owner}/{repo}` — stars, forks, language, description, archived status, last push date.
   - `GET /repos/{owner}/{repo}/commits?per_page=1` — total commit count derived from the `Link` pagination header.
   - Runs up to 16 concurrent requests with `asyncio.Semaphore`.
   - Results are cached to `.cache.json` (24h TTL) so re-runs are fast.

4. **Generate** — Produces a `README.md` with:
   - A table of contents with entry counts per category.
   - Per-section tables with columns: Repository, Stars, Last Push, Commits, Description.
   - Human-readable formatting: `59.5k` stars, `2mo ago` timestamps, `~~archived~~` repos.

## Setup

```sh
uv sync
```

## Usage

```sh
uv run awesome-index
```

Or directly:

```sh
uv run python -m awesome_index
```

The generated `README.md` is written to the project root.

## Authentication

The GitHub API has rate limits (60 req/h unauthenticated, 5000 req/h with a token). The tool auto-detects a token from:

1. `GITHUB_TOKEN` environment variable
2. `gh auth token` (GitHub CLI)

## Project structure

```
awesome-index/
├── pyproject.toml              # uv/hatch project config
├── src/
│   ├── README.md               # this file
│   └── awesome_index/
│       ├── __init__.py          # re-exports main()
│       └── generate.py          # all logic: fetch, parse, enrich, render
├── README.md                    # generated output (do not edit)
└── .cache.json                  # API response cache (gitignored)
```
