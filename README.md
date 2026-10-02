# Linux Terminal Agent (LTA) 🐧⚡

An intelligent, modular, and secure command-line agent for Linux that translates natural language prompts into precise terminal commands and executes them.

Built with **zero mandatory external dependencies** (runs on pure Python 3 standard library) and designed for extreme flexibility: run it **100% offline & private** (via Ollama or local LLM servers) or connect to **cloud AI backends** (OpenAI, Groq, Anthropic Claude, Google Gemini, OpenRouter).

---

## 🌟 Key Features

- 🔌 **Modular AI Providers**:
  - **Offline / Local**:
    - **Ollama**: Run models like `qwen2.5-coder`, `llama3`, `deepseek-coder`, or `mistral` locally with zero internet access.
    - **Local OpenAI-Compatible Server**: Connect to `llama.cpp` server, `LM Studio`, `vLLM`, or `LocalAI`.
    - **Rule-Based Engine**: Built-in zero-AI offline rule parser for common system tasks (works even before any LLM is downloaded).
  - **Online Cloud APIs**:
    - **Groq**: Ultra-low latency, high-speed execution.
    - **OpenAI**: GPT-4o, GPT-4o-mini.
    - **Anthropic Claude**: Claude 3.5 Sonnet, Claude 3 Haiku.
    - **Google Gemini**: Gemini 1.5 Flash, Gemini 1.5 Pro.
    - **OpenRouter**: Unified access to hundreds of models.
- 🛡️ **Multi-Tier Safety Guardrails**:
  - Automatically classifies commands into `SAFE`, `CAUTION`, `DANGEROUS`, or `BLOCKED`.
  - Permanently blocks catastrophic commands (e.g. `rm -rf /`, fork bombs, direct disk overwrites like `dd of=/dev/sda`, `chmod -R 777 /`).
  - Interactive confirmation prompt (`[y]es / [n]o / [e]dit / [q]uit`).
- 🧠 **Linux System Context Awareness**:
  - Automatically identifies your Linux distribution (Ubuntu, Debian, Arch, Fedora, Alpine, openSUSE, etc.), package manager (`apt`, `pacman`, `dnf`, `apk`), current shell (`bash`, `zsh`, `fish`), current working directory, and user privilege level (`root`, `sudo`, standard user).
- 💬 **Bilingual Support**:
  - Accepts queries in both **English** and **Persian (فارسی)**.
- 🚀 **Interactive REPL & One-Shot Modes**:
  - Launch an interactive terminal assistant session or run instant one-liners.
- 📜 **Audit History & Diagnostics**:
  - Built-in history logging (`lta history`), provider health checks (`lta test`), and environment inspection (`lta info`).

---

## 📦 Quick Installation

Run the automated one-step installer:

```bash
git clone https://github.com/nimajavan/linux-terminal-agent.git
cd linux-terminal-agent
chmod +x install.sh
./install.sh
```

The installer detects your user permissions and sets up the `lta` command in `/usr/local/bin` (for root) or `~/.local/bin` (for non-root users).

To uninstall:
```bash
./uninstall.sh
```

---

## 🚀 Usage Guide

### 1. One-Shot Command Execution
```bash
# General query (asks for confirmation before executing)
lta "find all files larger than 100MB in /var/log"

# Persian query
lta "میزان مصرف رم و دیسک را نشان بده"

# Auto-execute safe commands without confirmation prompt (-y)
lta -y "show all listening TCP ports"

# Dry-run mode: display command and safety analysis without executing (-d)
lta -d "install docker and start its service"
```

### 2. Interactive REPL Mode
Simply run `lta` without arguments or pass `-i`:
```bash
lta
```
Inside the interactive session:
```text
lta (my-project) > show git branches and recent commits
lta (my-project) > kill process running on port 8080
lta (my-project) > history
lta (my-project) > exit
```

---

## 🔌 Configuring AI Providers

### Option A: 100% Offline with Ollama (Recommended for Privacy)
1. Install Ollama:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. Pull a recommended coding model:
   ```bash
   ollama pull qwen2.5-coder:7b
   # or: ollama pull deepseek-coder:6.7b
   ```
3. Set Ollama as your default provider:
   ```bash
   lta config set provider ollama
   lta config set model qwen2.5-coder:7b
   ```
4. Verify connection:
   ```bash
   lta test ollama
   ```

### Option B: Offline with Local OpenAI-Compatible Server (llama.cpp / LM Studio / vLLM)
```bash
lta config set provider local
lta config set local.endpoint "http://localhost:8080/v1"
```

### Option C: Online Cloud APIs
You can either set environment variables or save your keys in the configuration:

- **Groq (Fast & Free Tier available)**:
  ```bash
  export GROQ_API_KEY="gsk_..."
  lta -p groq "check system uptime and load average"
  ```
- **OpenAI**:
  ```bash
  export OPENAI_API_KEY="sk-..."
  lta -p openai -m gpt-4o-mini "extract photos.tar.gz to /tmp"
  ```
- **Anthropic Claude**:
  ```bash
  export ANTHROPIC_API_KEY="sk-ant-..."
  lta -p anthropic "list all systemd failed services"
  ```
- **Google Gemini**:
  ```bash
  export GEMINI_API_KEY="AIzaSy..."
  lta -p gemini "clean apt package cache"
  ```

---

## 🛠️ CLI Reference

```text
Usage: lta [OPTIONS] [prompt ...]
       lta <subcommand>

Subcommands:
  info              Show Linux system environment and active agent configuration
  test [provider]   Test connectivity to local or online AI provider
  config [action]   Manage settings (list, get, set, init)
  history           View recent command execution history

Options:
  -p, --provider    Select AI backend (ollama, local, rule_based, openai, groq, anthropic, gemini, openrouter)
  -m, --model       Specify model name
  -y, --yes         Auto-execute safe commands without prompting
  -d, --dry-run     Formulate and display command without executing
  -i, --interactive Launch interactive REPL session
  --no-color        Disable colored output
  -v, --version     Show version information
  -h, --help        Show help message
```

---

## 🔒 Security Architecture

LTA inspects every generated command before it reaches the shell:
- **BLOCKED**: Dangerous destructive operations (`rm -rf /`, `:(){ :|:& };:`, raw disk writes `dd of=/dev/sd*`, `mkfs /dev/sda`, `chmod -R 777 /`) are **permanently blocked** and cannot be run.
- **DANGEROUS**: High-impact commands (`shutdown`, `reboot`, `iptables -F`, `fdisk`) require explicit user confirmation, even if `-y` was supplied.
- **CAUTION**: Service modifications (`systemctl restart`, `git reset --hard`, package removals) are flagged with yellow warning badges.
- **SAFE**: Read-only diagnostic commands (`ls`, `df`, `free`, `grep`, `systemctl status`) are highlighted with green badges.

---

## 🧪 Testing

Run the included unit test suite:
```bash
python3 -m unittest discover -s tests
```

---

## 📄 License
MIT License. Created by [nimajavan](https://github.com/nimajavan).
