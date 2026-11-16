"""Reject private curation payloads in the index or reachable commit history."""

from pathlib import Path
import subprocess
import sys


ANNOTATIONS = "imagery_model/annotations/"
PUBLIC_README = ANNOTATIONS + "README.md"
GENERATED_DIRECTORIES = ("imagery_model/artifacts/", "reviewer/tiles/")


def git_paths(repository: Path, *arguments: str) -> set[str]:
    output = subprocess.check_output(["git", "-C", str(repository), *arguments])
    return {path.decode("utf-8", errors="surrogateescape") for path in output.split(b"\0") if path}


def private_curation_paths(repository: Path) -> set[str]:
    tracked = git_paths(repository, "ls-files", "-z")
    # Scan HEAD ancestry so adding and then deleting a bundle within one PR
    # still fails. Other local branches and private backup refs are unrelated.
    history = git_paths(
        repository, "log", "HEAD", "--format=", "--name-only", "-z",
        "--full-history", "-m", "--no-renames", "--diff-filter=AM", "--", ANNOTATIONS,
    )
    annotations = {
        path for path in tracked | history
        if path.startswith(ANNOTATIONS) and path != PUBLIC_README
    }
    generated = {path for path in tracked if path.startswith(GENERATED_DIRECTORIES)}
    return annotations | generated


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    shallow = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "--is-shallow-repository"],
        text=True,
    ).strip()
    if shallow == "true":
        print("Fetch complete history before checking private curation.", file=sys.stderr)
        return 1
    paths = private_curation_paths(repository)
    if paths:
        print("Private curation or generated review data found in public Git storage.", file=sys.stderr)
        for path in sorted(paths):
            print(path, file=sys.stderr)
        return 1
    print("Private curation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
