"""Bounded, secret-excluding source snapshots and safe archive extraction."""
import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

LIMIT = 100 * 1024 * 1024
EXCLUDED = {".git", ".ssh", ".aws", ".codex", ".agents", ".venv", "venv", "node_modules", "__pycache__", ".lta"}


def inventory(project):
    """Discover monorepo entrypoints without executing package managers or application code."""
    project = Path(project).resolve()
    candidates = []
    markers = {"package.json": "node", "pyproject.toml": "python", "requirements.txt": "python", "Dockerfile": "dockerfile",
               "compose.yaml": "compose", "compose.yml": "compose", "docker-compose.yml": "compose", "index.html": "static"}
    for base, directories, files in os.walk(str(project), followlinks=False):
        relative = Path(base).relative_to(project)
        directories[:] = sorted(d for d in directories if not excluded((d,)) and not (Path(base) / d).is_symlink()) if len(relative.parts) < 4 else []
        found = sorted({markers[f] for f in files if f in markers})
        if found:
            candidates.append({"context": relative.as_posix(), "runtimes": found})
        if len(candidates) >= 100:
            break
    return candidates


def excluded(parts):
    return any(p in EXCLUDED or p == ".env" or p.startswith(".env.") or
               p.endswith((".pem", ".key", ".p12", ".pfx")) for p in parts)


def snapshot(project, target):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("Source project does not exist")
    total = 0
    count = 0
    with tarfile.open(target, "w:gz") as archive:
        for base, directories, files in os.walk(str(project), followlinks=False):
            directories[:] = sorted(d for d in directories if not excluded((d,)) and not (Path(base) / d).is_symlink())
            for filename in sorted(files):
                path = Path(base) / filename
                relative = path.relative_to(project)
                if excluded(relative.parts):
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Source symlinks and special files are not supported: " + str(relative))
                if path.resolve() == Path(target).resolve():
                    continue
                total += path.stat().st_size
                count += 1
                if total > LIMIT or count > 20000:
                    raise ValueError("Source snapshot exceeds 100 MiB / 20000 files")
                archive.add(str(path), arcname=relative.as_posix(), recursive=False)
    return hashlib.sha256(Path(target).read_bytes()).hexdigest()


def extract(archive_path, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for index, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if (index >= 20000 or path.is_absolute() or ".." in path.parts or
                    "\\" in member.name or ":" in member.name or not member.isfile() or excluded(path.parts)):
                raise ValueError("Unsafe source archive entry")
            total += member.size
            if total > LIMIT:
                raise ValueError("Expanded source exceeds 100 MiB")
            target = destination.joinpath(*path.parts)
            if destination not in target.resolve().parents or target.exists():
                raise ValueError("Duplicate or escaping source path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as stream, open(target, "xb") as output:
                import shutil
                shutil.copyfileobj(stream, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def clone(url, ref, destination):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Repository URL must be HTTPS without embedded credentials, query or fragment")
    if not ref or ref.startswith("-") or any(c.isspace() for c in ref):
        raise ValueError("Invalid Git ref")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    def git(*args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=str(destination),
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=180)
        if result.returncode:
            raise RuntimeError("Git fetch failed; verify repository access and ref")
        return result.stdout.decode().strip()
    Path(destination).mkdir(parents=True, exist_ok=True)
    git("init", "--quiet")
    git("fetch", "--depth", "1", "--", url, ref)
    git("checkout", "--detach", "FETCH_HEAD")
    return git("rev-parse", "HEAD")
