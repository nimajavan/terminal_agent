# Autonomous deployment and operations (3.0)

LTA now has a deterministic operations engine, separate from natural-language shell execution. `lta deploy` makes **zero model calls**. Provider outages cannot interrupt its planned commands, and the configured AI provider/model never changes. Existing agent commands and safety behavior are preserved.

## Supported boundary

One Linux Docker host per server profile, with multiple applications and domains; multiple profiles support staging, production and separate servers. Runtimes: Node/npm, Python, prebuilt static sites, custom Dockerfiles and a deliberately restricted Compose stack. Ubuntu/Debian bootstrapping is available explicitly. SSH requires Python 3.8+, a verified host key and noninteractive authentication. Workers require Docker Engine and Compose v2. YAML is optional: `pip install '.[yaml]'` on the controller, `python3-yaml` on the worker; JSON manifests/Compose need no extra Python packages.

This is not a Kubernetes scheduler or a universal framework converter. Use a Dockerfile for other languages, unusual build tools or multi-stage frontend builds. Monorepos can use `build.context`; `ops inspect` lists candidate entrypoints. Existing source Compose must remove published host ports, bind mounts, host namespaces, includes, `env_file`, inherited/interpolated host variables, privileged options, external networks and source-defined persistent volumes. Declare persistent services/data through the deployment manifest. Services are long-running; put one-shot jobs in `migration`. Application start commands must listen on `0.0.0.0` and the declared port.

## One-time setup

1. Verify the server's SSH fingerprint through a trusted channel and add it to your normal `known_hosts`. LTA never disables host-key verification.
2. Register explicit application/domain permissions. An SSH user with access to Docker can effectively administer that host; the LTA policy constrains LTA operations, not a hostile SSH user. Use trusted project code and a dedicated deployment identity/host.
3. Provision the host once and install the durable worker if you want reboot recovery, continuous monitoring and scheduled backups.

```bash
lta ops server add production --host root@server.example.com \
  --identity ~/.ssh/deploy --apps demo api web \
  --domains demo.example.com api.example.com web.example.com \
  --operations deploy rollback backup restore restart
lta ops server setup production --bootstrap --service
```

`--bootstrap` installs Docker through its official signed apt repository only when Docker is absent. It does not uninstall conflicting packages, rewrite firewalls or upgrade an existing Docker installation. Root SSH is required for bootstrap/systemd; an existing writable state directory and Docker access suffice for normal operations. State defaults to `/srv/lta` (mode 0700). Only one LTA installation/gateway should own a host's ports 80/443. Existing nginx/Caddy or occupied ports cause deployment to fail; LTA does not remove those services.

Server profiles live under `$XDG_DATA_HOME/linux-terminal-agent/operations/servers.json`. `server add` updates the controller profile; `server setup` publishes its policy to the worker. Permissions are checked at queue time and again when executing. Policies use explicit app/domain lists, operation grants and per-service CPU/memory maxima. Revoke on the worker with `server setup`; running commands are not forcibly interrupted. A worker already running an older runtime adopts a new systemd unit on its next restart; do not restart it during migrations/restores.

## Project discovery, manifest and deployment

```bash
lta ops inspect ./my-project
lta ops init ./my-project --name demo --domain demo.example.com --output ./my-project/lta.json
# For a monorepo: add --context apps/api
lta deploy ./my-project --server production --dry-run
lta deploy ./my-project --server production --autonomous --wait
# A Git ref is resolved to a commit before snapshotting:
lta deploy https://github.com/your-org/your-app.git --ref main --server production --autonomous --wait
```

`ops init` infers a starting manifest; review the port, start/test commands and health endpoint once. It never overwrites a manifest. A Git checkout uses the controller's existing Git credentials; credentials embedded in URLs are rejected, Git hooks are disabled and no recursive submodules are fetched. Private dependencies must be supplied through your own reviewed build workflow; build-time secrets are not automatically injected.

Snapshots exclude `.git`, `.env*`, common key files, local agent directories, virtual environments and node_modules. Symlinks and special archive entries are rejected; source is limited to 100 MiB/20,000 files. These exclusions are not a substitute for keeping secrets out of tracked application files. The snapshot hash and Git commit (when applicable) identify deployed source. Dockerfile base tags can change on a future build; each completed release records immutable local image IDs. Keep the host's Docker images for rollback; LTA never runs a global prune.

Example minimal manifest:

```json
{
  "version": 1,
  "name": "demo",
  "domain": "demo.example.com",
  "runtime": "static",
  "port": 80,
  "health": {"path": "/", "status": 200, "contains": "Welcome"},
  "resources": {"memory_mb": 256, "cpus": 1, "min_disk_mb": 2048}
}
```

See `examples/deployment/` for Node, Python/PostgreSQL/Redis and CI examples. `build.context` and `build.dockerfile` must remain inside the source. `start`, `test`, `migration` and `build.command` are argument arrays. Tests run in a disposable, resource-limited container without a network or production secrets. For dependencies required by tests, provide an isolated test strategy in a custom image. An HTTP readiness path should check the application's necessary dependencies; `health.contains` can verify an expected response. All release containers must be running with healthy Docker healthchecks, if defined.

## Release transaction and recovery

The engine checks Docker/disk/domain conflicts, extracts the verified snapshot, prepares managed networks/dependencies, builds images, runs tests, takes a pre-migration PostgreSQL backup when needed, runs the declared migration, and starts an isolated candidate. Each app has private data/edge networks. Only its web service joins the gateway network. Container memory, CPU, PID and log limits are enforced per service; allow capacity for **both current and previous releases**, databases, Redis, build processes and the 256 MiB gateway. Build-time resource use is Docker/host controlled, not bounded by runtime Compose limits.

Caddy routes the domain to the candidate after internal readiness. Public HTTPS must return the expected status/body and a release-specific gateway header, preventing success against a different host or stale release. Certificate verification stays enabled. Failed verification restores the previous route; the previous app remains running for fast rollback. Older containers are stopped after successful subsequent releases; their artifacts, images and persistent volumes are retained. Application rollback never silently restores old database data. Failed candidates are stopped when it is safe to do so.

```bash
lta ops jobs --server production
lta ops jobs JOB_ID --server production
lta ops logs demo --server production
lta ops releases demo --server production
lta ops rollback demo --server production
lta ops rollback demo --release RELEASE_ID --server production
lta ops retry FAILED_JOB_ID --server production
```

Jobs and phase events are durable. Disconnecting SSH or cancelling `--wait` does not cancel the server job. A systemd worker survives reboots. Without systemd, submitting a job wakes a detached worker that exits after idle time; queued state still survives but reboot needs another wake/setup. One worker serializes host mutations. No uncertain application command is automatically replayed after process death: interrupted work becomes `needs_attention`, the committed pointer reconciles completed publication, and unfinished traffic switches attempt route recovery.

Migrations must be backward-compatible with the currently serving release (expand/contract). A crash during migration blocks further deployments for that app until the operator checks the DB and runs `ops retry JOB_ID --migration-resolved`; that explicit command skips the migration on the replacement attempt. Do not use this flag unless the migration is known complete or manually repaired. Restore failures likewise require inspection and a fresh explicitly authorized restore command.

While any job for an app needs attention, automatic repairs/backups and new unrelated deployments are suspended so a failed restore cannot accidentally restart writers. After manually inspecting/repairing a non-migration incident, acknowledge it with `lta ops resolve JOB_ID --server production --acknowledge`. This records an operator decision; it does not itself repair data or execute commands.

## Secrets and managed dependencies

```bash
lta ops secret api-secret --server production
lta ops secret api-database --server production --from-env API_DATABASE_PASSWORD
```

Values travel through SSH stdin, not shell arguments; files are private 0600 under the state directory. Manifests store references, not values. This is a permission-protected local secret store, **not an encrypted vault**; protect host backups accordingly. Generated private Compose files and container environments necessarily contain runtime values. Raw command output is not exposed in audit events/dashboard, and no secret is sent to an AI model.

`environment` maps literal strings; `secrets` maps environment variable names to stored secret names for the primary service. `service_secrets` can provide explicit variables to Compose workers, e.g. `{"worker":{"API_TOKEN":"api-secret"}}`. Secret dollar signs are escaped against Compose interpolation. Redeploy to apply an application secret rotation. PostgreSQL credentials cannot be rotated by changing a secret file alone; LTA detects the mismatch and stops rather than pretending the DB password changed.

Managed `database` uses PostgreSQL 16 with a persistent named volume, no host port, and injects `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`. Managed Redis 7 uses AOF persistence and injects `REDIS_HOST`/`REDIS_PORT`. Those names resolve on the app's private data network. `volumes` maps a stable volume name to a primary-service path under `/data/`. No host filesystem or Docker socket is mounted into applications. LTA labels managed objects and refuses to adopt unrelated objects with colliding names.

## Backup, restore and new-host recovery

```bash
lta ops backup api --server production
lta ops jobs JOB_ID --server production
lta ops drill api BACKUP_ID --server production
lta ops export-backup api BACKUP_ID ./api.dump --server production
lta ops import-backup api ./api.dump --server replacement
lta ops restore api BACKUP_ID --server replacement --allow-data-restore
```

PostgreSQL backups use `pg_dump -Fc`; drills restore to a temporary database and drop it after validation. Restore uses a single transaction with exit-on-error and takes a safety backup first. Managed file volumes and Redis are also snapshotted. They require a **maintenance pause** of current/previous application writers (and Redis during its snapshot); the engine resumes them afterward. Database-only dumps do not stop applications. Volume snapshots reject links/special files, are checksum-verified, and drills restore them into an isolated disposable volume. A failed destructive volume restore leaves writers stopped for recovery. Scheduling uses database `backup_hours`/`retain` or `monitor.backup_hours`/`backup_retain` for volume-only applications.

Each returned backup ID refers to one database or volume artifact. Keep the full cohort from a job to recover DB/files together. Export streams data through SSH and writes a companion `.json` checksum/metadata file. Transfer both. Backups remain on the source server until exported; same-host backups do not survive host loss. Import verifies the application/checksum; restore verifies that the target DB/volume exists in the app manifest. On a fresh host: register/setup it, restore secret references, deploy the same reviewed source/config to provision dependencies, import the complete backup cohort, restore it, run health/drill checks, then move DNS. Use a temporary staging domain during rehearsal to avoid redirecting production prematurely. This procedure is intentionally explicit; do not claim unattended disaster recovery without a tested external backup destination and credentials.

## DNS, monitoring, diagnostics and dashboard

Point DNS at the server yourself, or add a `dns` block with `provider: "cloudflare"`, the 32-character `zone`, `token_secret`, and public IP `address`. Grant the profile the separate `dns` operation and store a zone-scoped token. LTA reconciles exactly the allowed domain's A/AAAA record, rejects conflicting CNAME/address sets and disables Cloudflare proxying for direct HTTPS verification. DNS changes are not automatically reversed on later deployment failure. HTTP/HTTPS inbound connectivity and DNS propagation are prerequisites for certificate issuance.

```bash
lta ops watch --server production
lta ops diagnose api --server production
lta ops status --server production
lta ops dashboard --server production
```

The systemd worker checks internal/public health, container state/OOM/restarts, Docker CPU/memory/network/disk I/O, free disk and TLS expiry. It schedules authorized backups and records state-change alerts in `alerts.jsonl`. `monitor.restart` defaults false; when enabled with the `restart` permission, the primary app can be restarted at most `max_repairs` times **per release**, separated by `cooldown` seconds. Attempts consume the budget even when they fail. Repairs are verified; no generated shell repair commands run. Diagnosis distinguishes application/dependency, DNS/TLS/gateway, memory-limit, low-disk and Docker failures; it is a bounded heuristic, not guaranteed root-cause inference.

The read-only dashboard binds only to `127.0.0.1:8787`, checks Host, escapes content and exposes no mutation endpoints or secrets. `ops dashboard` creates a local SSH tunnel. HTTP status JSON is at `/api/status`. `ops logs` is the phase audit trail; application logs are available to authorized server operators through Docker. External notification channels are not contacted automatically.

## CI/CD and validation

Copy `examples/deployment/github-actions.yml` to an application repository and configure its environment variables/secrets. Pin the reviewed LTA commit, use branch/environment restrictions and separate staging/production manifests/profiles. A successful `--wait` exits 0 only after HTTPS and stabilization; failure/noncompletion returns nonzero. Controller authentication and policies are set once; routine releases need no interactive steps.

Unit/integration tests cover policy boundaries, tar traversal, secret escaping, SSH arguments, real CLI/zipapp execution, transaction failure/recovery, migrations, backup integrity and drills. `tests/test_deployment_docker.py` is opt-in with `LTA_DOCKER_INTEGRATION=1` on an isolated Linux Docker runner, and runs as a separate GitHub Actions job. It exercises real Docker builds, container HTTP readiness and failed-candidate preservation; it does not provision public DNS/TLS. A live server/DNS credential test is still necessary before relying on a new environment for production.

Implementation references: [Docker installation](https://docs.docker.com/engine/install/ubuntu/), [Compose configuration](https://docs.docker.com/reference/cli/docker/compose/config/), [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [PostgreSQL restore](https://www.postgresql.org/docs/16/app-pgrestore.html), [Cloudflare DNS](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/).
