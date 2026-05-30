# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Hermes Agent is a self-improving AI agent by Nous Research. It runs a persistent conversation loop with any OpenAI-compatible LLM, calls tools (terminal, file ops, web, browser, delegation, etc.), persists sessions in SQLite, and delivers responses via CLI, Telegram, Discord, Slack, WhatsApp, and other messaging platforms. It has a built-in learning loop: the agent creates and curates skills from experience.

## Development Setup

```bash
# Clone and install
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"

# Configure (minimum: one LLM provider key)
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.hermes/config.yaml
echo "OPENROUTER_API_KEY=***" >> ~/.hermes/.env
```

The `./setup-hermes.sh` script automates all of the above.

## Commands

```bash
# Run the agent
./hermes                  # start interactive CLI (auto-detects venv)
hermes --tui              # Ink-based terminal UI

# Tests (always use the wrapper — matches CI behavior)
scripts/run_tests.sh                          # full suite (per-file isolated subprocesses)
scripts/run_tests.sh tests/agent/             # single directory
scripts/run_tests.sh tests/foo.py             # single file
scripts/run_tests.sh tests/foo.py -- --tb=long  # with extra pytest flags

# Lint
ruff check .              # blocking lint (PLW1514 enforced)
ruff check --diff .       # advisory diff

# TUI development
cd ui-tui
npm install
npm run dev               # watch mode
npm run type-check        # tsc --noEmit
npm run lint              # eslint
npm test                  # vitest

# Cross-platform safety check (run before PRs touching OS/path/process code)
scripts/check-windows-footguns.py
```

Tests run in a hermetic environment (`TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0`, clean env). Use `unittest.mock` and `monkeypatch`; no live network calls in tests.

## Architecture

### File Dependency Chain

```
tools/registry.py   (no deps — imported by all tool files)
      ↑
tools/*.py          (each calls registry.register() at import time)
      ↑
model_tools.py      (imports registry, triggers tool auto-discovery)
      ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

### Core Agent Loop (`run_agent.py` — `AIAgent` class, ~12k LOC)

`AIAgent.run_conversation()` is the main synchronous loop:

1. Build system prompt (`agent/prompt_builder.py`) — assembles identity, skills, context files, memory
2. Call LLM via OpenAI-compatible API
3. If tool calls in response → dispatch via `handle_function_call()` → append results → repeat
4. If text response → persist session to SQLite → return
5. Context compression triggers automatically near token limit (`agent/context_compressor.py`)

The agent takes ~60 init parameters. Key ones: `base_url`, `api_key`, `provider`, `model`, `max_iterations` (default 90), `enabled_toolsets`, `platform`, `session_id`.

### Tool System (`tools/` + `toolsets.py`)

**Self-registering**: every `tools/*.py` file calls `registry.register()` at import; `discover_builtin_tools()` in `tools/registry.py` imports them all automatically. No manual import list.

**Toolsets**: tools are grouped into named sets (`web`, `terminal`, `file`, `browser`, `delegation`, `skills`, `memory`, `cron`, etc.) defined in `toolsets.py`. Each platform picks a base toolset; users can enable/disable via `hermes tools` or `config.yaml`. A tool registers but stays hidden unless its name appears in an active toolset — `_HERMES_CORE_TOOLS` is the default bundle.

**Adding a built-in tool** requires changes in exactly 2 files:
1. Create `tools/your_tool.py` with schema + handler + `registry.register()` call
2. Add the tool name to the appropriate list in `toolsets.py`

All handlers must return a JSON string. Use `get_hermes_home()` for any persistent state paths (never `~/.hermes` hardcoded).

For custom/local tools, use the plugin route (`~/.hermes/plugins/<name>/`) instead of editing core.

### CLI Architecture (`cli.py` — `HermesCLI` class, ~11k LOC)

- **prompt_toolkit** for input with autocomplete; **Rich** for banner/panels
- **KawaiiSpinner** (`agent/display.py`) animates during API calls
- Slash commands are defined in a central `COMMAND_REGISTRY` (`hermes_cli/commands.py`) as `CommandDef` objects. All downstream consumers (CLI dispatch, gateway hooks, Telegram menus, Slack routing, autocomplete, help text) derive from this single registry automatically.
- **Skin engine** (`hermes_cli/skin_engine.py`) — data-driven theming (colors, spinner faces, branding). Skins are pure YAML data; no code changes needed to add one.

**Adding a slash command**: (1) add `CommandDef` to `COMMAND_REGISTRY`; (2) add handler in `HermesCLI.process_command()`; (3) if gateway-available, add handler in `gateway/run.py`.

### TUI (`ui-tui/` + `tui_gateway/`)

Activated via `hermes --tui`. Node (Ink/React) owns the screen; Python (`tui_gateway/`) owns sessions, tools, and model calls. They communicate via newline-delimited JSON-RPC over stdio.

**Do not re-implement the primary chat experience in React.** The transcript and composer belong to `hermes --tui` embedded via PTY. The dashboard (`hermes dashboard`) embeds the real TUI via `hermes_cli/pty_bridge.py` — anything added to Ink appears in the dashboard automatically. Structured React UI around the TUI (sidebars, inspectors, status panels) is fine.

### Messaging Gateway (`gateway/`)

`GatewayRunner` (`gateway/run.py`) orchestrates platform adapters, message routing, and cron. Each platform (`gateway/platforms/`) adapts incoming messages into the shared agent session model. See `gateway/ADDING_A_PLATFORM.md` for the protocol.

### Session Persistence (`hermes_state.py`)

`SessionDB` stores all conversations in SQLite with FTS5 full-text search and unique session titles. Per-session JSON snapshots are off by default (opt in with `sessions.write_json_snapshots: true`).

### Config System

Three separate loaders — use the right one:

| Loader | Used by |
|--------|---------|
| `load_cli_config()` in `cli.py` | Interactive CLI |
| `load_config()` in `hermes_cli/config.py` | `hermes tools`, `hermes setup`, subcommands |
| Direct YAML load in `gateway/run.py` | Gateway runtime |

**Adding config options**: add to `DEFAULT_CONFIG` in `hermes_cli/config.py`. Only bump `_config_version` when migrating/transforming existing user config (renaming keys, changing structure) — adding a new key does not require a bump.

**Secrets only in `~/.hermes/.env`**: non-secret settings (timeouts, thresholds, flags, paths) belong in `config.yaml`. Add new secrets to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py`.

### Plugin System

**General plugins** (`hermes_cli/plugins.py`): discovered from `~/.hermes/plugins/`, `./.hermes/plugins/`, and pip entry points. Expose `register(ctx)` to add tools, lifecycle hooks, and CLI subcommands. Plugins must NOT modify core files — expand the plugin surface instead.

**Memory plugins** (`plugins/memory/<name>/`): implement `MemoryProvider` ABC (`agent/memory_provider.py`). **The set of in-tree memory providers is closed** — new backends must ship as standalone repos. Bug fixes to existing in-tree providers are welcome.

**Model-provider plugins** (`plugins/model-providers/<name>/`): each calls `providers.register_provider(ProviderProfile(...))` at load. Lazy discovery — only on first `get_provider_profile()` or `list_providers()` call.

### Skills System

**Skills** are markdown instruction files (`SKILL.md`) that the agent loads into its system prompt. They live in:
- `skills/` — built-in, active by default
- `optional-skills/` — official but not activated by default; installed via `hermes skills install official/<cat>/<skill>`

**Skill vs. Tool**: make it a skill if it can be expressed as instructions + shell commands. Make it a tool only when it requires auth flows, binary data, streaming, or exact-precision execution that can't be delegated to LLM interpretation.

**Skill authoring hardlines** (reviewers reject violations):
1. `description` ≤ 60 characters, one sentence, ends with period, no marketing words
2. Reference native Hermes tools by name in backticks (e.g. `` `terminal` ``, `` `web_extract` ``, `` `read_file` ``); don't name raw shell utilities like `grep`, `cat`, `sed`
3. Gate `platforms:` only when genuinely platform-bound (try cross-platform fix first)
4. Credit human contributor as `author`, not "Hermes Agent"
5. Section order: `## When to Use`, `## Prerequisites`, `## How to Run`, `## Quick Reference`, `## Procedure`, `## Pitfalls`, `## Verification`
6. Skill tests at `tests/skills/test_<skill>_skill.py`; stdlib + pytest + `unittest.mock` only

### Dependency Pinning Policy

All deps must have upper bounds — this was tightened after supply-chain incidents (litellm compromise, Mini Shai-Hulud worm). Rules:

| Source | Treatment |
|--------|-----------|
| PyPI package | `==exact` pin in core deps; `>=floor,<next_major` in extras |
| Git URL | 40-char commit SHA |
| GitHub Actions | Commit SHA + version comment |

When adding to `pyproject.toml`: pin exactly, then run `uv lock` to regenerate `uv.lock`. Never commit a bare `>=X.Y.Z` without a ceiling.

## Cross-Platform Rules

Hermes runs on Linux, macOS, and native Windows. Key rules for cross-platform code:

- **Never `os.kill(pid, 0)`** — on Windows, signal 0 maps to `CTRL_C_EVENT` and kills the process group. Use `psutil.pid_exists(pid)` instead.
- **Use `shutil.which()`** before shelling out — POSIX tools don't exist on Windows.
- **`termios`/`fcntl` are Unix-only** — always catch `ImportError` + `NotImplementedError`.
- **File encoding** — use `encoding="utf-8"` explicitly on all `open()` calls (PLW1514 is enforced by ruff). Config files may have UTF-8 BOM — use `encoding="utf-8-sig"`.
- **Use `pathlib.Path`** instead of string path concatenation.
- **`os.setsid()`, `os.killpg()`, `os.fork()`** don't exist on Windows — gate with `platform.system()`.
- **Symlinks require elevated privileges on Windows** — add skip markers in tests.
- Run `scripts/check-windows-footguns.py` before submitting any PR touching OS/process/path code.

## Security

Hermes has terminal access. When writing security-sensitive code:
- Use `shlex.quote()` when interpolating user input into shell commands
- Resolve symlinks with `os.path.realpath()` before path-based access control
- Never log secrets
- Catch broad exceptions around tool execution to avoid crashing the agent loop
- The `execute_code` sandbox strips API keys from child process environment

## User Data Locations

All runtime state is profile-aware via `get_hermes_home()` — never hardcode `~/.hermes`.

| Path | Purpose |
|------|---------|
| `~/.hermes/config.yaml` | Settings |
| `~/.hermes/.env` | API keys and secrets |
| `~/.hermes/state.db` | SQLite session store (canonical) |
| `~/.hermes/skills/` | Active skills |
| `~/.hermes/memories/` | Persistent memory (MEMORY.md, USER.md) |
| `~/.hermes/logs/` | agent.log, errors.log, gateway.log |
| `~/.hermes/cron/` | Scheduled job data |
