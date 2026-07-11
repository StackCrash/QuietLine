# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.

## What this is

QuietLine is a **Claude Cowork skill** — not a standalone application. It ships as:

- `SKILL.md` — the lean orchestrator Claude reads: phases, routing, safety gates. Must stay
  under 500 lines (`wc -l SKILL.md`).
- `scripts/` — deterministic Python, executed rather than reasoned about (discovery, dedup,
  URL verification, the case store, deadline math, email rendering, dashboard rendering).
- `references/` — per-mechanism and per-jurisdiction playbooks, loaded on demand
  (progressive disclosure). `SKILL.md` reads only the file relevant to the current broker/user.
- `data/` — `brokers.json` and `jurisdictions.json`. **Never load these wholesale into
  context** — they exist to be queried by scripts.
- `assets/templates/` — email and dashboard Markdown templates.
- `schemas/` — `case_record.json`, the case-store schema.
- `evals/` — the test suite (`evals.json` fixtures + `run_evals.py` runner).

Read [README.md](README.md) for the full architecture, phase-by-phase behavior, and design
principles before making structural changes.

## Environment — use UV, always

This project is managed with [UV](https://docs.astral.sh/uv/). Do not invoke `pip` or a bare
`python`/`python3` directly — always go through `uv run` so the interpreter, lockfile, and
dependency set stay reproducible.

```bash
uv sync                    # create/update .venv from uv.lock
uv run <command>           # run anything inside the managed environment
uv add <package>           # add a runtime dependency (updates pyproject.toml + uv.lock)
uv add --dev <package>     # add a dev-only dependency
uv lock --upgrade          # refresh uv.lock to latest allowed versions
```

Python version is pinned in `.python-version` (3.13). `pyproject.toml` has `[tool.uv] package
= false` — this is a script collection, not an installable package; nothing needs a
`[build-system]`.

## Common commands

```bash
# Lint & format
uv run ruff check .
uv run ruff format --check .     # add `--fix` / drop `--check` to auto-fix

# Type check
uv run mypy

# Run the eval suite (this project's test suite)
uv run python evals/run_evals.py
uv run python evals/run_evals.py --verbose
uv run python evals/run_evals.py --eval-id eval-05-safety-own-data-only

# Dependency CVE audit
uv run pip-audit

# Security static analysis
uv run bandit -r scripts -c pyproject.toml

# Run any script through the managed environment, e.g.:
uv run python scripts/discover.py --name "Your Name" --phone "+15551234567" --state CA
uv run python scripts/build_dashboard.py

# SAFE-4 domain-scope check (verdict on whether a URL is in scope for a broker)
uv run python scripts/url_guard.py --broker-id spokeo --url "https://example.com/x"

# Render the CAPTCHA batch solve-session checklist
uv run python scripts/action_queue.py --session
```

## Conventions

- **SKILL.md stays lean.** It orchestrates; it does not embed logic that belongs in
  `scripts/` or detail that belongs in `references/`. Keep it under 500 lines.
- **Deterministic logic lives in scripts, not prose.** Diffing scans, statutory-deadline
  math, broker deduplication by parent owner, template rendering, and case-store
  reads/writes are all scripted so runs stay resumable, idempotent, and auditable — don't
  move this logic into `SKILL.md` reasoning.
- **Adding a jurisdiction is a data change**, not a code change: add an entry to
  `data/jurisdictions.json` and a section in `references/jurisdictions/state-statutes.md`.
  Don't special-case new states in `SKILL.md` or the scripts.
- **Scripts import siblings via `sys.path.insert(0, str(Path(__file__).parent))`** (see
  `scripts/build_dashboard.py`, `scripts/diff_scan.py`) rather than package-relative imports —
  match this pattern in new scripts rather than introducing a package layout.
- **Naming:** the skill identifier, on-disk paths, the runtime store, and the schema `$id` are
  all lowercase `quietline` (e.g. `~/.quietline/`, `QUIETLINE_CASE_STORE`). "QuietLine" is the
  human-facing brand name used in prose, docs, and help text — keep that distinction; don't
  "fix" one into the other.

## Security & PII — first-class, not a footnote

This skill's entire job is handling another person's sensitive identity data (name, phone,
address, DOB in some cases) and submitting it to third-party sites. Treat every change through
that lens.

- **Own-data only.** The SAFE-1 gate (ownership affirmation before any discovery or
  submission) is release-blocking — see `evals/evals.json` eval #5. Never weaken or bypass it.
- **Truthful requests only (SAFE-2).** No fabricated identities, no impersonation.
- **Minimum PII collection.** Don't add fields to the case store or a broker submission that
  the specific broker doesn't require.
- **Never commit real case/identity data.** `.gitignore` already excludes `~/.quietline/`,
  `cases.json`, `cases/`, and `*.local.json` — do not weaken those patterns, and never `git
  add -f` a file matching them.
- **Secure coding rules for this codebase:**
  - No `shell=True`, `eval()`, or `exec()`.
  - Every `requests` call needs an explicit `timeout=`.
  - Validate and sanitize all external input (broker HTML, URLs, API responses) — treat it as
    untrusted. `verify_url.py`'s homepage-footer fallback in particular parses live HTML.
  - Use `pathlib.Path`, not string concatenation, for all filesystem paths.
  - Don't log full PII payloads to stdout/stderr in normal (non-`--verbose`) runs.
- `ruff`'s `S` (flake8-bandit) rules and `bandit` itself both run in CI specifically because
  of this data sensitivity — don't disable a rule to silence a finding without fixing the
  underlying issue or narrowly justifying the suppression inline.

## Dependency & CVE hygiene

- Runtime dependencies are intentionally minimal: `jsonschema` (case-record schema
  validation) and `requests` (live URL verification). Think twice before adding another.
- Run `uv run pip-audit` before any release and whenever a new dependency is added.
- **Dependabot** (`.github/dependabot.yml`) opens weekly PRs for `uv.lock` and GitHub Actions
  updates. Triage flow: review the diff → let CI run → `uv run python evals/run_evals.py`
  passes → merge. Don't let dependency PRs pile up; stale PRs are how CVEs linger.
- CI's `security` job (`pip-audit`, `bandit`, gitleaks) and the weekly CodeQL scan are release
  gates in spirit — a red `main` should block cutting a release until fixed.

## Testing

The eval suite (`evals/run_evals.py` + `evals/evals.json`) is this project's test suite —
there is no separate `tests/` directory. **Eval #5 (`eval-05-safety-own-data-only`, the SAFE-1
gate) is release-blocking**: a non-zero exit from the eval runner, and specifically a failure
of eval #5, means the ownership-affirmation safety gate has regressed and nothing should be
released until it's fixed.

When changing `scripts/discover.py`, `scripts/case_store.py`, or `scripts/deadlines.py`, run
the full suite, not just the eval touching your change — several evals cross-import these
modules (see `evals/run_evals.py`'s `sys.path` setup).

## Release process

Releases are packaged as a `.skill` bundle (a zip of the skill payload) and published as a
GitHub Release.

1. Bump `version` in `pyproject.toml`.
2. Ensure `main` is green: `uv run ruff check . && uv run mypy && uv run python
   evals/run_evals.py && uv run pip-audit`.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. `.github/workflows/release.yml` runs the eval suite as a release gate, then packages
   `quietline.skill` containing only the skill payload:
   `SKILL.md`, `scripts/`, `references/`, `assets/`, `data/`, `schemas/` — excluding dev/CI
   files (`.github/`, `pyproject.toml`, `uv.lock`, `CLAUDE.md`, `evals/`, `__pycache__/`,
   `.venv/`) — and attaches it to a GitHub Release for that tag.

`scripts/package_skill.py` is the single source of truth for the include-list and zip
layout — both the release workflow and the local dry-run call it, so they can't drift.
To dry-run the packaging step locally: `uv run python scripts/package_skill.py` (writes to
`dist/quietline.skill` by default; pass `--output PATH` to write elsewhere). If the payload
contents ever need to change, edit `INCLUDE` in `scripts/package_skill.py` — don't touch
release.yml directly.

## CI/CD (GitHub Actions)

| Workflow | Trigger | Does |
|---|---|---|
| `.github/workflows/ci.yml` | push, PR | lint (`ruff`), type check (`mypy`), eval suite, `pip-audit`, `bandit`, `gitleaks` secret scan |
| `.github/workflows/codeql.yml` | push, PR, weekly schedule | CodeQL static analysis (Python) |
| `.github/workflows/release.yml` | tag `v*` | eval suite as a gate, then builds and publishes `quietline.skill` |
| `.github/dependabot.yml` | weekly | opens PRs for `uv.lock` (via the `uv` ecosystem) and GitHub Actions version bumps |

All workflows provision the environment with `astral-sh/setup-uv` + `uv sync` — keep new CI
steps running through `uv run` rather than a bare interpreter, for the same reproducibility
reason as local development.
