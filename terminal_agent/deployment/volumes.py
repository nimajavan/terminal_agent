"""Quiesced managed-volume snapshots; restore only validated regular files/directories."""
import os
import tarfile
import time
import uuid
from pathlib import PurePosixPath

from terminal_agent.core.storage import atomic_json
from .state import read, digest_file, job_id


def check_archive(path):
    count = 0
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            count += 1
            total += member.size
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or "\\" in member.name or not (member.isfile() or member.isdir()):
                raise ValueError("Volume snapshot contains unsafe paths, links or special files")
            if count > 1000000 or total > 100 * 1024 ** 3:
                raise ValueError("Volume snapshot exceeds 100 GiB / one million files")


def quiesce(engine, app):
    current = engine.current(app)
    stopped = []
    if current:
        try:
            for identifier in {current["release"], current.get("previous")} - {None}:
                release = engine.release(app, identifier)
                engine.compose(release, "stop", timeout=120)
                stopped.append(release)
        except BaseException:
            resume(engine, stopped)
            raise
    return stopped


def resume(engine, releases):
    errors = []
    for release in releases:
        try:
            engine.compose(release, "up", "-d", "--no-build", "--pull", "never", timeout=180)
        except (OSError, RuntimeError) as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("A quiesced application could not be restarted")


def snapshot_volume(engine, spec, volume, quiesced=False):
    app = spec["name"]
    if volume not in spec["volumes"] and not (volume == "redis" and spec["redis"]):
        raise ValueError("Volume is outside this application's manifest")
    folder = engine.state.root / "backups" / app
    folder.mkdir(exist_ok=True, mode=0o700)
    identifier = uuid.uuid4().hex
    temporary = folder / (identifier + ".partial")
    stopped = [] if quiesced else quiesce(engine, app)
    dependency = ["docker", "compose", "-p", "lta-" + app + "-data", "-f", str(engine.state.project(app) / "dependencies.json")]
    redis_stopped = False
    try:
        if volume == "redis":
            engine.run.run(dependency + ["stop", "redis"], timeout=120)
            redis_stopped = True
        with open(temporary, "xb") as output:
            engine.command("docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                           "--cap-add", "DAC_OVERRIDE",
                           "--security-opt", "no-new-privileges", "--memory", "128m", "--pids-limit", "64",
                           "--mount", "type=volume,src=lta-" + app + "-" + volume + ",dst=/data,readonly",
                           "alpine:3.21", "tar", "-czf", "-", "-C", "/data", ".", timeout=1800, output=output)
            output.flush()
            os.fsync(output.fileno())
        check_archive(temporary)
        destination = folder / (identifier + ".dump")
        temporary.rename(destination)
        destination.chmod(0o600)
        metadata = {"id": identifier, "app": app, "kind": "volume", "volume": volume, "created": time.time(),
                    "sha256": digest_file(destination), "size": destination.stat().st_size}
        atomic_json(folder / (identifier + ".json"), metadata)
        return {"backup": identifier, "volume": volume}
    finally:
        if temporary.exists():
            temporary.unlink()
        try:
            if redis_stopped:
                engine.run.run(dependency + ["up", "-d", "--no-deps", "redis"], timeout=180)
        finally:
            resume(engine, stopped)


def restore_volume(engine, spec, metadata, path, drill=False):
    app = spec["name"]
    volume = metadata["volume"]
    if volume not in spec["volumes"] and not (volume == "redis" and spec["redis"]):
        raise ValueError("Snapshot volume is not declared by this application")
    check_archive(path)
    temporary_name = "lta-drill-" + uuid.uuid4().hex if drill else "lta-" + app + "-" + volume
    if drill:
        engine.ensure_object("volume", temporary_name)
    else:
        snapshot_volume(engine, spec, volume)
    stopped = [] if drill else quiesce(engine, app)
    dependency = ["docker", "compose", "-p", "lta-" + app + "-data", "-f", str(engine.state.project(app) / "dependencies.json")]
    redis_stopped = False
    succeeded = False
    try:
        if volume == "redis" and not drill:
            engine.run.run(dependency + ["stop", "redis"], timeout=120)
            redis_stopped = True
        with path.open("rb") as stream:
            # The fixed shell program only clears the explicitly named Docker volume, never host paths.
            engine.command("docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--memory", "256m", "--pids-limit", "64",
                           "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
                           "--security-opt", "no-new-privileges", "--mount", "type=volume,src=" + temporary_name + ",dst=/data",
                           "alpine:3.21", "sh", "-c", "find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf - -C /data",
                           timeout=1800, input_stream=stream)
        succeeded = True
    finally:
        if drill:
            engine.command("docker", "volume", "rm", temporary_name, timeout=60)
        elif succeeded:
            try:
                if redis_stopped:
                    engine.run.run(dependency + ["up", "-d", "--no-deps", "redis"], timeout=180)
            finally:
                resume(engine, stopped)
        # A failed destructive restore intentionally leaves writers stopped for operator recovery.
    if not drill and engine.current(app):
        engine.await_health(engine.release(app, engine.current(app)["release"]), spec)
    return {"backup": metadata["id"], "volume": volume, "restore_verified": True, "drill": drill}
