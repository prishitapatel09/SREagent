#!/usr/bin/env python3
"""Build demo-repo/shopapi: a real git repository with a planted history.

The final tree is byte-identical to services/shopapi (verified below), so the
container built from demo-repo runs exactly the code the agent investigates.
Three "bad" commits introduce the flag-gated failure code paths; each failure's
distinctive string appears in exactly one commit's diff (also verified below),
so the agent can find the culprit with plain `git log -S`.

Usage: python3 scripts/seed_history.py
"""

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "services" / "shopapi"
SNAPSHOTS = ROOT / "scripts" / "history_snapshots"
DEST = ROOT / "demo-repo" / "shopapi"

AUTHORS = {
    "alice": ("Alice Nguyen", "alice@shopmart.dev"),
    "bob": ("Bob Okafor", "bob@shopmart.dev"),
    "carol": ("Carol Reyes", "carol@shopmart.dev"),
}


def canonical(rel: str) -> Path:
    return CANONICAL / rel


def snapshot(name: str) -> Path:
    return SNAPSHOTS / name


# (message, author, days_ago, {repo-relative path: Path to copy | literal str})
COMMITS = [
    ("chore: initial FastAPI scaffold", "alice", 14.0, {
        "Dockerfile": snapshot("Dockerfile_pre"),
        ".dockerignore": canonical(".dockerignore"),
        "requirements.txt": snapshot("requirements_pre.txt"),
        "VERSION": "1.4.1\n",
        "app/__init__.py": canonical("app/__init__.py"),
        "app/logging_setup.py": canonical("app/logging_setup.py"),
    }),
    ("feat(catalog): product listing and detail endpoints", "alice", 13.0, {
        "app/catalog.py": snapshot("catalog_pre_v1.py"),
        "app/main.py": canonical("app/main.py"),
    }),
    ("feat(orders): checkout and order status endpoints", "bob", 12.0, {
        "app/payment_client.py": snapshot("payment_client_pre.py"),
        "app/order_utils.py": snapshot("order_utils_pre.py"),
    }),
    ("feat(obs): prometheus metrics middleware and JSON logging", "alice", 11.0, {
        "app/metrics.py": canonical("app/metrics.py"),
    }),
    ("feat(flags): lightweight feature-flag registry and admin API", "bob", 10.0, {
        "app/flags.py": canonical("app/flags.py"),
    }),
    ("docs: README with API examples", "alice", 9.0, {
        "README.md": canonical("README.md"),
    }),
    ("fix(catalog): return 404 for unknown product id", "bob", 8.0, {
        "app/catalog.py": snapshot("catalog_pre_v2.py"),
    }),
    ("chore: bump fastapi, pin uvicorn", "alice", 7.0, {
        "requirements.txt": canonical("requirements.txt"),
    }),
    # BAD COMMIT #1 — payments_v2 flag routes checkout to a broken v2 client
    ("feat(payments): payments v2 client behind payments_v2 flag", "bob", 3.2, {
        "app/payment_client.py": canonical("app/payment_client.py"),
    }),
    ("test: smoke tests for checkout", "alice", 3.0, {
        "tests/test_checkout.py": canonical("tests/test_checkout.py"),
    }),
    # BAD COMMIT #2 — listing_inventory flag adds an N+1 downstream call
    ("feat(catalog): live inventory counts in product listing", "carol", 2.2, {
        "app/catalog.py": canonical("app/catalog.py"),
        "app/inventory.py": canonical("app/inventory.py"),
    }),
    ("chore: tune uvicorn worker settings", "bob", 1.8, {
        "Dockerfile": canonical("Dockerfile"),
    }),
    # BAD COMMIT #3 — order_timestamps flag crashes on unfulfilled orders
    ("refactor(orders): centralize order timestamp parsing", "carol", 1.0, {
        "app/order_utils.py": canonical("app/order_utils.py"),
    }),
    ("docs: add on-call notes", "alice", 0.8, {
        "docs/ONCALL.md": canonical("docs/ONCALL.md"),
    }),
    ("chore: bump version to 1.4.2", "bob", 0.25, {
        "VERSION": "1.4.2\n",
    }),
]

# distinctive string -> expected commit-subject prefix (verified with git log -S)
BAD_COMMIT_STRINGS = {
    "payments-v2.internal": "feat(payments)",
    "get_stock": "feat(catalog): live inventory",
    "parse_order_timestamps": "refactor(orders)",
}


def run_git(*args: str, env_extra: dict | None = None) -> str:
    import os

    env = dict(os.environ)
    env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["git", "-C", str(DEST), *args],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def write_files(files: dict) -> None:
    for rel, source in files.items():
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, Path):
            shutil.copyfile(source, target)
        else:
            target.write_text(source)


def build() -> None:
    if DEST.exists():
        # DEST is a live bind-mount source (the agent container's /repos/shopapi);
        # deleting the directory itself would strand the mount. Clear contents
        # in place so the directory inode survives a re-seed.
        for child in DEST.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        DEST.mkdir(parents=True)
    run_git("init", "-q", "-b", "main")
    run_git("config", "user.name", "seed")
    run_git("config", "user.email", "seed@shopmart.dev")

    now = datetime.now(timezone.utc)
    for message, author, days_ago, files in COMMITS:
        write_files(files)
        name, email = AUTHORS[author]
        date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        run_git("add", "-A")
        run_git(
            "commit", "-q", "-m", message,
            env_extra={
                "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
                "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
            },
        )


def verify() -> None:
    # 1. Each distinctive string must appear in exactly one commit's diff.
    for needle, subject_prefix in BAD_COMMIT_STRINGS.items():
        hits = [
            line for line in
            run_git("log", "-S", needle, "--format=%s").splitlines() if line
        ]
        if len(hits) != 1 or not hits[0].startswith(subject_prefix):
            sys.exit(
                f"verification failed: `git log -S {needle!r}` matched {hits!r}, "
                f"expected exactly one commit starting with {subject_prefix!r}"
            )

    # 2. Final tree must be byte-identical to the canonical source.
    def _is_junk(path: Path) -> bool:
        # Finder/Explorer droppings would otherwise fail the comparison.
        return any(
            part in ("__pycache__", ".DS_Store", "Thumbs.db") or part.startswith("._")
            for part in path.parts
        )

    mismatches = []
    canonical_files = {
        p.relative_to(CANONICAL) for p in CANONICAL.rglob("*")
        if p.is_file() and not _is_junk(p.relative_to(CANONICAL))
    }
    seeded_files = {
        p.relative_to(DEST) for p in DEST.rglob("*")
        if p.is_file() and ".git" not in p.parts and not _is_junk(p.relative_to(DEST))
    }
    for rel in sorted(canonical_files | seeded_files):
        a, b = CANONICAL / rel, DEST / rel
        if not a.exists() or not b.exists() or a.read_bytes() != b.read_bytes():
            mismatches.append(str(rel))
    if mismatches:
        sys.exit(f"verification failed: seeded tree differs from canonical: {mismatches}")


def main() -> None:
    build()
    verify()
    log_output = run_git("log", "--oneline", "--format=%h %ad %an  %s", "--date=short")
    print(f"Seeded {DEST.relative_to(ROOT)} with {len(COMMITS)} commits:\n")
    print(log_output)
    print("Verified: tree matches services/shopapi; each planted bug traces to exactly one commit.")


if __name__ == "__main__":
    main()
