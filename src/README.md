# awesome-index

Auto-generate an enriched README from [sindresorhus/awesome](https://github.com/sindresorhus/awesome) with live GitHub metadata.

The [awesome list](https://github.com/sindresorhus/awesome) is one of the most popular resources on GitHub, but it's just a flat list of links. This tool turns it into a proper index with stars, activity, commit counts, and descriptions pulled straight from the GitHub API.

## How it works

The whole pipeline runs in four steps:

**1. Fetch the source list**

It grabs the raw `readme.md` from `sindresorhus/awesome`. That file has around 700 links to awesome-* repos, organized under category headings.

**2. Parse the markdown**

The parser walks through each line looking for two things: section headings (like *Platforms*, *Programming Languages*) and repo entries. Entries follow the pattern `- [Name](https://github.com/owner/repo) - Description`, which gets picked apart with a regex.

**3. Enrich with GitHub metadata**

For every repo it found, the tool hits the GitHub API to pull in the good stuff:

- Stars, forks, language, description, whether it's archived, and when it was last pushed.
- Commit count, using a neat trick: request one commit per page and read the total from the `Link` pagination header.

All of this runs concurrently (up to 16 requests at a time using `httpx` and `asyncio`). API responses are cached locally in `.cache.json` for 24 hours, so re-runs are fast and won't burn through your rate limit.

**4. Generate the README**

Everything gets assembled into a clean `README.md` at the project root. Each category becomes a table with columns for the repo name, star count (formatted like `59.5k`), last push time (`2mo ago`), commit count, and description. Archived repos are shown with strikethrough. A table of contents at the top lists every category with its entry count.

A GitHub Actions workflow runs this daily and commits the result, so the index stays fresh without any manual work.

## Setup

```sh
uv sync
```

## Usage

```sh
uv run awesome-index
```

Or:

```sh
uv run python -m awesome_index
```

The output goes to `README.md` in the project root.

## Authentication

The GitHub API allows 60 requests per hour without a token, and 5,000 with one. Since the tool makes about 1,400 requests per run (two per repo), you'll need a token. It picks one up automatically from:

1. The `GITHUB_TOKEN` environment variable
2. The GitHub CLI (`gh auth token`)

## Project structure

```
awesome-index/
├── pyproject.toml              # project config (uv + hatch)
├── src/
│   ├── README.md               # this file
│   └── awesome_index/
│       ├── __init__.py          # package entry point
│       └── generate.py          # fetch, parse, enrich, render
├── .github/
│   └── workflows/
│       └── update.yml          # daily scheduled regeneration
├── README.md                   # generated output (do not edit by hand)
└── .cache.json                 # API response cache (gitignored)
```
