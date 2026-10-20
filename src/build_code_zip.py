"""Build the anonymized code supplement ZIP for the competition submission.

The archive contains code, tests, the paper source, paper figures and the small
summary tables behind the paper. Every text file is scanned for personal
identifiers (user names, absolute home paths, e-mail addresses, institution
names); the build fails if any are found. Run from the repository root:

    python src/build_code_zip.py [--output submission/code_supplement.zip]
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIMIT_BYTES = 50_000_000
TOP = "code_supplement"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".csv", ".json", ".tex", ".svg"}
# Internal packaging tools are not needed by readers (and this one lists the
# identifiers it screens for).
SKIP_SOURCES = {"build_submission_package.py", "build_code_zip.py"}
# Small result tables that back the numbers and figures of the paper.
RESULT_FILES = [
    "outputs/question1/summary.json",
    "outputs/question1/tables/*.csv",
    "outputs/question2/comparison.csv",
    "outputs/question2/comparison_v2.csv",
    "outputs/question2/selection_v2.json",
    "outputs/question2/c2_multiplier_sensitivity.csv",
    "outputs/question2/v3_curve_points/comparison.csv",
    "outputs/question2/fcfs/metrics*",
    "outputs/question2/sjf/metrics*",
    "outputs/question2/q2_star/metrics*",
    "outputs/question2/q2_star/tail_*",
    "outputs/question3/v3/comparison.csv",
    "outputs/question3/v3/compensation.csv",
    "outputs/question3/v3/selection.json",
    "outputs/v4_validation/development.csv",
    "outputs/v4_validation/development_selection.json",
    "outputs/v4_validation/validation.csv",
    "outputs/v4_validation/validation_summary.csv",
    "outputs/v4_validation/speed_stress.csv",
    "outputs/v5_architecture/summary.csv",
    "outputs/v5_architecture/comparison.csv",
    "outputs/v6_source_protection/validation.csv",
    "outputs/v6_source_protection/validation_summary.csv",
    "outputs/v6_source_protection/validation_group_summary.csv",
    "outputs/v6_source_protection/development.csv",
    "outputs/v6_source_protection/development_selection.json",
    "outputs/paper_figures/*.png",
    "outputs/paper_figures/*.svg",
]
FORBIDDEN = re.compile(r"nanfe|nanfeidexumu|@gmail|hkust|appdata|[a-z]:[\\/]+users[\\/]|/c/users/|/home/[a-z]",
                       re.IGNORECASE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect() -> list[tuple[str, Path]]:
    """Return (archive name, source path) pairs."""
    items: list[tuple[str, Path]] = []
    for folder in ("src", "tests"):
        for path in sorted((ROOT / folder).glob("*.py")):
            if path.name not in SKIP_SOURCES:
                items.append((f"{folder}/{path.name}", path))
    items.append(("requirements.txt", ROOT / "requirements.txt"))
    items.append(("README.md", ROOT / "reports/paper/SUPPLEMENT_README.md"))
    items.append(("paper/paper.tex", ROOT / "reports/paper/paper.tex"))
    for pattern in RESULT_FILES:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"No file matches {pattern}")
        items.extend((path.relative_to(ROOT).as_posix(), path) for path in matches if path.is_file())
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "submission" / "code_supplement.zip")
    args = parser.parse_args()
    items = collect()
    problems = []
    for name, path in items:
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if FORBIDDEN.search(text):
                problems.append(name)
    if problems:
        raise SystemExit("Personal identifiers found; refusing to package:\n  " + "\n  ".join(problems))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in items:
            info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=(2026, 10, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    size = args.output.stat().st_size
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise SystemExit("ZIP integrity check failed")
        for info in archive.infolist():
            if FORBIDDEN.search(info.filename):
                raise SystemExit(f"Forbidden name inside archive: {info.filename}")
    if size >= LIMIT_BYTES:
        raise SystemExit(f"Archive is {size:,} bytes, above the 50 MB limit")
    print(f"{len(items)} files, {size:,} bytes, sha256 {sha256(args.output.read_bytes())}")
    print(f"written to {args.output}")


if __name__ == "__main__":
    main()
