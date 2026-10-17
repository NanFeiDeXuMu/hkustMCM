"""Build a verified, data-free supplement ZIP beneath the system temp directory.

This packages existing evidence only. It does not run simulations or submit files.
The embedded manifest permits content verification after moving the archive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LIMIT_BYTES = 50_000_000
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
REQUIRED_ROOT = (
    "requirements.txt", "README.md", "AGENTS.md", "DECISIONS.md",
    "CURRENT_STATUS.md", "SUBMISSION_PACKAGE.md",
)
EXCLUDED_NAMES = {
    "schedule.csv", "allocations.csv", "job_info_clean.csv", "node_info_df.csv",
}
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".codex", ".agents"}
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".csv", ".toml", ".yaml", ".yml"}
OUTPUT_EXTENSIONS = {".csv", ".json", ".txt", ".png", ".svg", ".py"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def forbidden(relative: Path) -> bool:
    return (relative.name.lower() in EXCLUDED_NAMES
            or any(part.lower() in EXCLUDED_PARTS for part in relative.parts)
            or relative.suffix.lower() in {".zip", ".pyc", ".pyo"})


def collect_files(root: Path = ROOT) -> list[Path]:
    """Use explicit project trees, never a repository-wide recursive inclusion."""
    root = root.resolve()
    files: set[Path] = set()
    for filename in REQUIRED_ROOT:
        path = root / filename
        if not path.is_file():
            raise FileNotFoundError(f"Required supplement document missing: {filename}")
        files.add(path)
    files.update(root.glob("TASK*.md"))
    for directory, allowed in (
        ("src", TEXT_EXTENSIONS), ("tests", TEXT_EXTENSIONS),
        ("reports", TEXT_EXTENSIONS | {".png", ".svg", ".pdf"}),
        ("outputs", OUTPUT_EXTENSIONS),
    ):
        tree = root / directory
        if not tree.is_dir():
            raise FileNotFoundError(f"Required supplement directory missing: {directory}")
        for path in tree.rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and path.suffix.lower() in allowed and not forbidden(relative):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError(f"Refusing linked file: {relative}")
                files.add(path)
    result = sorted(files, key=lambda path: path.relative_to(root).as_posix())
    if any(forbidden(path.relative_to(root)) for path in result):
        raise ValueError("Forbidden artifact passed collection filters")
    return result


def build_manifest(files: list[Path], root: Path) -> dict:
    return {
        "format_version": 1,
        "purpose": "HKUST MCM Problem B reproducibility supplement; not a competition submission",
        "excluded": ["original PDFs and input CSVs", "all schedule.csv and allocations.csv", "virtual environments and caches"],
        "reproduction_note": "Original input CSVs must be supplied separately. See SUBMISSION_PACKAGE.md; full schedule audits require regenerated CSVs.",
        "files": [
            {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        ],
    }


def validate_destination(destination: Path, root: Path) -> Path:
    destination = destination.expanduser().resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if not destination.is_relative_to(temporary_root):
        raise ValueError(f"Archive must be under the system temporary directory: {temporary_root}")
    if destination.is_relative_to(root.resolve()):
        raise ValueError("Archive must remain outside the repository")
    if destination.suffix.lower() != ".zip":
        raise ValueError("Archive output must have a .zip suffix")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing archive: {destination}")
    return destination


def verify_archive(path: Path) -> dict:
    """Read every member and independently match size/hash against its manifest."""
    size = path.stat().st_size
    if size >= LIMIT_BYTES:
        raise ValueError(f"ZIP must be strictly smaller than {LIMIT_BYTES} bytes; got {size}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or MANIFEST_NAME not in names:
            raise ValueError("Duplicate archive entries or missing package manifest")
        for name in names:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or forbidden(relative):
                raise ValueError(f"Forbidden archive entry: {name}")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        records = manifest["files"]
        expected = [record["path"] for record in records]
        if len(expected) != len(set(expected)) or set(names) != set(expected) | {MANIFEST_NAME}:
            raise ValueError("Archive contents do not match package manifest")
        for record in records:
            body = archive.read(record["path"])
            if len(body) != record["bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise ValueError(f"Archive member differs from manifest: {record['path']}")
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"CRC check failed: {corrupt}")
    return {
        "passed": True, "archive": str(path.resolve()), "archive_bytes": size,
        "archive_sha256": sha256(path), "files": len(records),
        "uncompressed_bytes": sum(record["bytes"] for record in records),
        "size_limit_bytes_exclusive": LIMIT_BYTES,
        "raw_inputs_included": False, "full_schedule_csvs_included": False,
    }


def build_archive(destination: Path, root: Path = ROOT) -> dict:
    root = root.resolve()
    destination = validate_destination(destination, root)
    files = collect_files(root)
    manifest = build_manifest(files, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids modifying an earlier verified supplement.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for record, source in zip(manifest["files"], files):
            body = source.read_bytes()
            if len(body) != record["bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise ValueError(f"Source changed while packaging: {record['path']}")
            info = zipfile.ZipInfo(record["path"], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body, compresslevel=9)
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", compresslevel=9)
    result = verify_archive(destination)
    result["source_files_unchanged_during_build"] = all(
        sha256(root / record["path"]) == record["sha256"] for record in manifest["files"])
    if not result["source_files_unchanged_during_build"]:
        raise ValueError("Project files changed during packaging; rebuild once edits have finished")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New ZIP path under the system temporary directory")
    parser.add_argument("--dry-run", action="store_true", help="Summarize included files and uncompressed size; write no archive")
    parser.add_argument("--list-files", action="store_true", help="Include the full file list in --dry-run output")
    parser.add_argument("--verify-only", type=Path, help="Verify an existing archive without rebuilding it")
    args = parser.parse_args()
    if args.verify_only:
        result = verify_archive(args.verify_only)
    elif args.dry_run:
        manifest = build_manifest(collect_files(ROOT), ROOT)
        result = {"dry_run": True, "files": len(manifest["files"]),
                  "uncompressed_bytes": sum(item["bytes"] for item in manifest["files"])}
        if args.list_files:
            result["included"] = [item["path"] for item in manifest["files"]]
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = args.output or Path(tempfile.gettempdir()) / "hkustMCM_submission" / f"hkustMCM_supplement_{timestamp}.zip"
        result = build_archive(destination)
        verification_path = destination.with_suffix(".verification.json")
        verification_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["verification_report"] = str(verification_path.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
