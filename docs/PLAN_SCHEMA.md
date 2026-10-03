# Plans and skill packs

Plans are JSON objects containing `goal`, optional `summary`, and 1–12 `steps`
(configurable with `max_steps`). Unknown fields are rejected. A step requires a
unique `id`, `title`, `tool`, and `args`. Optional fields are `depends_on` (earlier
IDs only), `accept_exit_codes` (default `[0]`) and `verify` (default `[]`).

Model-generated labels are normalized when the mapping is unambiguous: numeric,
missing, malformed or unreferenced duplicate IDs receive unique string labels.
Dependencies referencing repeated IDs, unknown IDs, themselves or future steps
remain invalid. Only labels/references change; actions and verification requirements
do not. Imported/manual plans remain strict. An invalid model response receives
at most one schema-correction request, subject to model call and cost limits.
Ollama planning requests include a JSON schema in `format`.

Every modifying tool or unrecognized shell action needs an explicit verification.
Each verification has `tool`, `args`, optional `expected_exit` (default `0`) and
optional `contains` (a case-sensitive literal required in stdout). All checks must
pass. File writes and service mutations are forbidden as verifications. Shell
verification must be a recognized inspection command. Verifications themselves
are reviewed through the same approval policy.

Supported actions (exact argument keys):

- `shell`: `command` string. Bash on Linux; unknown/composed commands need review.
- `read_file`: `path` string. UTF-8 file inside the project, up to 256 KB.
- `write_file`: `path`, `content` strings. Parent must exist. Preview, approval,
  conflict check, atomic replacement and backup are required.
- `change_directory`: `path` string. Existing directory inside project.
- `service`: `name`, `action` strings. Inspection: `status`, `show`, `logs`,
  `is-active`, `is-enabled`. Mutations: `start`, `stop`, `restart`, `reload`,
  `enable`, `disable`. Uses fixed argv with systemctl/journalctl, no shell.
- `http_check`: `url` string. HTTP(S), no embedded credentials or redirects;
  response limited to 8 KiB, non-2xx status fails. Proxy environment ignored.
- `inspect`: `kind` string, one of `git_status`, `git_diff`, `git_log`,
  `docker_containers`, `docker_resources`, `nginx_config`. Uses fixed argv.
  Nginx validation asks for review because it can open configured log files.
- `network_traffic`: integer `seconds` (1–60) and `interval` (1–10, no greater
  than `seconds`). Samples Linux `/proc/net/dev` interface counters and prints
  receive/transmit rates in KiB/s. Duration cannot exceed the configured timeout.
  No packet contents are captured and no package or sudo is needed. Stops after
  the selected duration; Ctrl+C interrupts earlier.

```json
{
  "goal": "Check application configuration",
  "summary": "Inspect local config and verify the health endpoint",
  "steps": [
    {
      "id": "config",
      "title": "Read config",
      "tool": "read_file",
      "args": {"path": "app.conf"}
    },
    {
      "id": "health",
      "title": "Verify HTTP health",
      "tool": "http_check",
      "args": {"url": "http://127.0.0.1:8080/health"},
      "depends_on": ["config"],
      "verify": [
        {"tool": "http_check", "args": {"url": "http://127.0.0.1:8080/health"}, "contains": "healthy"}
      ]
    }
  ]
}
```

Changing the success string to a meaningless assertion weakens verification; the
schema checks structure, not the semantic correctness of a model's plan. Review
the proposed success criteria along with the actions.

A skill pack wraps a plan as `{"name": "my-skill", "description": "...", "plan": {...}}`.
Names use lowercase letters, numbers, underscores and hyphens, begin with a letter,
and have at most 48 characters. There is no variable interpolation or embedded
plugin code. Install with `lta skills install PATH`; edit a copy/exported plan to
adapt concrete paths and arguments. Files are limited to 300 KB.

Use `examples/config-repair.skill.json` only in a disposable project with an
`app.conf` file. It demonstrates a real diff, verification and rollback; it is not
a universal configuration repair recipe.
