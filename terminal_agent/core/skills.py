"""Declarative, local skill packs. Installing a pack never executes its content."""
import copy
import json
import re
from pathlib import Path
from terminal_agent.core.storage import atomic_json, data_root
from terminal_agent.core.planning import validate_plan

TRAFFIC_REQUESTS = {"monitor live network traffic", "monitors live network traffic", "monitor network traffic", "show live network traffic", "نمایش ترافیک شبکه", "ترافیک زنده شبکه"}


def step(identity, title, tool, args, **extra):
    return dict(id=identity, title=title, tool=tool, args=args, **extra)


BUILTINS = {
    "traffic": {"description": "Sample live interface RX/TX rates for 10 seconds", "steps": [
        step("monitor", "Monitor network throughput", "network_traffic", {"seconds": 10, "interval": 1})]},
    "git": {"description": "Inspect working tree, recent changes and commits", "steps": [
        step("status", "Working tree", "inspect", {"kind": "git_status"}),
        step("diff", "Uncommitted changes", "inspect", {"kind": "git_diff"}),
        step("log", "Recent commits", "inspect", {"kind": "git_log"})]},
    "docker": {"description": "Inspect containers and resource usage", "steps": [
        step("containers", "Container state", "inspect", {"kind": "docker_containers"}),
        step("resources", "Resource usage", "inspect", {"kind": "docker_resources"}, depends_on=["containers"])]},
    "network": {"description": "Inspect interfaces, routes and listening ports", "steps": [
        step("interfaces", "Interfaces", "shell", {"command": "ip -brief addr show"}),
        step("routes", "Routes", "shell", {"command": "ip route show"}),
        step("ports", "Listening ports", "shell", {"command": "ss -tulpn"})]},
}


class SkillRegistry:
    def __init__(self, root=None):
        self.root = Path(root or data_root()) / "skills"

    def list(self):
        result = {name: pack["description"] for name, pack in BUILTINS.items()}
        result.update({"systemd": "Inspect a systemd service and its logs", "nginx": "Inspect Nginx service, configuration and HTTP health"})
        for path in self.root.glob("*.json"):
            result.setdefault(path.stem, "Installed local skill")
        return result

    def install(self, source):
        path = Path(source)
        if path.stat().st_size > 300000:
            raise ValueError("Skill pack is too large")
        pack = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(pack, dict) or set(pack) != {"name", "description", "plan"}:
            raise ValueError("Skill pack requires name, description, plan")
        name = pack["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", name) or name in self.list():
            raise ValueError("Invalid or already installed skill name")
        if not isinstance(pack["description"], str):
            raise ValueError("Invalid description")
        validate_plan(pack["plan"])
        atomic_json(self.root / (name + ".json"), pack)
        return name

    def plan(self, name, service=None, url=None, repair=False):
        if name in {"systemd", "nginx"}:
            service = service or ("nginx" if name == "nginx" else None)
            if not service:
                raise ValueError("Provide --service for the systemd skill")
            steps = [step("status", "Inspect service state", "service", {"name": service, "action": "status"}, accept_exit_codes=[0, 3, 4]),
                     step("logs", "Inspect recent service logs", "service", {"name": service, "action": "logs"}),
                     step("ports", "Inspect listening ports", "shell", {"command": "ss -tulpn"})]
            if name == "nginx":
                steps.append(step("config", "Validate Nginx configuration", "inspect", {"kind": "nginx_config"}))
            if repair:
                checks = [{"tool": "service", "args": {"name": service, "action": "is-active"}, "contains": "active"}]
                if url:
                    checks.append({"tool": "http_check", "args": {"url": url}, "contains": "HTTP 200"})
                steps.append(step("restart", "Restart service after diagnostics (requires approval)", "service", {"name": service, "action": "restart"},
                                  depends_on=[s["id"] for s in steps], verify=checks))
            elif url:
                steps.append(step("health", "Check HTTP health", "http_check", {"url": url},
                                  verify=[{"tool": "http_check", "args": {"url": url}, "contains": "HTTP 200"}]))
            return validate_plan({"goal": "Diagnose %s%s" % (service, " and attempt restart" if repair else ""),
                                  "summary": "Collect evidence; a restart does not repair invalid configuration or missing dependencies.", "steps": steps})
        if name in BUILTINS:
            pack = copy.deepcopy(BUILTINS[name])
            return validate_plan({"goal": pack["description"], "summary": "Read-only diagnostic skill", "steps": pack["steps"]})
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", name):
            raise ValueError("Invalid skill name")
        pack = json.loads((self.root / (name + ".json")).read_text(encoding="utf-8"))
        return validate_plan(pack["plan"])
