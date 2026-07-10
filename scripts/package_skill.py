"""
Package the QuietLine skill payload into a distributable .skill bundle.

This is the single source of truth for "what goes in a release zip." Both
the local dry-run (`uv run python scripts/package_skill.py`) and
`.github/workflows/release.yml` invoke this script, so the two can never
drift the way a duplicated inline zip step and doc paragraph could.

Packages only the skill payload — SKILL.md plus the scripts/, references/,
assets/, data/, and schemas/ directories — excluding dev/CI files
(.github/, pyproject.toml, uv.lock, CLAUDE.md, evals/, __pycache__/,
.venv/). Matches the top-level `quietline/` directory layout the release
workflow has always produced, so the installed artifact shape doesn't
change.
"""

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Skill payload only — must match CLAUDE.md's "Release process" section and
# .github/workflows/release.yml. Keep all three in sync if this changes.
INCLUDE = ["SKILL.md", "scripts", "references", "assets", "data", "schemas"]

DEFAULT_OUTPUT = REPO_ROOT / "dist" / "quietline.skill"

# Top-level directory name staged inside the zip (matches the layout
# release.yml has always produced: a "quietline/" prefix on every entry).
ARCHIVE_ROOT = "quietline"


def _is_excluded(path: Path) -> bool:
    """Return True if `path` (or any parent segment) should be skipped."""
    for part in path.parts:
        if part == "__pycache__" or part.startswith("."):
            return True
    if path.suffix == ".pyc":
        return True
    if "data/fragments" in path.as_posix():
        return True
    return False


def _iter_payload_files(repo_root: Path) -> list[Path]:
    """Return the sorted list of repo-relative files to include in the bundle."""
    files: list[Path] = []
    for entry in INCLUDE:
        source = repo_root / entry
        if source.is_file():
            rel = source.relative_to(repo_root)
            if not _is_excluded(rel):
                files.append(rel)
        elif source.is_dir():
            for candidate in sorted(source.rglob("*")):
                if not candidate.is_file():
                    continue
                rel = candidate.relative_to(repo_root)
                if not _is_excluded(rel):
                    files.append(rel)
        else:
            print(f"WARNING: '{entry}' listed in INCLUDE but not found at {source}", file=sys.stderr)
    return files


def package(output: Path, repo_root: Path = REPO_ROOT) -> list[Path]:
    """
    Build the .skill zip at `output`. Returns the list of repo-relative
    files written into the archive (the manifest).

    Raises FileNotFoundError if SKILL.md is missing (refuses to package a
    payload that doesn't look like the skill).
    """
    if not (repo_root / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found under {repo_root} — refusing to package")

    files = _iter_payload_files(repo_root)

    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / ARCHIVE_ROOT
        for rel in files:
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo_root / rel, dest)

        if output.exists():
            output.unlink()

        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(stage / rel, arcname=str(Path(ARCHIVE_ROOT) / rel))

    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the QuietLine skill payload into a .skill zip bundle")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Path to write the .skill zip (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    output = Path(args.output)

    try:
        files = package(output)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    total_size = sum((REPO_ROOT / rel).stat().st_size for rel in files)

    print(f"Packaged {len(files)} file(s) into {output} ({total_size:,} bytes uncompressed)")
    for rel in files:
        print(f"  {ARCHIVE_ROOT}/{rel.as_posix()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
