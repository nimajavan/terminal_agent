"""Generate constrained Compose releases; no host mounts or host namespaces."""
import json
from pathlib import Path
from .spec import relative


def dockerfile(spec, source):
    runtime = spec["runtime"]
    context = Path(source) / spec["build"].get("context", ".")
    if not context.is_dir():
        raise ValueError("Build context does not exist")
    if runtime in {"dockerfile", "compose"}:
        return
    start = spec.get("start")
    if runtime == "node":
        install = "npm ci" if (context / "package-lock.json").exists() else "npm install"
        lines = ["FROM node:22-alpine", "WORKDIR /app", "COPY . .", "RUN " + install]
        start = start or ["npm", "start"]
    elif runtime == "python":
        lines = ["FROM python:3.12-slim", "WORKDIR /app", "COPY . ."]
        if (context / "requirements.txt").exists():
            lines.append('RUN ["pip", "install", "--no-cache-dir", "-r", "requirements.txt"]')
        elif (context / "pyproject.toml").exists():
            lines.append('RUN ["pip", "install", "--no-cache-dir", "."]')
        if not start:
            raise ValueError("Python runtime requires start")
    else:
        output = relative(spec["build"].get("output", "."))
        lines = ["FROM nginx:stable-alpine", "COPY " + json.dumps([output, "/usr/share/nginx/html/"])]
    if "command" in spec["build"]:
        if runtime == "static":
            raise ValueError("Static runtime serves built files; use Node/Dockerfile for a build pipeline")
        lines.append("RUN " + json.dumps(spec["build"]["command"]))
    if start:
        lines.append("CMD " + json.dumps(start))
    (context / "Dockerfile.lta").write_text("\n".join(lines) + "\n", encoding="utf-8")


def restrict(config, source):
    """Allowlist the normalized Compose surface, rejecting silent privilege escapes."""
    if set(config) - {"name", "services", "networks", "volumes"}:
        raise ValueError("Compose top-level feature is outside deployment policy")
    if config.get("volumes"):
        raise ValueError("Declare persistent volumes in lta.json, not source Compose")
    for network in config.get("networks", {}).values():
        if set(network) - {"name"}:
            raise ValueError("Custom Compose networks are not supported")
    allowed = {"image", "build", "command", "entrypoint", "environment", "working_dir", "user", "healthcheck",
               "depends_on", "expose", "labels", "restart", "networks", "init", "read_only", "stop_grace_period"}
    for service, value in config.get("services", {}).items():
        from .spec import name
        name(service)
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("Compose service contains unsupported/host-affecting fields: " + service)
        if not value.get("build") and not value.get("image"):
            raise ValueError("Each Compose service needs image or build")
        if value.get("image"):
            import re
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/@:-]*", value["image"]):
                raise ValueError("Invalid container image reference")
        if "build" in value:
            build = value["build"]
            if not isinstance(build, dict) or set(build) - {"context", "dockerfile", "args", "target"}:
                raise ValueError("Compose build must use a local context without entitlements")
            context = Path(build.get("context", source)).resolve()
            if context != Path(source).resolve() and Path(source).resolve() not in context.parents:
                raise ValueError("Compose build context escapes source")
            relative(build.get("dockerfile", "Dockerfile"))
        value.pop("networks", None)
        value.pop("labels", None)
        value["restart"] = "unless-stopped"
    if not config.get("services") or len(config["services"]) > 16:
        raise ValueError("Compose needs between 1 and 16 services")
    return config


def release_config(spec, source, identifier, environment, config=None):
    app = spec["name"]
    service = spec["service"]
    if config is None:
        context = str(Path(source) / spec["build"].get("context", "."))
        filename = spec["build"].get("dockerfile", "Dockerfile") if spec["runtime"] == "dockerfile" else "Dockerfile.lta"
        config = {"services": {service: {"build": {"context": context, "dockerfile": filename}}}}
    config = restrict(config, source)
    if service not in config["services"]:
        raise ValueError("Manifest service is missing from Compose")
    # Per-project network isolates applications. A separate edge network exposes only the web service to Caddy.
    config["networks"] = {"default": {}, "data": {"external": True, "name": "lta-" + app + "-data"},
                          "edge": {"external": True, "name": "lta-" + app + "-edge"}}
    config["volumes"] = {key: {"external": True, "name": "lta-" + app + "-" + key} for key in spec["volumes"]}
    for key, value in config["services"].items():
        value["networks"] = {"default": {}, "data": {}}
        value["mem_limit"] = str(spec["resources"]["memory_mb"]) + "m"
        value["cpus"] = spec["resources"]["cpus"]
        value["pids_limit"] = spec["resources"]["pids"]
        value["security_opt"] = ["no-new-privileges:true"]
        value["cap_drop"] = ["ALL"]
        # nginx needs these for its master process, without access to host files/devices.
        value["cap_add"] = ["CHOWN", "SETUID", "SETGID", "NET_BIND_SERVICE"]
        value["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
        if "build" in value:
            value["image"] = "lta-" + app + "-" + key + ":" + identifier
    web = config["services"][service]
    if spec.get("start"):
        web["entrypoint"] = spec["start"]
        web["command"] = []
    web["networks"]["edge"] = {"aliases": ["web-" + identifier]}
    web.setdefault("environment", {}).update(environment)
    web["environment"].setdefault("PORT", str(spec["port"]))
    web["volumes"] = [key + ":" + value for key, value in spec["volumes"].items()]
    config.pop("name", None)
    return config
