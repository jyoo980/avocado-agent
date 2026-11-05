#!/usr/bin/env python3

"""Save the result of an experimental run into the `avocado-experimental-data` repository.

`eval/experiments/run.sh` writes each run to
`~/.local/share/avocado-experiments/<tag>/<batch>/run-<i>/`. This script copies one such run into
the experimental-data repository under `<treatment>/<model>/<subject>/`, laid out as:

    <data-dir>/<treatment>/<model>/<subject>/
        ...                     the run's subject/ (the specified program and any files the
                                agent wrote), minus CBMC `*.goto` binaries and the `*.log`
                                files at its top level
        claude-files/
            <session-id>.jsonl  the run's transcripts/ (plus <session-id>/subagents/)
            run.log             the run's log (the driver's log with avocado-verify)
            run-pt2.log, ...    the logs of runs resumed from it, in order
            claude-output.json  (claude runner only) the `claude -p` result

`<subject>` is the basename of the `subject=` line in the run's `batch-info.txt`.

A run that was continued with `run.sh --resume` is saved by passing the original run directory
followed by each resumed run directory, in order (each one's `resumed_from=` in batch-info.txt must
name the one before it). The subject/ of the last one is saved (a resumed
run starts from the stopped run's subject/, so it covers the whole run), and the transcripts of all
of them are merged.

Usage:

    % eval/experiments/save_run.py --model opus-5-5 --treatment avocado-minus-tools \
          ~/.local/share/avocado-experiments/<tag>/<batch>/run-1

    % eval/experiments/save_run.py --model opus-5-5 --treatment full-avocado \
          <tag>/<batch>/run-1 <tag>/<resumed-batch>/run-1      # a run and its resumption

The data repository defaults to `$AVOCADO_DATA_DIR`, else `avocado-experimental-data` next to this
`avocado-agent` checkout; override it with `--data-dir`. The script only copies files: review and
commit them in the data repository yourself.
"""

import argparse
import os
import shutil
import sys

from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = AGENT_ROOT.parent / "avocado-experimental-data"

# Files in a run's subject/ that are not saved: CBMC goto binaries anywhere (hundreds of MB per
# run), and the scratch logs the agent leaves at the top level of the subject. Logs in
# subdirectories the agent made (e.g. exp/) are copied over.
IGNORE_ANYWHERE = ("*.goto",)
IGNORE_AT_ROOT = ("*.log",)

CLAUDE_FILES = "claude-files"

EXIT_CODE_MEANINGS = {
    "1": "the runner failed",
    "2": "a session hit a subscription usage limit",
    "124": "the run hit --timeout",
    "125": "the container could not be started",
    "137": "the container was killed (OOM, or it overran --timeout)",
    "143": "the batch was stopped with kill",
}

AVOCADO_BACKEND_MODELS = ["opus-4-8", "opus-5-5", "qwen-3-8"]
TREATMENT_NAMES = [
    "pure-agentic-baseline",
    "full-avocado",
    "avocado-minus-docs",
    "avocado-minus-tools",
    "avocado-minus-mutation-testing",
    "avocado-minus-harness",
    "avocado-minus-tools-minus-docs",
]

def main() -> None:
    args = _parse_args()

    run_dirs = [run_dir.expanduser().resolve() for run_dir in args.run_dirs]
    data_dir = args.data_dir.expanduser().resolve()
    for name, value in (("--model", args.model), ("--treatment", args.treatment)):
        if not value or "/" in value or value.startswith("."):
            _exit_with_message(f"{name} must be a plain directory name, got {value!r}")
    if not data_dir.is_dir():
        _exit_with_message(f"data directory {data_dir} does not exist; pass --data-dir")
    for run_dir in run_dirs:
        _check_run_dir(run_dir)

    subject = _get_subject_name(run_dirs, args.subject_name)
    dest = data_dir / args.treatment / args.model / subject
    if dest.exists() and not args.force:
        _exit_with_message(f"{dest} already exists; pass --force to replace it")

    _print_run_warnings(run_dirs, data_dir, args.model, args.treatment)

    subject_dir = run_dirs[-1] / "subject"
    count = _get_file_count_under_path(subject_dir, _ignore_subject(subject_dir))
    t_count = sum(_get_file_count_under_path(run_dir / "transcripts") for run_dir in run_dirs)
    excluded = ", ".join(IGNORE_ANYWHERE + tuple(f"./{p}" for p in IGNORE_AT_ROOT))
    print(f"subject:     {subject_dir}")
    print(f"             {count} files (excluding {excluded})")
    for index, run_dir in enumerate(run_dirs):
        print(f"run {index + 1}:       {run_dir} -> {CLAUDE_FILES}/{_get_log_name(index)}")
    print(f"transcripts: {t_count} files")
    print(f"destination: {dest}{' (replacing)' if dest.exists() else ''}")

    if args.dry_run:
        print("dry run: nothing copied")
        return

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _save_runs(run_dirs, dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    print(f"saved to {dest}")
    print("review and commit it with:")
    print(f"    cd {data_dir} && git add {dest.relative_to(data_dir)} && git status")

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=AVOCADO_BACKEND_MODELS,
        help="model directory name, e.g., opus-5-5")
    parser.add_argument(
        "--treatment", required=True, choices=TREATMENT_NAMES, help="treatment directory name, e.g., avocado-minus-tools"
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        metavar="RUN_DIR",
        help="a run-<i> directory, optionally followed by the runs resumed from it, in order",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("AVOCADO_DATA_DIR", DEFAULT_DATA_DIR)),
        help=f"the avocado-experimental-data checkout (default: $AVOCADO_DATA_DIR or {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--subject-name", help="subject directory name (default: from batch-info.txt)"
    )
    parser.add_argument(
        "--force", action="store_true", help="replace the destination if it already exists"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be saved without copying"
    )
    return parser.parse_args()


def _exit_with_message(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)

def _warn(message: str) -> None:
    print(f"_warning: {message}", file=sys.stderr)


def _read_batch_info(run_dir: Path) -> dict[str, str]:
    """Return the key=value pairs of the batch-info.txt next to `run_dir` (empty if absent)."""
    info_file = run_dir.parent / "batch-info.txt"
    info: dict[str, str] = {}
    if not info_file.is_file():
        return info
    for line in info_file.read_text().splitlines():
        # A line may hold several space-separated pairs (e.g. "runs=1 jobs=1 ...").
        key, sep, value = line.partition("=")
        if sep:
            info[key.strip()] = value.strip()
    return info


def _check_run_dir(run_dir: Path) -> None:
    if not run_dir.is_dir():
        _exit_with_message(f"{run_dir} is not a directory")
    for required in ("subject", "transcripts"):
        if not (run_dir / required).is_dir():
            _exit_with_message(f"{run_dir} has no {required}/; is it a run-<i> directory written by run.sh?")


def _get_subject_name(run_dirs: list[Path], override: str | None) -> str:
    if override:
        return override
    names = set()
    for run_dir in run_dirs:
        subject = _read_batch_info(run_dir).get("subject")
        if not subject:
            _exit_with_message(f"no subject= in {run_dir.parent / 'batch-info.txt'}; pass --subject-name")
        assert subject
        names.add(Path(subject).name)
    if len(names) > 1:
        _exit_with_message(f"the given runs are of different subjects: {', '.join(sorted(names))}")
    return names.pop()


def _ignore_subject(root: Path):
    """Return a copytree `ignore` callable implementing IGNORE_ANYWHERE and IGNORE_AT_ROOT."""
    anywhere = shutil.ignore_patterns(*IGNORE_ANYWHERE)
    at_root = shutil.ignore_patterns(*IGNORE_AT_ROOT)

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(anywhere(directory, names))
        if Path(directory).resolve() == root.resolve():
            ignored |= set(at_root(directory, names))
        return ignored

    return ignore


def _get_file_count_under_path(path: Path, ignore=None) -> int:
    """Return the number of files under `path`, skipping what a copytree `ignore` skips."""
    count = 0
    for directory, dirs, files in os.walk(path):
        skipped = ignore(directory, dirs + files) if ignore else set()
        dirs[:] = [d for d in dirs if d not in skipped]
        for name in files:
            if name not in skipped:
                count += 1
    return count


def _get_log_name(index: int) -> str:
    return "run.log" if index == 0 else f"run-pt{index + 1}.log"


def _print_run_warnings(run_dirs: list[Path], data_dir: Path, model: str, treatment: str) -> None:
    exit_file = run_dirs[-1] / "exit-code"
    if exit_file.is_file():
        code = exit_file.read_text().strip()
        if code != "0":
            meaning = EXIT_CODE_MEANINGS.get(code, "unknown")
            _warn(f"{run_dirs[-1]} exited with code {code} ({meaning}); the run may be incomplete")
    else:
        _warn(f"{run_dirs[-1]} has no exit-code; the run may still be going")

    if not (data_dir / treatment).is_dir():
        known = sorted(
            p.name for p in data_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        _warn(f"{treatment}/ does not exist yet in {data_dir} (existing: {', '.join(known)})")

    for run_dir in run_dirs:
        harness_dir = _read_batch_info(run_dir).get("harness_dir")
        if harness_dir and Path(harness_dir).parts[-2:] != (model, treatment):
            _warn(f"{run_dir} was run with harness {harness_dir}, not .../{model}/{treatment}")


def _save_runs(run_dirs: list[Path], dest: Path) -> None:
    claude_files = dest / CLAUDE_FILES
    subject = run_dirs[-1] / "subject"
    shutil.copytree(subject, dest, ignore=_ignore_subject(subject))
    if claude_files.exists():
        _exit_with_message(f"the run's subject/ already has a {CLAUDE_FILES}/; refusing to merge into it")
    claude_files.mkdir()
    for index, run_dir in enumerate(run_dirs):
        shutil.copytree(run_dir / "transcripts", claude_files, dirs_exist_ok=True)
        if (run_dir / "run.log").is_file():
            shutil.copy2(run_dir / "run.log", claude_files / _get_log_name(index))
        if (run_dir / "claude-output.json").is_file():
            name = "claude-output.json" if index == 0 else f"claude-output-pt{index + 1}.json"
            shutil.copy2(run_dir / "claude-output.json", claude_files / name)

if __name__ == "__main__":
    main()
