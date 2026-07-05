"""Read-only git access to the investigated service's repository.

Plain `git` subprocess output is exactly what the LLM should read, so no
GitPython. Errors come back as strings — the LLM can recover from a bad
sha; an exception would kill the investigation.
"""

import re
import subprocess

_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
_LOG_FORMAT = "%h  %ad  %an  %s"
DIFF_CHAR_CAP = 4000


class GitRepo:
    def __init__(self, repo_path: str):
        self._path = repo_path

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", self._path, "-c", "safe.directory=*", *args],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return f"(git error: {result.stderr.strip() or 'unknown'})"
        return result.stdout

    def recent_commits(self, limit: int = 10) -> str:
        limit = max(1, min(int(limit), 15))
        out = self._run("log", f"-n{limit}", "--date=format:%Y-%m-%d %H:%M",
                        f"--format={_LOG_FORMAT}")
        return out.strip() or "(no commits found)"

    def commit_diff(self, sha: str) -> str:
        sha = sha.strip()
        if not _SHA_RE.match(sha):
            return f"(invalid sha: {sha!r} — pass an abbreviated hex sha from the commit list)"
        out = self._run("show", "--stat", "-p", "--date=format:%Y-%m-%d %H:%M", sha)
        if len(out) > DIFF_CHAR_CAP:
            return out[:DIFF_CHAR_CAP] + "\n... (diff truncated)"
        return out.strip() or "(empty diff)"

    def search_commits(self, text: str) -> str:
        """`git log -S text`: commits whose diff added or removed the string."""
        text = text.strip()
        if not text:
            return "(search text must be non-empty)"
        out = self._run("log", "-S", text, "--date=format:%Y-%m-%d %H:%M",
                        f"--format={_LOG_FORMAT}")
        return out.strip() or f"(no commits found whose diff touches {text!r})"

    def commit_meta(self, sha: str) -> dict | None:
        """Structured metadata for one commit (used to enrich the diagnosis)."""
        sha = sha.strip()
        if not _SHA_RE.match(sha):
            return None
        # ^{commit} peels tags and errors on blobs/trees, so the output below
        # is guaranteed to be the one formatted line.
        out = self._run("show", "-s", "--format=%h%x00%an%x00%s", f"{sha}^{{commit}}")
        if out.startswith("(git error"):
            return None
        parts = out.strip().split("\x00")
        if len(parts) != 3:
            return None
        short_sha, author, message = parts
        return {"sha": short_sha, "author": author, "message": message}
