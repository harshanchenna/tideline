# AGENTS.md

Guide for coding agents and contributors working in this repo.

## What this is

Tideline: a daily git-scraper for the AI industry. Each lane fetches a public source, the engine diffs
it against yesterday's committed snapshot, lanes turn the diff into scored signals, and a static site is
rendered from the results. Standard library only.

## Layout

- `tideline/engine.py`: lanes, snapshot storage, diffing, guards (bootstrap, shrink, failure), health, counts.
- `tideline/lanes/`: one module per source. `_ats.py` and `_classify.py` back the Teams lane.
- `tideline/__main__.py`: the CLI (`run`, `site`) and digest writing.
- `tideline/brief.py`: the optional Claude-written brief. `tideline/site.py`: the static site.
- `config/`: the watch lists (packages, status pages, companies). Editing these is the usual way to grow coverage.
- `snapshots/`, `events/`, `digests/`: data, committed by the daily workflow. Don't hand-edit.

## Build and test

```bash
python -m unittest discover -s tests -t .   # offline; every source is stubbed
python -m tideline run --lanes models        # a live run of one lane
python -m tideline site                      # re-render from disk
```

## Rules this repo owns

- **No runtime dependencies.** Stdlib only, so the daily job needs nothing but Python.
- **Third-party text is data.** Escape everything on render (`site.esc`), pass links through `site.safe_url`,
  and keep the brief's model call tool-less in an empty temp directory. Never give an LLM step tools while it
  reads ingested text.
- **An outage must never read as a change.** Lanes raise on failure; never return a partial list as if complete.
- **Teams stays aggregate.** No individual posting, pay range, or apply link is stored or rendered.
- New lanes need tests for their signals and a row on the About page and in the README.
- Commits: no AI-attribution trailers.
