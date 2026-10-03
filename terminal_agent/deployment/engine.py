"""Single-host transactional releases, readiness gates, backups and recovery."""
import hashlib
import json
import os
import shutil
import socket
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

from terminal_agent.core.storage import atomic_json, exclusive_lock
from .compose import dockerfile, release_config
from .source import extract
from .spec import validate, authorize, name
from .state import State, Runner, read, private_write, job_id, digest_file


def escape_compose(value):
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, dict):
        return {k: escape_compose(v) for k, v in value.items()}
    if isinstance(value, list):
        return [escape_compose(v) for v in value]
    return value


class Engine:
    def __init__(self, root, runner=None, sleep=time.sleep):
        self.state = State(root)
        self.run = runner or Runner()
        self.sleep = sleep

    def command(self, *args, **kwargs):
        return self.run.run(list(args), **kwargs)

    def compose(self, release, *args, **kwargs):
        return self.command("docker", "compose", "--project-name", "lta-" + release.parent.parent.name + "-" + release.name,
                            "-f", str(release / "compose.json"), *args, **kwargs)

    def current(self, app):
        return read(self.state.project(app) / "current.json")

    def release(self, app, identifier):
        return self.state.project(app) / "releases" / job_id(identifier)

    def preflight(self, spec):
        if os.name != "posix":
            raise ValueError("Deployment workers require Linux")
        references = list(spec["secrets"].values())
        for values in spec["service_secrets"].values():
            references.extend(values.values())
        if spec.get("database"):
            references.append(spec["database"]["password_secret"])
        if spec.get("dns"):
            references.append(spec["dns"]["token_secret"])
        for reference in set(references):
            self.state.secret(reference)
        self.command("docker", "info", "--format", "{{.ServerVersion}}", timeout=20)
        self.command("docker", "compose", "version", "--short", timeout=20)
        if shutil.disk_usage(self.state.root).free < spec["resources"]["min_disk_mb"] * 1024 * 1024:
            raise ValueError("Not enough free disk for deployment")
        for project in (self.state.root / "projects").iterdir():
            current = read(project / "current.json")
            if current and project.name != spec["name"] and current["domain"] == spec["domain"]:
                raise ValueError("Domain is already assigned to another application")

    def ensure_object(self, kind, identifier):
        # inspect failure may be a daemon failure: creation must succeed or the job fails.
        try:
            labels = json.loads(self.command("docker", kind, "inspect", identifier, "--format", "{{json .Labels}}", timeout=20)) or {}
        except RuntimeError:
            self.command("docker", kind, "create", "--label", "io.lta.root=" + str(self.state.root), identifier, timeout=30)
            return
        if labels.get("io.lta.root") != str(self.state.root):
            raise ValueError("Refusing to reuse an unmanaged Docker " + kind + ": " + identifier)

    def dependencies(self, spec):
        app = spec["name"]
        for suffix in ("data", "edge"):
            self.ensure_object("network", "lta-" + app + "-" + suffix)
        for volume in spec["volumes"]:
            self.ensure_object("volume", "lta-" + app + "-" + volume)
        services = {}
        environment = dict(spec["environment"])
        environment.update({key: self.state.secret(ref) for key, ref in spec["secrets"].items()})
        db = spec.get("database")
        project = self.state.project(app)
        existing = read(project / "dependencies-spec.json")
        signature = {"database": db, "redis": spec["redis"]}
        if existing and (existing.get("database") or {}).get("password_secret") != (db or {}).get("password_secret") and existing.get("database"):
            raise ValueError("Changing a database secret reference requires an explicit database credential migration")
        if db:
            password = self.state.secret(db["password_secret"])
            # PostgreSQL entrypoint initializes credentials only once; never pretend a changed secret rotates them.
            credential = hashlib.sha256(password.encode()).hexdigest()
            old_credential = read(project / "database-credential.json")
            if old_credential and old_credential != {"hash": credential, "name": db["name"], "user": db["user"]}:
                raise ValueError("Database credentials changed; rotate them in PostgreSQL before updating the recorded credential")
            self.ensure_object("volume", "lta-" + app + "-postgres")
            services["postgres"] = {"image": "postgres:16-alpine", "environment": {
                "POSTGRES_DB": db["name"], "POSTGRES_USER": db["user"], "POSTGRES_PASSWORD": password},
                "volumes": ["postgres:/var/lib/postgresql/data"],
                "healthcheck": {"test": ["CMD", "pg_isready", "-U", db["user"], "-d", db["name"]], "interval": "5s", "timeout": "3s", "retries": 30}}
            environment.update(PGHOST="postgres", PGPORT="5432", PGDATABASE=db["name"], PGUSER=db["user"], PGPASSWORD=password)
        if spec["redis"]:
            self.ensure_object("volume", "lta-" + app + "-redis")
            services["redis"] = {"image": "redis:7-alpine", "command": ["redis-server", "--appendonly", "yes"],
                                 "volumes": ["redis:/data"], "healthcheck": {"test": ["CMD", "redis-cli", "ping"], "interval": "5s", "timeout": "3s", "retries": 30}}
            environment.update(REDIS_HOST="redis", REDIS_PORT="6379")
        if services:
            for service in services.values():
                service.update(restart="unless-stopped", mem_limit=str(spec["resources"]["memory_mb"]) + "m",
                               cpus=spec["resources"]["cpus"], pids_limit=spec["resources"]["pids"],
                               logging={"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}})
            config = {"services": services, "networks": {"default": {"external": True, "name": "lta-" + app + "-data"}},
                      "volumes": {key: {"external": True, "name": "lta-" + app + "-" + key} for key in services}}
            atomic_json(project / "dependencies.json", escape_compose(config))
            self.command("docker", "compose", "-p", "lta-" + app + "-data", "-f", str(project / "dependencies.json"),
                         "up", "-d", "--wait", "--wait-timeout", "180", timeout=300)
            if db:
                atomic_json(project / "database-credential.json", {"hash": credential, "name": db["name"], "user": db["user"]})
        atomic_json(project / "dependencies-spec.json", signature)
        return environment

    def load_source_compose(self, spec, source):
        path = source / spec["compose"]
        text = path.read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError("Compose YAML requires python3-yaml on the worker; JSON Compose is dependency-free") from exc
            raw = yaml.safe_load(text)
        if not isinstance(raw, dict) or set(raw) - {"version", "name", "services", "networks"}:
            raise ValueError("Source Compose must only contain services and default networks")
        # Validate before Compose can read includes, env files, host paths or interpolate server environment.
        if "${" in text:
            raise ValueError("Declare environment/secret references in lta.json instead of Compose interpolation")
        allowed = {"image", "build", "command", "entrypoint", "environment", "working_dir", "user", "healthcheck",
                   "depends_on", "expose", "labels", "restart", "networks", "init", "read_only", "stop_grace_period"}
        if not isinstance(raw.get("services"), dict):
            raise ValueError("Compose services must be a mapping")
        for service in raw["services"].values():
            if not isinstance(service, dict) or set(service) - allowed:
                raise ValueError("Unsupported source Compose field (ports, env_file, includes and host mounts are forbidden)")
            build = service.get("build")
            if build:
                from .spec import relative
                if isinstance(build, str):
                    relative(build)
                elif isinstance(build, dict) and not set(build) - {"context", "dockerfile", "args", "target"}:
                    relative(build.get("context", "."))
                    relative(build.get("dockerfile", "Dockerfile"))
                else:
                    raise ValueError("Unsupported Compose build")
            if not isinstance(service.get("environment", {}), dict):
                raise ValueError("Compose environment must be a mapping with explicit values")
            if any(v is None for v in service.get("environment", {}).values()):
                raise ValueError("Server environment inheritance is forbidden")
        return json.loads(self.command("docker", "compose", "-f", str(path), "config", "--format", "json", cwd=source, timeout=30))

    def prepare(self, job, spec):
        release = self.release(spec["name"], job["id"])
        source = release / "source"
        archive = self.state.root / "incoming" / (job_id(job["payload"]["upload"]) + ".tgz")
        expected = job["payload"]["sha256"]
        if digest_file(archive) != expected:
            raise ValueError("Source archive checksum does not match")
        release.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not source.exists():
            temporary = release / "extracting"
            if temporary.exists():
                shutil.rmtree(temporary)
            extract(archive, temporary)
            temporary.rename(source)
        atomic_json(release / "manifest.json", spec)
        dockerfile(spec, source)
        environment = self.dependencies(spec)
        config = self.load_source_compose(spec, source) if spec["runtime"] == "compose" else None
        config = release_config(spec, source, job["id"], environment, config)
        for service, references in spec["service_secrets"].items():
            if service not in config["services"]:
                raise ValueError("Service secret references an unknown service: " + service)
            config["services"][service].setdefault("environment", {}).update(
                {variable: self.state.secret(reference) for variable, reference in references.items()})
        atomic_json(release / "compose.json", escape_compose(config))
        return release

    def pin_images(self, release):
        config = read(release / "compose.json")
        images = {}
        for name_, service in config["services"].items():
            identifier = self.command("docker", "image", "inspect", service["image"], "--format", "{{.Id}}", timeout=30)
            if not identifier.startswith("sha256:"):
                raise ValueError("Could not resolve immutable image ID")
            service["image"] = identifier
            service.pop("build", None)
            images[name_] = identifier
        atomic_json(release / "compose.json", config)
        atomic_json(release / "images.json", images)

    def healthy(self, release, spec):
        try:
            all_ids = self.compose(release, "ps", "-a", "-q", timeout=15).splitlines()
            expected = read(release / "compose.json")["services"]
            if len(all_ids) != len(expected):
                return False
            all_containers = json.loads(self.command("docker", "inspect", *all_ids, timeout=15))
            if any(not c["State"]["Running"] or c["State"].get("Health", {}).get("Status", "healthy") != "healthy" for c in all_containers):
                return False
            identifiers = self.compose(release, "ps", "-q", spec["service"], timeout=15).splitlines()
            if len(identifiers) != 1:
                return False
            container = json.loads(self.command("docker", "inspect", identifiers[0], timeout=15))[0]
            if not container["State"]["Running"]:
                return False
            if container["State"].get("Health", {}).get("Status", "healthy") != "healthy":
                return False
            network = container["NetworkSettings"]["Networks"]["lta-" + spec["name"] + "-edge"]
            address = network["IPAddress"]
            import ipaddress
            ipaddress.ip_address(address)
            url = "http://%s:%s%s" % (address, spec["port"], spec["health"]["path"])
            # Ignore controller proxy settings and redirects; check this exact candidate.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            request = urllib.request.Request(url, headers={"Host": spec["domain"]})
            try:
                response = opener.open(request, timeout=spec["health"]["timeout"])
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = response.read(65536).decode("utf-8", errors="replace")
                return response.code == spec["health"]["status"] and spec["health"].get("contains", "") in body
        except (OSError, ValueError, RuntimeError, KeyError, IndexError):
            return False

    def await_health(self, release, spec):
        for attempt in range(spec["health"]["attempts"]):
            if self.healthy(release, spec):
                return
            if attempt + 1 < spec["health"]["attempts"]:
                self.sleep(spec["health"]["interval"])
        raise RuntimeError("Candidate did not pass HTTP readiness checks")

    def gateway(self, app, domain, identifier, port):
        gateway = self.state.root / "gateway"
        routes = gateway / "routes"
        routes.mkdir(parents=True, exist_ok=True)
        caddy = gateway / "Caddyfile"
        private_write(caddy, "{\n  admin localhost:2019\n}\nimport /etc/caddy/routes/*.caddy\n")
        route = routes / (name(app) + ".caddy")
        old = route.read_text() if route.exists() else None
        if identifier:
            private_write(route, "%s {\n reverse_proxy web-%s:%d {\n  header_down X-LTA-Release %s\n }\n}\n" % (domain, job_id(identifier), port, identifier))
        elif route.exists():
            route.unlink()
        try:
            try:
                labels = json.loads(self.command("docker", "inspect", "lta-gateway", "--format", "{{json .Config.Labels}}", timeout=20)) or {}
                if labels.get("io.lta.root") != str(self.state.root):
                    raise ValueError("The gateway container belongs to another installation")
            except RuntimeError:
                self.ensure_object("volume", "lta-caddy-data")
                self.ensure_object("volume", "lta-caddy-config")
                self.command("docker", "run", "-d", "--name", "lta-gateway", "--restart", "unless-stopped",
                             "--label", "io.lta.root=" + str(self.state.root),
                             "--network", "lta-" + app + "-edge", "-p", "80:80", "-p", "443:443",
                             "--mount", "type=bind,src=" + str(gateway) + ",dst=/etc/caddy,readonly",
                             "-v", "lta-caddy-data:/data", "-v", "lta-caddy-config:/config",
                             "--memory", "256m", "--cpus", "1", "--pids-limit", "128", "caddy:2", timeout=180)
            networks = json.loads(self.command("docker", "inspect", "lta-gateway", "--format", "{{json .NetworkSettings.Networks}}", timeout=15))
            network = "lta-" + app + "-edge"
            if network not in networks:
                self.command("docker", "network", "connect", network, "lta-gateway", timeout=20)
            self.command("docker", "exec", "lta-gateway", "caddy", "validate", "--config", "/etc/caddy/Caddyfile", timeout=30)
            for attempt in range(10):
                try:
                    self.command("docker", "exec", "lta-gateway", "caddy", "reload", "--config", "/etc/caddy/Caddyfile", timeout=30)
                    break
                except RuntimeError:
                    if attempt == 9:
                        raise
                    self.sleep(1)
        except BaseException:
            if old is None:
                if route.exists():
                    route.unlink()
            else:
                private_write(route, old)
            try:
                self.command("docker", "exec", "lta-gateway", "caddy", "reload", "--config", "/etc/caddy/Caddyfile", timeout=30)
            except Exception:
                pass
            raise

    def public_health(self, spec, identifier=None):
        # HTTPS verification checks certificates and the published route, including DNS.
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("https://" + spec["domain"] + spec["health"]["path"], timeout=spec["health"]["timeout"]) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                identifier = identifier or (self.current(spec["name"]) or {}).get("release")
                return (bool(identifier) and response.headers.get("X-LTA-Release") == identifier
                        and urlsplit(response.url).hostname == spec["domain"] and response.status == spec["health"]["status"]
                        and spec["health"].get("contains", "") in body)
        except (OSError, ValueError):
            return False

    def deploy(self, job):
        spec = validate(job["payload"]["manifest"])
        active_policy = read(self.state.root / "policy.json", job["payload"]["policy"])
        authorize(active_policy, spec, "deploy")
        job["payload"]["policy"] = active_policy
        if spec.get("migration"):
            authorize(active_policy, spec, "backup")
        for old in self.state.jobs():
            if old["app"] == job["app"] and old["status"] == "needs_attention":
                if job["payload"].get("retry_of") != old["id"]:
                    raise ValueError("An uncertain operation must be resolved before another deployment (see ops resolve / ops retry)")
        self.preflight(spec)
        previous = self.current(spec["name"])
        if previous and previous["domain"] != spec["domain"]:
            raise ValueError("Changing a live application's domain requires a separate application/environment")
        self.state.event(job, "prepare", "Preparing source, networks and persistent dependencies")
        release = self.prepare(job, spec)
        self.state.event(job, "build", "Building release images")
        self.compose(release, "build", "--pull", timeout=1800, discard=True)
        self.compose(release, "pull", "--ignore-buildable", timeout=600, discard=True)
        self.pin_images(release)
        if spec.get("test"):
            self.state.event(job, "test", "Running release tests in a disposable container")
            image = read(release / "images.json")[spec["service"]]
            self.command("docker", "run", "--rm", "--network", "none", "--memory", str(spec["resources"]["memory_mb"]) + "m",
                         "--cpus", str(spec["resources"]["cpus"]), "--pids-limit", str(spec["resources"]["pids"]),
                         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", spec["test"][0],
                         image, *spec["test"][1:], timeout=600, discard=True)
        if spec.get("migration"):
            if not spec.get("database"):
                raise ValueError("Automatic migrations require a managed PostgreSQL database and a pre-migration backup")
            self.state.event(job, "backup", "Backing up database before migration")
            self.backup(spec["name"], spec=spec)
            # Intent is durable before a possibly irreversible operation. Never replay after a crash.
            job["migration_started"] = True
            self.state.event(job, "migration", "Running declared backward-compatible migration")
            self.compose(release, "run", "--rm", "--no-deps", "--entrypoint", spec["migration"][0],
                         spec["service"], *spec["migration"][1:], timeout=600, discard=True)
            job["migration_completed"] = True
            self.state.save(job)
        job["previous"] = previous
        self.state.event(job, "start", "Starting candidate release")
        self.compose(release, "up", "-d", "--no-build", "--pull", "never", timeout=300)
        self.await_health(release, spec)
        if spec.get("dns"):
            self.state.event(job, "dns", "Provisioning the explicitly authorized DNS record")
            from .dns import provision
            provision(spec, self.state.secret(spec["dns"]["token_secret"]))
        job["switch_started"] = True
        self.state.event(job, "switch", "Publishing healthy candidate through HTTPS gateway")
        try:
            self.gateway(spec["name"], spec["domain"], job["id"], spec["port"])
            for attempt in range(spec["health"]["attempts"]):
                if self.public_health(spec, job["id"]):
                    break
                if attempt + 1 == spec["health"]["attempts"]:
                    raise RuntimeError("Public HTTPS check failed; verify DNS, firewall and certificate issuance")
                self.sleep(spec["health"]["interval"])
            for _ in range(spec["health"]["stabilize"]):
                self.sleep(spec["health"]["interval"])
                if not self.healthy(release, spec) or not self.public_health(spec, job["id"]):
                    raise RuntimeError("Release failed the stabilization window")
            current = {"release": job["id"], "previous": previous["release"] if previous else None,
                       "domain": spec["domain"], "deployed": time.time(), "source": job["payload"].get("source"),
                       "sha256": job["payload"]["sha256"], "policy": job["payload"]["policy"]}
            atomic_json(self.state.project(spec["name"]) / "current.json", current)
            job["committed"] = True
            self.state.event(job, "committed", "Release verified and committed")
        except BaseException:
            try:
                self.recover_switch(job, spec)
            except Exception:
                job["recovery_failed"] = True
                self.state.save(job)
                raise RuntimeError("Traffic recovery failed; candidate left running for inspection") from None
            raise
        # Keep previous release running for immediate rollback; resource use is visible in status.
        return current

    def stop_obsolete(self, app, keep):
        """Stop only this app's obsolete containers; preserve source, images and persistent data."""
        failures = []
        for release in (self.state.project(app) / "releases").glob("*"):
            if release.name not in keep and (release / "compose.json").exists():
                try:
                    self.compose(release, "down", "--remove-orphans", timeout=120, discard=True)
                except (RuntimeError, OSError):
                    failures.append(release.name)
        return failures

    def recover_switch(self, job, spec):
        current = self.current(spec["name"])
        if current and current["release"] == job["id"]:
            # Atomic current pointer is the commit record, even if the worker died before saving job status.
            job["committed"] = True
            return
        previous = job.get("previous")
        port = spec["port"]
        if previous:
            previous_spec = read(self.release(spec["name"], previous["release"]) / "manifest.json")
            port = previous_spec["port"]
        self.gateway(spec["name"], spec["domain"], previous["release"] if previous else None, port)
        self.state.event(job, "recovered", "Restored previous gateway route; database was not rolled back")

    def rollback(self, app, identifier=None, job=None):
        current = self.current(app)
        if not current:
            raise ValueError("Application has no deployed release")
        identifier = identifier or current.get("previous")
        release = self.release(app, identifier)
        spec = validate(read(release / "manifest.json"))
        authorize(read(self.state.root / "policy.json", current["policy"]), spec, "rollback")
        if spec["domain"] != current["domain"]:
            raise ValueError("Rollback domain mismatch")
        self.compose(release, "up", "-d", "--no-build", "--pull", "never", timeout=300)
        self.await_health(release, spec)
        previous_spec = read(self.release(app, current["release"]) / "manifest.json")
        if job is not None:
            job.update(previous=current, target_release=identifier, switch_started=True)
            self.state.event(job, "switch", "Switching to a verified retained release")
        try:
            self.gateway(app, spec["domain"], identifier, spec["port"])
            if not self.public_health(spec, identifier):
                raise RuntimeError("Rollback public health check failed")
            atomic_json(self.state.project(app) / "current.json", dict(current, release=identifier, previous=current["release"], deployed=time.time()))
            if job is not None:
                job["committed"] = True
                self.state.save(job)
        except BaseException:
            committed = self.current(app)
            if committed and committed["release"] == identifier:
                raise
            try:
                self.gateway(app, current["domain"], current["release"], previous_spec["port"])
            except Exception:
                if job is not None:
                    job["recovery_failed"] = True
                    self.state.save(job)
                raise RuntimeError("Rollback route recovery failed; inspect the gateway") from None
            raise
        return {"release": identifier}

    def db_args(self, app, *args):
        return ["docker", "compose", "-p", "lta-" + app + "-data", "-f", str(self.state.project(app) / "dependencies.json"),
                "exec", "-T", "postgres", *args]

    def backup(self, app, spec=None):
        from .volumes import quiesce, resume
        active_spec = spec
        if active_spec is None:
            current = self.current(app)
            if not current:
                raise ValueError("Application has no active release")
            active_spec = read(self.release(app, current["release"]) / "manifest.json")
            authorize(read(self.state.root / "policy.json", current["policy"]), active_spec, "backup")
        # Coordinated app/DB/storage backups freeze application writers for the whole cohort.
        stopped = quiesce(self, app) if spec is None and (active_spec["volumes"] or active_spec["redis"]) else []
        try:
            return self._backup(app, spec=spec, quiesced=bool(stopped))
        finally:
            resume(self, stopped)

    def _backup(self, app, spec=None, quiesced=False):
        automatic_migration = spec is not None
        if spec is None:
            current = self.current(app)
            if not current:
                raise ValueError("Application has no active release")
            spec = read(self.release(app, current["release"]) / "manifest.json")
            authorize(read(self.state.root / "policy.json", current["policy"]), spec, "backup")
        db = spec.get("database")
        if not db:
            if not spec["volumes"] and not spec["redis"]:
                raise ValueError("Application has no managed persistent data")
            return {"backups": self.backup_volumes(spec, quiesced)}
        import uuid
        identifier = uuid.uuid4().hex
        folder = self.state.root / "backups" / name(app)
        folder.mkdir(exist_ok=True, mode=0o700)
        temporary = folder / (identifier + ".partial")
        try:
            with open(temporary, "xb") as output:
                self.run.run(self.db_args(app, "pg_dump", "-U", db["user"], "-d", db["name"], "-Fc"), timeout=1800, output=output)
                output.flush()
                os.fsync(output.fileno())
            with temporary.open("rb") as header:
                valid_header = header.read(5) == b"PGDMP"
            if not valid_header:
                raise RuntimeError("Invalid PostgreSQL backup")
            destination = folder / (identifier + ".dump")
            temporary.rename(destination)
            destination.chmod(0o600)
            checksum = digest_file(destination)
            atomic_json(folder / (identifier + ".json"), {"id": identifier, "app": app, "created": time.time(),
                        "sha256": checksum, "size": destination.stat().st_size, "database": {"name": db["name"], "user": db["user"]}})
            return {"backup": identifier, "sha256": checksum,
                    "volumes": [] if automatic_migration else self.backup_volumes(spec, quiesced)}
        finally:
            if temporary.exists():
                temporary.unlink()

    def backup_volumes(self, spec, quiesced=False):
        from .volumes import snapshot_volume
        return [snapshot_volume(self, spec, volume, quiesced=quiesced) for volume in list(spec["volumes"]) + (["redis"] if spec["redis"] else [])]

    def restore(self, app, identifier, drill=False):
        current = self.current(app)
        if not current:
            raise ValueError("Deploy this application's dependencies before restoring")
        spec = read(self.release(app, current["release"]) / "manifest.json")
        authorize(read(self.state.root / "policy.json", current["policy"]), spec, "backup" if drill else "restore")
        folder = self.state.root / "backups" / app
        path = folder / (job_id(identifier) + ".dump")
        metadata = read(folder / (identifier + ".json"))
        if not metadata or digest_file(path) != metadata["sha256"]:
            raise ValueError("Backup checksum mismatch")
        if metadata.get("kind") == "volume":
            from .volumes import restore_volume
            return restore_volume(self, spec, metadata, path, drill)
        db = spec.get("database")
        if not db:
            raise ValueError("No managed database")
        target = "lta_drill_" + identifier[:12] if drill else db["name"]
        if drill:
            self.run.run(self.db_args(app, "createdb", "-U", db["user"], target), timeout=60)
        else:
            self.backup(app)
            self.compose(self.release(app, current["release"]), "stop", timeout=120)
            if current.get("previous"):
                self.compose(self.release(app, current["previous"]), "stop", timeout=120)
        try:
            with path.open("rb") as stream:
                self.run.run(self.db_args(app, "pg_restore", "-U", db["user"], "-d", target, "--clean", "--if-exists",
                                         "--no-owner", "--exit-on-error", "--single-transaction"), timeout=1800, input_stream=stream)
        finally:
            if drill:
                self.run.run(self.db_args(app, "dropdb", "-U", db["user"], "--if-exists", target), timeout=60)
        if not drill:
            release = self.release(app, current["release"])
            self.compose(release, "up", "-d", "--no-build", "--pull", "never", timeout=180)
            self.await_health(release, spec)
        return {"backup": identifier, "restore_verified": True, "drill": drill}

    def execute(self, job):
        job["status"] = "running"
        self.state.save(job)
        try:
            self.run.last_error = None
            self.run.redactions = [self.state.secret(p.name) for p in (self.state.root / "secrets").iterdir() if p.is_file() and not p.name.startswith(".")]
            operation = job["operation"]
            if operation == "deploy":
                result = self.deploy(job)
            elif operation == "rollback":
                result = self.rollback(job["app"], job["payload"].get("release"), job=job)
            elif operation == "backup":
                result = self.backup(job["app"])
            elif operation in {"restore", "drill"}:
                result = self.restore(job["app"], job["payload"]["backup"], operation == "drill")
            else:
                raise ValueError("Unknown queued operation")
            job.update(status="completed", result=result)
            self.state.event(job, "completed", "Operation completed")
            if operation in {"deploy", "rollback"}:
                current = self.current(job["app"])
                failed = self.stop_obsolete(job["app"], {current["release"], current.get("previous")})
                if failed:
                    job["cleanup_pending"] = failed
                    self.state.save(job)
        except (Exception, KeyboardInterrupt) as exc:
            current = self.current(job["app"])
            committed_release = job["id"] if job["operation"] == "deploy" else job.get("target_release")
            if job["operation"] in {"deploy", "rollback"} and current and current["release"] == committed_release:
                job.update(status="completed", committed=True, result=current)
                self.state.event(job, "committed", "Release committed before interruption; cleanup may be pending")
                if isinstance(exc, KeyboardInterrupt):
                    raise
                return
            job["status"] = "needs_attention" if job.get("migration_started") or job.get("recovery_failed") or job["operation"] == "restore" else "failed"
            # Exception strings are intentionally not persisted: third-party errors can contain secrets.
            job["error"] = type(exc).__name__
            job["failed_phase"] = job["phase"]
            if getattr(self.run, "last_error", None):
                job["diagnostic"] = self.run.last_error
            self.state.event(job, "failed", "Operation failed in phase " + job["phase"] + "; " + type(exc).__name__)
            if job["operation"] == "deploy" and not job.get("committed") and not job.get("recovery_failed"):
                release = self.release(job["app"], job["id"])
                if (release / "compose.json").exists():
                    try:
                        self.compose(release, "stop", timeout=60)
                    except Exception:
                        pass
            if isinstance(exc, KeyboardInterrupt):
                raise

    def worker(self, once=False, idle_exit=0):
        with exclusive_lock(self.state.root / "worker.lock"):
            # A killed worker never replays a migration, restore or arbitrary application code.
            for job in self.state.jobs():
                if job["status"] == "running":
                    if job["operation"] == "deploy":
                        try:
                            if job.get("switch_started"):
                                self.recover_switch(job, validate(job["payload"]["manifest"]))
                            release = self.release(job["app"], job["id"])
                            if not job.get("committed") and (release / "compose.json").exists():
                                self.compose(release, "stop", timeout=60)
                        except (RuntimeError, ValueError, OSError):
                            job["recovery_failed"] = True
                    elif job["operation"] == "rollback" and job.get("switch_started"):
                        current = self.current(job["app"])
                        if current and current["release"] == job.get("target_release"):
                            job["committed"] = True
                        else:
                            try:
                                previous = job["previous"]
                                spec = read(self.release(job["app"], previous["release"]) / "manifest.json")
                                self.gateway(job["app"], spec["domain"], previous["release"], spec["port"])
                            except (RuntimeError, ValueError, OSError):
                                job["recovery_failed"] = True
                    job["status"] = "completed" if job.get("committed") else "needs_attention"
                    self.state.event(job, "reconciled", "Recovered interrupted worker; inspect before retrying")
            idle_since = time.monotonic()
            while True:
                queued = [job for job in self.state.jobs() if job["status"] == "queued"]
                for job in queued:
                    self.execute(job)
                    idle_since = time.monotonic()
                if once:
                    return
                from .observe import observe
                try:
                    observe(self)
                except (ValueError, RuntimeError, OSError):
                    # Monitoring failure must not stop the deployment queue.
                    pass
                if idle_exit and time.monotonic() - idle_since >= idle_exit and not any(j["status"] == "queued" for j in self.state.jobs()):
                    return
                self.sleep(5)
