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

---

## This Deployment (Peter's WSL2 Install)

Context for Claude Code sessions working on this specific installation.
Last updated: 2026-05-30.

### Environment

- **Single Hermes install** running in **WSL2 Ubuntu** on a Windows machine. The root filesystem lives on the D: drive.
  - `D:\home\peter\.hermes\` and `\\wsl.localhost\Ubuntu\home\peter\.hermes\` are the **same files** — two views of the same WSL filesystem.
- **Run shell commands in WSL:** `wsl -d Ubuntu -- bash -lc "…"`
  - The plain Git-Bash shell is a sandbox — it does NOT reach the real install. Only `wsl -d Ubuntu` (or paths via `\\wsl.localhost\Ubuntu\…`) does.
- **Hardware:** Ryzen 3 4300U, 15 GB RAM. WSL is configured with 9 GB RAM + 16 GB swap (`.wslconfig`). Browser + agents + Docker can exhaust this — avoid running the worker profile (`hermes -p worker`) casually.

### Running Services

| Service | How to check | Notes |
|---------|-------------|-------|
| Gateway | `systemctl --user status hermes-gateway.service` | Auto-starts via systemd user + linger + `hermes-wsl-autostart.vbs` on Windows logon |
| Dashboard | `systemctl --user status hermes-dashboard.service` | http://localhost:9119. Stop with `systemctl --user stop hermes-dashboard.service` — NOT `hermes dashboard --stop` (service restarts it) |

### Models (OpenRouter)

- **Interactive / `default` profile:** `z-ai/glm-4.5-air:free` (free tier)
- **Background / workers / auxiliary:** `deepseek/deepseek-v4-flash` (pennies, reliable)
- Keys: `.env OPENROUTER_API_KEY`, also mirrored in `config.yaml openrouter.api_key`
- `auxiliary.*` models are routed to `provider: openrouter` + `deepseek-v4-flash`. Do NOT revert to `provider: auto` — it 404s on the Nous endpoint.

### Active Platforms

- **Telegram:** connected, bot `@Peters_hermesagent_bot`
- **api_server:** port 8642
- **webhook:** port 8644
- Discord: disabled

### Multi-Agent / Kanban

Dispatcher is embedded in the gateway (60s poll). Three profiles:

| Profile | Role | Model |
|---------|------|-------|
| `default` | interactive / free | GLM-4.5-air:free |
| `director` | orchestrator | deepseek-v4-flash |
| `worker` | executor | deepseek-v4-flash |

### Cron Routines

**All cron jobs must be in the `default` profile** — the gateway only fires the running profile's jobs.

| Job | Schedule | Notes |
|-----|----------|-------|
| Morning briefing | `0 8 * * *` | |
| Finance monitor | `30 16 * * 1-5` | Alpaca, `[SILENT]` tag |
| AI news watcher | `0 9 * * *` | |
| Plaud new-notes sync | `0 7 * * *` | `--no-agent`, runs `~/.hermes/scripts/plaud_pull_new.py` |

Check with `hermes cron list` / `hermes cron status`.

### MCP Servers (active)

Notion, Filesystem, Firecrawl/fetch, Playwright, Sequential-Thinking, Alpaca (paper), yfinance.
Disabled (broken upstream): sqlite, puppeteer.

### Browser Automation

- System **Google Chrome** at `/usr/bin/google-chrome` + Playwright at `~/.hermes/state/pw/`
- Playwright's bundled Chromium does NOT install on Ubuntu 26.04 ("resolute") — always use system Chrome
- Visible window requires WSLg (`DISPLAY=:0`); headless works without it

### Key Files Under `~/.hermes/`

| Path | Purpose |
|------|---------|
| `config.yaml`, `.env` | Main settings and secrets |
| `config.yaml.bak.*` | Backups (including `.preFix_`, `.preAux`) |
| `profiles/{default,director,worker}/` | Per-profile config and .env |
| `skills/integrations/vinsolutions-followups/SKILL.md` | VinSolutions follow-up workflow (draft & confirm safety) |
| `skills/playwright-mcp-wsl-setup/SKILL.md` | Corrected Playwright/WSL setup instructions |
| `scripts/plaud_pull_new.py` | Plaud notes sync script |
| `state/pw/` | Playwright state |
| `state/vinsolutions-profile/` | Persistent VinSolutions browser session |
| `state/plaud-cache/`, `state/vin-discovery/` | Plaud cache, VinSolutions login screenshots |
| `logs/{agent,errors,gateway}.log` | Live logs |

Dev reference (Windows side): `C:\Users\peter\.claude\projects\C--Users-peter\memory\hermes_wsl_migration.md` + `hermes_architecture_reference.md`.

### Credential Locations (not values)

- OpenRouter: `.env OPENROUTER_API_KEY`
- Firecrawl: `.env FIRECRAWL_API_KEY`
- Telegram: `.env TELEGRAM_BOT_TOKEN`
- VinSolutions/Cox Bridge SSO: `.env VINSOLUTIONS_USER` / `VINSOLUTIONS_PASS`; 2FA → email code (see `.env` for address)
- Alpaca (paper), Notion: `config.yaml mcp_servers.*.env`
- Plaud token: `~/.plaud/.env`

### Pending Action Items

1. **Plaud token — DEAD.** Session was invalidated server-side; the token in Chrome == the one in `~/.plaud/.env`, both 401. Fully log out then log back in at https://web.plaud.ai (not just reopen the tab), then re-extract the token.

2. **Telegram home channel — not set.** `TELEGRAM_HOME_CHANNEL` is unset, so cron routines compute but don't deliver. DM `@Peters_hermesagent_bot` once, then set the returned chat ID as the home channel in config.

3. **VinSolutions follow-up skill — login automation in progress.** Cox Bridge SSO flow: username → Next → password → Sign in → "Verify your identity" → choose Email → code field. Sub-agent was completing the 2FA + screen-mapping; check `~/.hermes/state/vin-discovery/` screenshots. For fully autonomous runs, Hermes needs Gmail MCP access to read the 2FA code (code goes to the email in `.env VINSOLUTIONS_2FA_EMAIL`). **Skill safety: drafts messages for approval by default — do not change without explicit instruction.**

4. **`hermes update` — queued.** Back up `config.yaml` first; verify custom fixes (auxiliary model routing, etc.) survive; restore from `.bak` if needed.

### Operational Gotchas

- **WSL instability under load** can throw `Wsl/Service/E_UNEXPECTED`. Recover: `wsl --terminate Ubuntu` then `wsl -d Ubuntu`. Never `wsl --shutdown` mid-task (kills gateway and dashboard).
- **Per-profile cron:** gateway only fires the running profile's (`default`) jobs. Keep all routines in `default`, or use `--profile` flag (sequential, not parallel).
- **`hermes -p worker …` boots the worker profile's full MCP stack** — heavy enough to wedge WSL on this machine.
- **`~/.hermes/kanban/app.py` (port 5675) is corrupted** — a leftover with wrong content. The real dashboard is `hermes dashboard` at port 9119.
- **Native Windows Hermes is retired.** Scheduled tasks `Hermes_Gateway` / `Hermes_Gateway_donna` were disabled and the orphaned process killed. Do not re-enable — it fights WSL over the Telegram bot token.
