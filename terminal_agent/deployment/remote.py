"""OpenSSH transport with strict host verification and versioned Python zip runtimes."""
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import zipfile
from pathlib import Path

from terminal_agent.core.storage import data_root, atomic_json
from .state import read, Runner
from .spec import name, DOMAIN, OPERATIONS, integer


def profiles_path():
    return data_root() / "operations" / "servers.json"


def validate_profile(profile):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*", profile.get("host", "")):
        raise ValueError("SSH host must be user@hostname (IPv4 or DNS)")
    root = profile.get("root", "")
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+", root) or root in {"/", "/etc", "/usr", "/var", "/home", "/root", "/srv", "/tmp"}:
        raise ValueError("Use a dedicated absolute state directory, for example /srv/lta")
    integer(profile.get("port", 22), 1, 65535, "SSH port")
    if any(not isinstance(profile.get(key), list) for key in ("apps", "domains", "operations")):
        raise ValueError("Policy apps, domains and operations must be lists")
    if not profile.get("apps") or not profile.get("domains"):
        raise ValueError("Server policy needs explicit apps and domains")
    for app in profile["apps"]:
        name(app)
    for domain in profile["domains"]:
        if not DOMAIN.fullmatch(domain):
            raise ValueError("Invalid policy domain")
    if not set(profile.get("operations", [])) <= OPERATIONS:
        raise ValueError("Invalid server operation")
    integer(profile.get("max_memory_mb", 4096), 64, 65536, "max_memory_mb")
    integer(profile.get("max_cpus", 4), 1, 64, "max_cpus")
    return profile


def policy(profile):
    return {key: profile[key] for key in ("apps", "domains", "operations", "max_memory_mb", "max_cpus")}


def runtime_bytes():
    package = Path(__file__).resolve().parents[1]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*.py")):
            info = zipfile.ZipInfo("terminal_agent/" + path.relative_to(package).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
        archive.writestr(zipfile.ZipInfo("__main__.py", date_time=(2020, 1, 1, 0, 0, 0)), "import sys\nfrom terminal_agent.deployment.cli import worker_main\nsys.exit(worker_main())\n")
    return output.getvalue()


class Remote:
    def __init__(self, profile):
        self.profile = validate_profile(profile)
        self.runner = Runner()
        self.root = profile["root"]
        self.runtime = None

    def ssh_args(self):
        args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-p", str(self.profile["port"])]
        if self.profile.get("identity"):
            args += ["-o", "IdentitiesOnly=yes", "-i", self.profile["identity"]]
        if self.profile.get("known_hosts"):
            args += ["-o", "UserKnownHostsFile=" + self.profile["known_hosts"]]
        return args

    def run(self, args, data=None, timeout=120):
        # Arguments are quoted for the remote POSIX shell; never interpolate repository commands here.
        return self.runner.run(self.ssh_args() + [self.profile["host"], " ".join(shlex.quote(str(x)) for x in args)], data=data, timeout=timeout)

    def upload(self, path, data):
        if not path.startswith(self.root + "/"):
            raise ValueError("Upload escaped server state root")
        script = ("import os,sys,tempfile,pathlib; p=pathlib.Path(sys.argv[1]); "
                  "p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); "
                  "fd,t=tempfile.mkstemp(dir=str(p.parent)); f=os.fdopen(fd,'wb'); "
                  "f.write(sys.stdin.buffer.read()); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)")
        self.run(["python3", "-c", script, path], data=data, timeout=300)

    def transfer(self, path, local, upload=False):
        if not path.startswith(self.root + "/") or ".." in Path(path).parts:
            raise ValueError("Transfer escaped server state root")
        if upload:
            script = ("import sys,os,shutil,pathlib,tempfile; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); "
                      "fd,t=tempfile.mkstemp(dir=str(p.parent)); f=os.fdopen(fd,'wb'); shutil.copyfileobj(sys.stdin.buffer,f); "
                      "f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)")
        else:
            script = "import sys,shutil; f=open(sys.argv[1],'rb'); shutil.copyfileobj(f,sys.stdout.buffer)"
        args = ["python3", "-c", script, path]
        command = self.ssh_args() + [self.profile["host"], " ".join(shlex.quote(x) for x in args)]
        with open(local, "rb" if upload else "xb") as stream:
            return self.runner.run(command, input_stream=stream if upload else None, output=None if upload else stream, timeout=3600)

    def install(self):
        data = runtime_bytes()
        digest = hashlib.sha256(data).hexdigest()[:20]
        self.runtime = self.root + "/runtimes/" + digest + ".pyz"
        self.upload(self.runtime, data)
        return self.runtime

    def call(self, command, *args, payload=None, timeout=120):
        if not self.runtime:
            self.install()
        try:
            result = self.run(["python3", self.runtime, "--root", self.root, command, *args],
                              data=json.dumps(payload).encode() if payload is not None else None, timeout=timeout)
        except RuntimeError:
            try:
                detail = json.loads(self.runner.last_error or "{}")
            except ValueError:
                detail = {}
            if detail.get("message"):
                raise ValueError("Worker: " + detail["message"]) from None
            raise
        return json.loads(result) if result else None

    def wake(self):
        if not self.runtime:
            self.install()
        # stdout/stderr are never inherited by SSH. The persistent queue survives disconnection.
        script = "import subprocess,sys; subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)"
        self.run(["python3", "-c", script, "python3", self.runtime, "--root", self.root, "worker", "--idle-exit", "60"])

    def bootstrap(self):
        # Explicit server setup only. Never remove existing Docker packages or rewrite firewall rules.
        script = r'''set -eu
if [ "$(id -u)" -ne 0 ]; then echo 'Bootstrap requires a root SSH profile' >&2; exit 1; fi
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo 'Only Ubuntu and Debian are supported' >&2; exit 1;; esac
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl python3 python3-yaml
if ! command -v docker >/dev/null 2>&1; then
 install -m 0755 -d /etc/apt/keyrings
 curl --fail --silent --show-error "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/lta-docker.asc
 chmod a+r /etc/apt/keyrings/lta-docker.asc
 printf 'deb [arch=%s signed-by=/etc/apt/keyrings/lta-docker.asc] https://download.docker.com/linux/%s %s stable\n' "$(dpkg --print-architecture)" "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" > /etc/apt/sources.list.d/lta-docker.list
 apt-get update
 apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version
systemctl enable --now docker
'''
        self.run(["sh", "-s"], data=script.encode(), timeout=1200)
