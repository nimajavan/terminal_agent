"""Strict deployment manifests and explicit server authorization."""
import copy
import json
import re
from pathlib import Path, PurePosixPath

NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
OPERATIONS = {"deploy", "rollback", "backup", "restore", "restart", "dns"}


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Names must start with a lowercase letter and contain at most 40 letters, digits or hyphens")
    return value


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Expected a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError("Path must stay inside the project")
    return value


def fields(obj, allowed, label):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise ValueError("Invalid or unknown fields in " + label)


def integer(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("%s must be an integer between %s and %s" % (label, lower, upper))
    return value


def argv(value, label):
    if not isinstance(value, list) or not value or not value[0] or len(value) > 100 or any(
        not isinstance(x, str) or "\x00" in x or len(x) > 8192 for x in value
    ):
        raise ValueError(label + " must be a nonempty argument array")
    return value


def validate(raw):
    fields(raw, {"version", "name", "domain", "runtime", "port", "health", "start", "test", "build",
                 "compose", "service", "environment", "secrets", "resources", "database", "redis",
                 "migration", "monitor", "volumes", "dns", "service_secrets"}, "manifest")
    spec = copy.deepcopy(raw)
    if type(spec.get("version")) is not int or spec["version"] != 1:
        raise ValueError("Manifest version must be 1")
    name(spec.get("name"))
    if not isinstance(spec.get("domain"), str) or not DOMAIN.fullmatch(spec["domain"]):
        raise ValueError("A lowercase public DNS domain is required")
    if not isinstance(spec.get("runtime"), str) or spec["runtime"] not in {"node", "python", "static", "dockerfile", "compose"}:
        raise ValueError("runtime must be node, python, static, dockerfile or compose")
    integer(spec.get("port"), 1, 65535, "port")
    spec.setdefault("service", "app")
    name(spec["service"])
    if spec["runtime"] == "compose":
        relative(spec.get("compose", "compose.yaml"))
        spec.setdefault("compose", "compose.yaml")
    build = spec.setdefault("build", {})
    fields(build, {"context", "dockerfile", "command", "output"}, "build")
    for key in ("context", "dockerfile", "output"):
        if key in build:
            relative(build[key])
    if spec["runtime"] == "compose" and build:
        raise ValueError("Compose builds must be declared in the Compose services")
    if "command" in build and spec["runtime"] not in {"node", "python"}:
        raise ValueError("build.command is supported by node/python; use a Dockerfile for other build pipelines")
    if "output" in build and spec["runtime"] != "static":
        raise ValueError("build.output is only supported by the static runtime")
    if "dockerfile" in build and spec["runtime"] != "dockerfile":
        raise ValueError("build.dockerfile requires the dockerfile runtime")
    for key in ("start", "test", "migration"):
        if key in spec:
            argv(spec[key], key)
    if "command" in build:
        argv(build["command"], "build.command")
    health = spec.setdefault("health", {})
    fields(health, {"path", "timeout", "attempts", "interval", "stabilize", "status", "contains"}, "health")
    health.setdefault("path", "/")
    if not isinstance(health["path"], str) or not health["path"].startswith("/") or any(
        c in health["path"] for c in "\r\n\x00"
    ):
        raise ValueError("health.path must be an HTTP path")
    for key, default, low, high in (("timeout", 5, 1, 30), ("attempts", 30, 1, 120),
                                  ("interval", 2, 1, 30), ("stabilize", 3, 1, 30), ("status", 200, 200, 399)):
        integer(health.setdefault(key, default), low, high, "health." + key)
    if "contains" in health and (not isinstance(health["contains"], str) or len(health["contains"]) > 1024):
        raise ValueError("health.contains must be a short string")
    for key in ("environment", "secrets"):
        values = spec.setdefault(key, {})
        if not isinstance(values, dict) or any(not isinstance(k, str) or not ENV.fullmatch(k) or not isinstance(v, str) or "\x00" in v for k, v in values.items()):
            raise ValueError(key + " must map uppercase environment names to strings")
    if set(spec["environment"]) & set(spec["secrets"]):
        raise ValueError("A variable cannot be both environment and secret")
    for reference in spec["secrets"].values():
        name(reference)
    services = spec.setdefault("service_secrets", {})
    if not isinstance(services, dict):
        raise ValueError("service_secrets must map service names to secret references")
    for service, values in services.items():
        name(service)
        if not isinstance(values, dict):
            raise ValueError("Service secret values must be a mapping")
        for variable, reference in values.items():
            if not isinstance(variable, str) or not ENV.fullmatch(variable):
                raise ValueError("Invalid service secret variable")
            name(reference)
    resources = spec.setdefault("resources", {})
    fields(resources, {"memory_mb", "cpus", "pids", "min_disk_mb"}, "resources")
    for key, default, low, high in (("memory_mb", 512, 64, 65536), ("cpus", 1, 1, 64),
                                  ("pids", 256, 16, 4096), ("min_disk_mb", 2048, 128, 1048576)):
        integer(resources.setdefault(key, default), low, high, "resources." + key)
    if spec.get("database") is not None:
        db = spec["database"]
        fields(db, {"name", "user", "password_secret", "backup_hours", "retain"}, "database")
        for key in ("name", "user", "password_secret"):
            name(db.get(key))
        integer(db.setdefault("backup_hours", 24), 1, 720, "database.backup_hours")
        integer(db.setdefault("retain", 7), 1, 365, "database.retain")
    if type(spec.setdefault("redis", False)) is not bool:
        raise ValueError("redis must be boolean")
    volumes = spec.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise ValueError("volumes must map names to container paths")
    for key, value in volumes.items():
        name(key)
        if key in {"postgres", "redis"}:
            raise ValueError("postgres and redis are reserved volume names")
        if not isinstance(value, str) or not value.startswith("/data/") or ".." in PurePosixPath(value).parts:
            raise ValueError("Managed volume targets must be under /data/")
    monitor = spec.setdefault("monitor", {})
    fields(monitor, {"interval", "restart", "cooldown", "max_repairs", "backup_hours", "backup_retain"}, "monitor")
    for key, default, low, high in (("interval", 60, 10, 3600), ("cooldown", 900, 60, 86400), ("max_repairs", 2, 0, 10),
                                  ("backup_hours", 24, 1, 720), ("backup_retain", 7, 1, 365)):
        integer(monitor.setdefault(key, default), low, high, "monitor." + key)
    if type(monitor.setdefault("restart", False)) is not bool:
        raise ValueError("monitor.restart must be boolean")
    if spec.get("dns") is not None:
        import ipaddress
        dns = spec["dns"]
        fields(dns, {"provider", "zone", "token_secret", "address"}, "dns")
        if dns.get("provider") != "cloudflare" or not isinstance(dns.get("zone"), str) or not re.fullmatch(r"[a-f0-9]{32}", dns["zone"]):
            raise ValueError("DNS requires a Cloudflare zone identifier")
        name(dns.get("token_secret"))
        if not ipaddress.ip_address(dns.get("address", "")).is_global:
            raise ValueError("DNS address must be a public IP address")
    return spec


def load(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Manifest exceeds 1 MiB")
    text = path.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("Use lta.json or install YAML support: pip install 'linux-terminal-agent[yaml]'") from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError("Invalid YAML manifest") from exc
    return validate(raw)


def authorize(profile, spec, operation):
    if not isinstance(profile, dict) or any(not isinstance(profile.get(k), list) for k in ("apps", "domains", "operations")):
        raise ValueError("Invalid server authorization policy")
    if operation not in profile.get("operations", []):
        raise ValueError("Server policy does not allow " + operation)
    if spec["name"] not in profile.get("apps", []) or spec["domain"] not in profile.get("domains", []):
        raise ValueError("Application or domain is outside the server policy")
    if spec["resources"]["memory_mb"] > profile.get("max_memory_mb", 4096):
        raise ValueError("Application exceeds the server memory policy")
    if spec["resources"]["cpus"] > profile.get("max_cpus", 4):
        raise ValueError("Application exceeds the server CPU policy")
    if operation == "deploy" and spec["monitor"]["restart"] and "restart" not in profile.get("operations", []):
        raise ValueError("Automatic repair requires the restart permission")
    if operation == "deploy" and spec.get("dns") and "dns" not in profile.get("operations", []):
        raise ValueError("DNS provisioning requires the dns permission")


def detect(project, app, domain):
    project = Path(project)
    spec = {"version": 1, "name": name(app), "domain": domain, "port": 8080, "health": {"path": "/"}}
    for filename in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
        if (project / filename).is_file():
            spec.update(runtime="compose", compose=filename, service="app")
            return spec
    if (project / "Dockerfile").is_file():
        spec.update(runtime="dockerfile")
    elif (project / "package.json").is_file():
        package = json.loads((project / "package.json").read_text(encoding="utf-8"))
        if not isinstance(package, dict) or not isinstance(package.get("scripts", {}), dict):
            raise ValueError("package.json must contain an object with a scripts mapping")
        spec.update(runtime="node", port=3000, start=["npm", "start"])
        if "build" in package.get("scripts", {}):
            spec["build"] = {"command": ["npm", "run", "build"]}
        if "test" in package.get("scripts", {}):
            spec["test"] = ["npm", "test", "--", "--runInBand"] if "jest" in package["scripts"]["test"] else ["npm", "test"]
    elif any((project / p).exists() for p in ("pyproject.toml", "requirements.txt", "app.py", "main.py")):
        spec.update(runtime="python")
        entry = "app.py" if (project / "app.py").exists() else "main.py"
        if not (project / entry).exists():
            raise ValueError("Python project needs an explicit start command; use the documented manifest template")
        spec["start"] = ["python", entry]
    elif (project / "index.html").is_file():
        spec.update(runtime="static", port=80)
    else:
        raise ValueError("No supported entrypoint found; supply a Dockerfile or explicit manifest (monorepos use build.context)")
    return spec
