# Linux Terminal Agent (LTA)

A dependency-free Python assistant for Linux: inspect a problem, review a plan,
execute bounded tools, and verify observable outcomes. English and Persian prompts
are supported by the model providers and common offline rules.

## Install

Python 3.8+ and Linux/Bash are required for shell execution. Windows can run the
planner, file tools and tests; run Linux commands inside WSL. Service tools require
systemd, and optional skills require their respective binaries (Git, Docker, Nginx).

```bash
./install.sh
# Or, in a virtual environment:
python -m pip install .
lta --help
```

The core uses only the Python standard library. Nothing downloads a model or sends
requests to a cloud provider until you explicitly select/configure one. The default
backend is Ollama on loopback; common exact inspection requests can use local rules.
Unknown requests and provider failures do not silently become executable commands.

## Quick start

```bash
# Single command: review first, or preview without execution.
lta -p rule_based -d "show disk usage"
lta -p ollama "show free memory"

# Multi-step work with observed results and explicit verifications.
lta --agent -p ollama "Inspect this project and fix its configuration problem"

# Bounded feedback loop: inspect results, review with the model, propose next steps.
lta --agent --adaptive --max-rounds 3 -p ollama "Diagnose why my local website is down"

# Forbid cloud models AND remote model endpoints.
lta --agent --local-only -p ollama "Inspect the current project"

# Diagnose a service without calling a model.
lta doctor nginx --url http://127.0.0.1/health
# Propose a restart after diagnostics; mutations still require explicit approval.
lta doctor nginx --repair --url http://127.0.0.1/health
```

`doctor --repair` proposes a restart, not an unconditional repair of every cause.
For Nginx, configuration validation must pass first. Restart is followed by
`is-active`, and an HTTP 200 check when a URL is supplied. Configuration repair
inside `/etc` is deliberately outside the project's structured file tools.

## Review and execution

- `-y` bypasses prompts only for a small set of recognized inspection operations.
  Unknown commands, shell composition, file edits, service changes and remote HTTP
  checks still need approval. Enter defaults to pause/skip.
- Edited shell commands go through policy and confirmation again.
- A plan shows ordered steps, dependencies, tool arguments, file diffs and outcome
  checks. Changes and unknown shell steps require an explicit verification in plans.
- Failed or unverified steps stop the plan. A successful process exit alone is not
  reported as a verified goal. Diagnostic steps can declare expected nonzero codes.
- Ctrl+C cancels a running process. Timeouts kill the Linux process group; captured
  output is bounded to 64 KiB per stream and live output is displayed as it arrives.
- Commands have no interactive stdin. Use noninteractive commands; authenticate
  `sudo` yourself before running approved service changes. Full-screen editors and
  password prompts belong in a separate terminal.
- File tools are restricted to the chosen project, reject symlinks and protected
  paths, show a diff, and save a private backup before an atomic replacement.
  They detect changes between preview and write. Rollback refuses to overwrite
  subsequent edits. File text is limited to 256 KB.

The command policy is a conservative approval aid, **not a shell sandbox**. Bash
can access everything your account can access after approval. The policy does not
prove arbitrary code safe; use a disposable environment for untrusted workloads.
Regex/allowlist checks and secret redaction are not comprehensive security boundaries.

## Sessions and recovery

```bash
lta sessions
lta sessions latest
lta resume                       # continue pending work in this project
lta resume --retry               # explicitly retry a failed/interrupted step
lta resume --replan -p ollama     # new plan based on saved evidence
lta resume --adaptive -p ollama
lta export-plan plan.json
# Edit the JSON in your editor, then review it:
lta run-plan plan.json --dry-run
lta run-plan plan.json
lta rollback BACKUP_ID           # always asks; -y does not bypass this
```

Use `--project /path/to/project` consistently to select a project's sessions.
Current directories persist across plan steps via `change_directory`. Successful
steps are not replayed on resume. An execution journal is written before effects;
a crash leaves work requiring an explicit retry. OS locks prevent concurrent plan
execution and rollback within a project. A changed session must be reloaded.

Sessions and backups live under
`$XDG_DATA_HOME/linux-terminal-agent/projects/<project-hash>` (default
`~/.local/share`). JSON writes are atomic and private on POSIX. The old global
history is not imported into project histories automatically. Backups retain file
contents locally; known secrets and protected files are excluded from file editing.
Dry runs may save session metadata but do not execute tools or modify target files.

Known credential patterns, secret assignments, environment credentials and loaded
configuration credentials are filtered from model input and stored evidence. This
is best effort: review context before using a cloud model. `--local-only` applies to
**model traffic**; it is not a network sandbox for approved shell or HTTP tools.
Local model transport ignores proxy environment variables and refuses redirects.

## Skills

Built-in packs: `git`, `docker`, `network`, `systemd`, `nginx`.

```bash
lta skills list
lta skills run git -y
lta skills run systemd --service ssh
lta skills run network --dry-run
lta skills install examples/config-repair.skill.json
lta skills run config-repair --project /path/to/demo
```

Installed packs are local declarative JSON, not Python plugins. Installation
validates their schema and never executes their actions. Every run uses the same
policy and review workflow. Existing names cannot be overwritten implicitly.
See [plan and skill format](docs/PLAN_SCHEMA.md).

## Models, privacy and usage

Providers: `ollama`, `local` (OpenAI-compatible), `rule_based`, `openai`, `groq`,
`anthropic`, `gemini`, `openrouter`. Set a model supported by your chosen endpoint;
packaged defaults are examples, not an assertion of current vendor availability.

```bash
lta config set provider ollama
lta config set ollama.model qwen2.5-coder:7b
lta config set local.endpoint http://127.0.0.1:8080/v1
lta config set local_only true
lta config set timeout 60
lta config set max_steps 12
lta config set max_model_calls 20
lta config set route_simple false
lta test ollama
```

API keys can use `OPENAI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`/`GOOGLE_API_KEY`, or `OPENROUTER_API_KEY`. Cloud use is rejected
while `local_only` is true. There is no automatic local-to-cloud failover.

The explicit `-m` override wins; otherwise the active provider's model is used.
The legacy top-level `model` applies only to the configured default provider.
Set it to an empty string to rely on per-provider models. Configuration values
accept JSON types (`false` is a boolean, not a truthy string).

Usage displays reported input/output tokens, latency, model calls and cost at your
configured rates. Missing token counts or prices remain visibly unknown. Prices
are never invented or downloaded. Configure USD per million tokens as JSON:

```bash
# Illustrative rates ONLY; substitute your actual provider/model and prices.
lta config set pricing '{"openai/YOUR_MODEL":{"input":1.0,"output":3.0}}'
lta config set budget_usd 0.50
```

The budget uses a conservative next-call reservation and requested output limit.
A budgeted cloud call without pricing is refused. This is an estimate-based guard,
not a guarantee of the vendor's invoice. Failed requests count toward the call
limit and retain their reservation when usage is unavailable. Usage persists in
multi-step sessions; single-command usage is scoped to the current process.

## Interactive terminal

Run `lta` for the REPL. Commands include `agent <goal>`, `sessions`, `resume`,
`cd <directory>`, `history`, `info`, `help`, and `exit`. Plans and live results use
a dependency-free terminal dashboard; text remains usable without ANSI color.
Persian strings are stored as UTF-8; visual right-to-left shaping depends on your
terminal. This is a line-oriented interface, not a full-screen terminal emulator.

## Validation

```bash
python -B -m unittest discover -s tests
lta evaluate
lta evaluate --output evaluation.json
# Optional isolated Linux run; needs Docker and an image download on first build:
docker build -f tests/Dockerfile -t lta-tests .
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m --cap-drop ALL lta-tests
```

Tests cover policy regressions, provider payloads through mocked transports, model
routing/budgets, redaction, project isolation, previews/backups/conflicts, resume,
verification, adaptive feedback, real loopback HTTP and subprocess supervision.
The offline evaluator exercises repair/rollback without unexpected files, a broken
HTTP service, an occupied port, protected paths and a hung process. It reports
scenario success; it does not measure real cloud-model reasoning quality or claim
host systemd integration. CI runs Windows/Linux tests and an unprivileged container.

MIT license. See [Persian guide](README_FA.md).
