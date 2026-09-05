# Agent Steward

> A provider-neutral local continuity, memory, feedback, and safety layer for
> AI coding agents.

**Unofficial, community-maintained project. Not affiliated with, endorsed
by, or supported by OpenAI, GitHub, or Anthropic.** The product names below
(OpenAI Codex CLI, Anthropic Claude Code, GitHub Copilot) refer to those
tools' hook systems, which this project integrates with as an external tool —
it does not modify or redistribute any of them. (The distribution/CLI keep the
`codex-*`/`exi` names for backward compatibility — see [Naming](#naming)
below; the feedback/memory engine is provider-neutral.)

## Naming

This project's display name is **Agent Steward**; the GitHub repository is
[`Torrant5/agent-steward`](https://github.com/Torrant5/agent-steward). For
existing installs, nothing under the hood changes: the Python distribution
name stays `codex-feedback-guard`, the console scripts stay
`exi` / `exi-hook` / `codex-guard`, the on-disk data/config directories stay
named `codex-feedback-guard` (see [Configuration](#configuration)), hook
identifiers are unchanged, and the `EXI_*` environment variables
(`EXI_CONFIG`, `EXI_DATA_DIR`, etc.) are unchanged. Only the repository
location and the project's public name have changed. Existing clones and
virtual environments do **not** need to be renamed: GitHub redirects the old
repository URL, and the old local folder names continue to work. The install
examples below use the new names; when updating an existing install, keep using
the paths where it is already installed.

Standard-library-only tools that share one design goal: make agent work
**accountable** — knowledge is shared with evidence, recurring corrective
feedback is captured and enforced without the user repeating themselves, and
long Codex runs cannot silently burn a week of quota in a day.

- **`exi`** — an *external intelligence* store. Capture observations with
  evidence, search them (SQLite FTS5), and surface confirmed knowledge as
  human-reviewable **promotion candidates**. It never edits `AGENTS.md`,
  `CLAUDE.md`, or any skill file for you.
- **Feedback / memory engine** (`exi-hook`) — automatic memory retrieval,
  **autonomous durable-memory capture** (the active model decides what is worth
  keeping; no magic words, no manual user commands), and zero-click
  corrective-feedback capture + graded enforcement, delivered through hooks on
  **four surfaces**: OpenAI Codex CLI, Anthropic Claude Code, and GitHub Copilot
  in both **VS Code (Copilot Agent)** and the **Copilot CLI**. One
  provider-neutral policy core; a thin adapter per surface.
- **`codex-guard`** — a **Codex-only** budget guard. Codex hooks watch each
  turn for runaway time, tool-call floods, tight loops, and weekly-quota
  burn, and *deny* the offending tool call before it runs. A `managed-run`
  supervisor can hard-stop a process **it launched** on time/quota overrun.
  (The budget/quota guard is never installed on Claude or Copilot — only the
  shared feedback/memory enforcement is.)

Python 3.11+, standard library only (no third-party runtime dependencies).
The provider-neutral feedback/memory layer runs on macOS, Linux, and
**Windows** (cross-platform locking and paths; see the
[portability matrix](#portability--surface-support)). Codex's optional
process-group supervisor (`managed-run`) remains POSIX-only. No network, LLM, or
metered API from any hook — the only subprocess is a local
`llm-quota`-compatible CLI *you* provide for the Codex budget guard (see
[Quota adapter](#quota-adapter) below).

---

## Install

The checkout can live in any **permanent** directory; it does not have to be
copied into the root of your home directory. Do not put it in a temporary
folder. The recommended setup below keeps the source and its Python virtual
environment under your home directory, then writes an **absolute** `exi-hook`
path into each agent's hook config. Hooks therefore keep working even when a
terminal has not activated the virtual environment and `exi-hook` is not on
`PATH`.

### Recommended per-user install (macOS/Linux)

```bash
EXI_SOURCE="$HOME/.local/src/agent-steward" \
  && EXI_VENV="$HOME/.local/share/agent-steward-venv" \
  && mkdir -p "$HOME/.local/src" "$HOME/.local/share" \
  && git clone https://github.com/Torrant5/agent-steward.git "$EXI_SOURCE" \
  && python3 -m venv "$EXI_VENV" \
  && "$EXI_VENV/bin/python" -m pip install "$EXI_SOURCE"
```

Install only the surfaces you actually use:

```bash
EXI_VENV="$HOME/.local/share/agent-steward-venv" \
  && "$EXI_VENV/bin/codex-guard" install-hooks

EXI_VENV="$HOME/.local/share/agent-steward-venv" \
  && "$EXI_VENV/bin/exi-hook" install claude --scope user \
  --exe "$EXI_VENV/bin/exi-hook"
```

The first command installs both the Codex budget guard and feedback/memory
hooks. The second installs Claude feedback/memory hooks. Omit either command if
you do not use that product. Approve/trust the generated hooks when the agent
asks; untrusted hooks do not run.

### Corporate Windows PC (GitHub Copilot in VS Code)

This is the recommended machine-wide setup for the stated Windows + Copilot
VS Code use case. It needs Python 3.11+ and Git, but no administrator access:

```powershell
$ExiSource = Join-Path $HOME "Tools\agent-steward"
$ExiVenv = Join-Path $HOME ".agent-steward-venv"
New-Item -ItemType Directory -Force (Split-Path $ExiSource) | Out-Null
git clone https://github.com/Torrant5/agent-steward.git $ExiSource
py -m venv $ExiVenv
& "$ExiVenv\Scripts\python.exe" -m pip install $ExiSource
& "$ExiVenv\Scripts\exi-hook.exe" install copilot-vscode `
  --scope user `
  --exe "$ExiVenv\Scripts\exi-hook.exe"
```

`--scope user` writes `%USERPROFILE%\.copilot\hooks\exi-feedback-vscode.json`,
so the feedback/memory hooks apply to every local VS Code workspace for that
Windows account. For one repository only, run this from that repository and
replace `--scope user` with `--scope project`. If your company uses **Copilot
CLI** rather than the VS Code agent, replace `copilot-vscode` with
`copilot-cli`; do not install both adapters into the same discovery scope.

Verify the installed file without changing it again:

```powershell
& "$ExiVenv\Scripts\exi-hook.exe" install copilot-vscode `
  --scope user `
  --exe "$ExiVenv\Scripts\exi-hook.exe" `
  --dry-run
& "$ExiVenv\Scripts\exi.exe" feedback candidates
```

The first command prints the merged hooks without writing; the second proves
the CLI can read its per-user state. An empty candidate list is normal before
you give corrective feedback.

### Updating an existing install

Set the source and virtual-environment variables to the directories that are
actually present on that machine. The examples below show the new default
names. An installation created from the older documentation normally uses
`~/.local/src/codex-feedback-guard` and
`~/.local/share/codex-feedback-guard-venv` on macOS/Linux, or
`$HOME\Tools\codex-feedback-guard` and
`$HOME\.codex-feedback-guard-venv` on Windows; keep those old values unless you
have deliberately renamed the local directories.

macOS/Linux:

```bash
EXI_SOURCE="$HOME/.local/src/agent-steward" \
  && EXI_VENV="$HOME/.local/share/agent-steward-venv" \
  && git -C "$EXI_SOURCE" pull --ff-only \
  && "$EXI_VENV/bin/python" -m pip install --force-reinstall --no-deps "$EXI_SOURCE"
```

Windows PowerShell:

```powershell
$ExiSource = Join-Path $HOME "Tools\agent-steward"
$ExiVenv = Join-Path $HOME ".agent-steward-venv"
git -C $ExiSource pull --ff-only
& "$ExiVenv\Scripts\python.exe" -m pip install --force-reinstall --no-deps $ExiSource
```

Re-running the matching `install` command after an update is safe and
idempotent. Existing hook entries are preserved and EXI entries are not
duplicated.

### Development checkout / traditional pip install

For development, cloning anywhere permanent and installing editable is also
supported:

```bash
git clone https://github.com/Torrant5/agent-steward.git \
  && cd agent-steward \
  && python3 -m pip install -e .
```

This installs three console scripts — `exi`, `codex-guard`, and **`exi-hook`**
(the unified hook entry point used by every surface). With an editable install,
keep the checkout at the same path. If you use `pip install --user .` instead,
confirm `exi-hook --help` works in a fresh terminal before installing hooks;
Python's per-user scripts directory is not on `PATH` in every OS setup.

Default config and defaults are packaged inside `exi/`. Per-user config/data
live under standard XDG paths (POSIX) or `%LOCALAPPDATA%` (Windows) — see
[Configuration](#configuration) — so no `EXI_*` environment variables are
required.

> Editable installs (`pip install -e .`) are recommended for development —
> `bin/codex-guard` resolves directly to your checkout. A non-editable wheel
> install works the same way; `codex-guard install-hooks` falls back to
> invoking the active interpreter with `-m exi.guardcli` instead.

---

## Architecture and features

The components share one package (`exi/`) and one config-loading layer
(`exi/config.py`), but are otherwise independent:

| Module | Responsibility |
|---|---|
| `exi/store.py` | Append-only observation log, FTS5 search, promotion candidates, **automatic relevance retrieval**, **durable-memory `remember()` (user-authoritative `active` vs evidence-disciplined `confirmed`)** |
| `exi/exicli.py` | `exi` command line (capture/search/list/promote/audit/confirm/verify/retire + `feedback` incl. `resolve`/`dismiss`/`candidates` + **`memory` incl. `resolve`/`dismiss`/`candidates`**) |
| `exi/feedback.py` | Feedback-rule store, declarative enforcement engine, severity ladder, **pending feedback- and memory-candidate lifecycles** |
| `exi/feedback_detect.py` | Robust prompt extraction + conservative JA/EN corrective-feedback detector + candidate/evidence id derivation (no model/API call) |
| `exi/secretscan.py` | Conservative, standard-library secret/credential rejecter for durable-memory content (no network/LLM) |
| `exi/feedback_core.py` | **Provider-neutral policy engine** — all feedback/memory policy as neutral outcome objects (single source of truth) |
| `exi/feedback_hook.py` | **Codex adapter** over `feedback_core` (normalize payload → encode Codex output); backward-compatible entry point |
| `exi/feedback_adapters.py` | **Claude / Copilot-VS Code / Copilot-CLI adapters** over `feedback_core` |
| `exi/hookcli.py` | `exi-hook` unified entry point: `exi-hook <provider> <event>` runtime + `exi-hook install <provider>` |
| `exi/hookgen.py` | Safe, idempotent hook installers/generators for Claude / Copilot (backup, dry-run) |
| `exi/locking.py` | **Cross-platform advisory file lock** (fcntl on POSIX, msvcrt on Windows); used everywhere |
| `exi/guard.py` | Budget-guard state, threshold evaluation (Codex-only) |
| `exi/guardcli.py` | `codex-guard` command line (hook/managed-run/status/install-hooks) |
| `exi/hook.py` | Codex hook entry points for the budget guard |
| `exi/hookmerge.py` | Safe, idempotent merge of Codex guard/feedback hooks into `hooks.json` |
| `exi/managed_run.py` | Process-group supervisor for time/quota-bounded subprocesses |
| `exi/quota.py` | Thin wrapper + cache over a local `llm-quota`-compatible CLI |

### `exi` — external intelligence store

Source of truth is an append-only JSONL event log (`data/observations.jsonl`);
current state is derived by replaying it, and the FTS5 index
(`data/index.sqlite`) is a disposable cache rebuilt whenever the log is newer.

**Observation fields:** `id, status, scope, claim, evidence_paths,
confirmed_count, created_at, last_verified, review_after, supersedes, triggers`.

`confirmed_count` is the number of **distinct** evidence sources. An
observation cannot become `confirmed` (and therefore cannot be promoted)
with fewer than **2 independent** evidence sources — enforced in the store,
not left to the caller.

```bash
# capture (requires evidence — no rootless claims). Starts as 'candidate'.
exi capture --scope "backend/deploy-pipeline" \
  --claim "Deploys must go through the deploy-pipeline wrapper" \
  --evidence "AGENTS.md#L45" --trigger deploy --trigger ci \
  --review-after "2027-01-01T00:00:00+0900"

# add an INDEPENDENT second source -> auto-promotes status to 'confirmed'
exi confirm <id> --evidence "runbook.md"

exi search "deploy-pipeline" -v   # FTS5, literal-term (special chars safe)
exi list --status confirmed
exi promote <id>                  # writes data/promotions/<id>.md ONLY
exi audit                         # review-due / weak / promotable summary
exi verify <id> --review-after …  # mark re-verified
exi retire <id>
```

`promote` **only** writes a Markdown candidate under `data/promotions/`. It
does not touch `AGENTS.md`/`CLAUDE.md`/skills — a human decides whether and
where to apply it.

### `exi feedback` — never make the user say it twice

A feedback-learning store with **graded** Codex enforcement. Each time the user
has to give a piece of feedback (a *rule*), an occurrence is recorded; the
**count** of distinct occurrences drives how forcefully the hooks act. Nothing
is ever auto-promoted into `AGENTS.md`/`CLAUDE.md`/skills, auto-imported, or
seeded.

Occurrences are captured **automatically and zero-click** (see [The zero-click
loop](#the-zero-click-feedback-loop) below): the `UserPromptSubmit` hook
notices a likely corrective prompt, opens a body-less *pending candidate*, and
instructs the current agent to resolve it with a single `exi feedback resolve`
call — the user never types a command. `exi feedback record` still exists for
advanced/manual/back-compat use, but is **optional**.

```bash
exi feedback list -v
exi feedback show no-hidden-fallback
exi feedback candidates             # auto-detected candidates (hash + cues only)
exi feedback violations [NAME]      # hook-detected violations (never change count)
exi feedback enable/disable NAME    # (human-gated — see admin gate)

# [optional/advanced] record an occurrence by hand. First time => count 1.
# A NEW --evidence id => count+1. The zero-click loop uses `resolve` instead.
exi feedback record --name no-hidden-fallback \
  --description "Do not add silent fallbacks; surface failures as failures" \
  --evidence "incident-2026-08-19" \
  --why "hidden fallbacks mask the real bug" \
  --how-to-apply "let it fail loudly; report the cause"
```

`count` is **the number of distinct human occurrences only.** A duplicate
occurrence (same `--evidence` id, or — via `resolve` — the same corrective
**prompt hash**) is rejected so a complaint logged twice can't inflate it, and
a hook violation appends a `violation` event but **never** touches `count`.

#### The zero-click feedback loop

No manual `exi feedback record` is required. On every `UserPromptSubmit`:

1. **Extract** the user's prompt from the documented/common payload shapes.
   The raw prompt is **never** written to durable memory — only a short,
   non-reversible hash and derived ids are.
2. A **conservative, built-in detector** (`feedback_detect.py`) decides whether
   the prompt looks like recurring corrective feedback, in **Japanese or
   English** (e.g. `二度と` / `何度も言っている` / `再三言っている` / `やめろ` /
   `絶対に〜するな` / `あほなこと言ってないよね` / `いかれてる` / `前にも言った` /
   `やめて` / `勝手に` / `〜しないで` /
   `そんな面倒なことできない`, `I already told you` / `don't do that again` /
   `stop doing` / `never do`). It ignores matches inside code, and requires a
   strong cue (or two distinct weak cues) so ordinary questions don't trip it.
   No model or API call is made — it's pure standard-library string matching.
3. For a likely-feedback prompt it opens a **pending candidate** keyed by
   `session + turn + prompt-hash`, persisting **only** the candidate id, prompt
   hash, timestamps, session/turn, and detection **cue categories** — never the
   prompt body.
4. It **injects an instruction** telling the agent to resolve the candidate
   *without asking the user*: match an existing rule or author a new canonical
   one (with why / how-to-apply) and call `exi feedback resolve`, or `exi
   feedback dismiss` if it isn't really feedback. The **evidence id is derived
   internally from the candidate** — the agent never supplies one.

```bash
# the agent runs ONE of these (evidence id is internal; never passed):
exi feedback resolve --candidate <id> --name no-auto-commit \
  --description "Do not commit without being asked" \
  --why "the user reviews diffs first" --how-to-apply "stage only; wait for OK"
exi feedback dismiss --candidate <id> --reason "quoted code, not a complaint"
```

`resolve` is **one-shot / idempotent / session+candidate-bound / expiry-checked**
and can **only** add a new occurrence or a new rule — it can never disable,
delete, or reconfigure a rule (those stay behind the [admin gate](#administrative-gate--human-gating-the-management-cli)).
Because the evidence id is the prompt hash, re-resolving the same corrective
prompt does **not** inflate the count; a *differently-worded* complaint does.
The `Stop` hook blocks (within the existing 3-per-turn cap) while a candidate
is still unresolved, and after the cap leaves it marked **abandoned** for later
human audit rather than fabricating a rule. Approval-marker prompts never become
candidates. Set `feedback.auto_capture=false` to turn the loop off.

#### Severity ladder

Each matched rule resolves to a severity — an explicit `severity` on the
spec wins, otherwise it is derived from `count`:

| count | severity | effect |
|---|---|---|
| 1–2 | **warn**  | non-blocking note added as context / stderr |
| 3–4 | **pause** | `PreToolUse` deny with an out-of-band approval path |
| ≥ 5 | **deny**  | hard block — **not** bypassable |

#### Enforcement specs (declarative only — no shell)

Attach conditions with `configure`. The spec is strictly validated against
an allow-list; **there is no key that runs a shell command or external
checker.** The engine only evaluates built-in, predictable conditions:

```bash
# forbid a dangerous command shape
exi feedback configure no-hidden-fallback \
  --spec-json '{"event":"pre_bash","when":"--sandbox danger-full-access"}'

# require a test sibling when editing a module (test files themselves excluded)
exi feedback configure ship-tests \
  --spec-json '{"event":"pre_edit","path_glob":"**/*.py","exclude_glob":"**/test_*.py","absent_sibling":"{dir}/test_{stem}.py"}'

# at Stop, fail if a changed file still has debug prints
exi feedback configure no-debug \
  --spec-json '{"event":"stop_check","forbid_regex":"print\\("}'
```

* **`event`**: `pre_bash` | `pre_edit` | `stop_check`.
* Common: `severity`, `message`, `scope` (substring of cwd), `unless` (regex
  escape hatch).
* `pre_bash`: `when` / `forbid_regex` on the command string.
* `pre_edit`: `path_glob`, `exclude_glob`, `absent_sibling`
  (`{stem}`/`{dir}`/`{suffix}`/`{name}`/`{parent}`), `require_regex`,
  `forbid_regex` over the proposed new content.
* `stop_check`: `path_glob`, `exclude_glob`, `absent_sibling`,
  `require_regex`, `forbid_regex` over the **on-disk content of files this
  session actually changed** (tracked via `PostToolUse`; Bash writes are
  never guessed). Reads are refused outside the session cwd (path-traversal
  guard). Globs use a predictable matcher where `*` never crosses `/` and
  `**` does.

#### `pause` — the Codex-`ask`-substitute

Codex's `PreToolUse` supports only `deny`/`allow` (no `ask`), and returning
an unsupported decision would let the tool run anyway — so this engine
**never** returns `ask`. A `pause` is implemented as a one-shot, out-of-band
approval:

1. `PreToolUse` matches a pause rule → **deny** with a random `nonce`,
   telling the user to reply with the exact line `ALLOW_FEEDBACK:<nonce>`.
2. Only the **`UserPromptSubmit`** hook recognizes that reply and marks the
   pending approval `approved` — the model cannot forge a user prompt, so it
   cannot self-approve. The **entire** stripped prompt must match
   `ALLOW_FEEDBACK:<16 lowercase hex>` **exactly**; any leading or trailing
   text fails to approve.
3. The **next identical** tool call (same session + tool fingerprint + rule)
   consumes the permit **exactly once** and is allowed; a further retry
   pauses again. Permits have a TTL (default 10 min) and cannot be reused
   across a different session, tool call, or rule.
4. A **hard `deny`** (`count ≥ 5` or explicit `severity:"deny"`) has no
   nonce and cannot be approved this way.

#### Administrative gate — human-gating the management CLI

Independently of any user rule, a **built-in** `PreToolUse` gate recognizes
the supported feedback-management mutations and requires the same
out-of-band human confirmation before they run:

* Gated: `exi feedback configure`, `exi feedback disable`,
  `exi feedback enable` — recognized across the common command forms
  (`bin/exi …` relative/absolute, a bare `exi` on `PATH`, and
  `python -m exi.exicli …`).
* **Not** gated: `exi feedback record` (it only adds human evidence and must
  stay usable), nor read-only `list` / `show` / `violations`.

The gate **denies** with a random `ALLOW_FEEDBACK_ADMIN:<16 lowercase hex>`
marker. Approval mirrors the pause flow but uses a **separate nonce pool**,
so an `ALLOW_FEEDBACK:` reply can never satisfy the admin gate and
vice-versa: only an entire stripped prompt matching
`ALLOW_FEEDBACK_ADMIN:<nonce>` exactly approves, same session, same exact
tool fingerprint, TTL-bound, and consumed **one-shot**. If detection ever
errors it fails **closed** (treats the command as gated) — the opposite of
every other regex path here, which fail open.

Hard denies are always evaluated **first**: an admin permit never bypasses a
feedback rule that independently resolves to a hard `deny`.

This is a belt, not a sandbox — see [Safety Model](#safety-model).

#### Stop loop-guard

The `Stop` hook blocks (`decision:"block"`) up to a small fixed number of
times (default 3) to give the agent a chance to fix the flagged files; after
that it **stops blocking** and emits a manual-confirm note, so it can never
wedge the session in an infinite stop loop. The cap is **hard-clamped to
`0..3`** no matter what `feedback.stop_max_blocks` is set to, and the
attempt counter is keyed by **session + turn only** — not by which rule
fired — so an agent cannot exceed three blocks in a turn by alternating
which configured rule trips each time. `Stop` always prints valid JSON.

`UserPromptSubmit` injects enabled rules with `count ≥ 3` (configurable)
whose `scope` (if any) matches the current cwd, highest count first, capped
at `feedback.inject_max_chars` (default 3000; lower-count rules are
shortened, then omitted with a note, before any hard cut). The injected
size **never exceeds 3000 chars** even if the config asks for more — the
config can only lower the cap.

### Automatic relevant-memory retrieval

A stored observation that is never surfaced is dead weight, so the same
`UserPromptSubmit` hook **also searches the `exi` observation store on every
normal turn** and injects the most relevant *confirmed* knowledge as developer
context — no `exi search` needed.

* **What is injected:** only `confirmed`, **current** observations. Under-
  evidenced candidates, `superseded`/`retired` items, and **review-expired**
  (stale) observations are excluded, so out-of-date memory can't keep steering
  the agent.
* **How relevance is scored — no embeddings, no network, no third-party dep.**
  A deterministic, standard-library scorer combines: FTS5 matches (where the
  query has FTS-tokenizable terms), lexical overlap over English word tokens
  **and** CJK character uni/bigrams (so Japanese, which FTS5 does not segment,
  still retrieves), a boost when a `trigger` keyword literally appears in the
  prompt, and a `scope`↔cwd match. **No LLM, Claude, Codex, network, or metered
  API is ever called from a hook.**
* **Bounded:** at most `memory.inject_max_results` hits (default 5, hard ceiling
  10) within `memory.inject_max_chars` (default 1500, hard ceiling 2000),
  highest relevance first, each rendered as a concise `scope` + `claim` +
  `(evidence xN)` line — **never a conversation body**. If the store is empty or
  corrupt, a short diagnostic goes to stderr and the turn proceeds (fail-open).

Memory retrieval, confirmed-rule injection, approval handling, the feedback
candidate instruction, and the durable-memory capture instruction (below) are
merged into **one** valid `UserPromptSubmit` hook document — no multiple JSON
docs, no shadowing.

### Autonomous durable-memory capture

**There are no magic words and no manual user commands.** You never say
「覚えて」 / "remember this", and you never run any `exi` command yourself. On
**every normal turn** on all four surfaces, the `UserPromptSubmit` hook appends
one concise, provider-neutral instruction telling the *active agent* (Codex /
Claude / Copilot) to judge the whole turn for knowledge worth keeping and, only
if something qualifies, to record it **silently** via an internal
`exi memory resolve` call. There is **no trigger-word detector** — the model
decides. A turn with nothing worth remembering needs **no command at all**; the
body-less candidate that backs the instruction simply expires. This never blocks
the agent from stopping and never loops.

**What is remembered (only these):** stable user preferences/constraints, stable
environment facts, verified reusable procedures, verified root causes, and
decisions likely to matter in future sessions.

**What is never remembered:** ephemeral status, speculation, one-off requests,
generic knowledge already in the model's training, and anything secret-like
(credentials/tokens/keys — see Privacy).

#### Trust and evidence rules

Two honest trust levels, kept deliberately distinct:

* **User-authoritative (`active`).** A direct, stable user *preference* or
  *constraint* is authoritative because the user stated it. It becomes
  retrievable **immediately** from the single turn as an `active` memory. It is
  **not** dressed up as having two independent technical sources — its
  initial `confirmed_count` honestly stays 1; authority, not evidence count,
  makes it active.
* **Evidence-disciplined (`confirmed`).** A verified technical fact / procedure
  / root cause follows the **existing** confirmed-memory safety model unchanged:
  it starts as a non-retrievable `candidate` and only becomes retrievable once
  it has **≥ 2 actual independent evidence sources** (for example, two distinct
  files, logs, or URLs). A user-turn hash is provenance only and **never counts
  as technical evidence**; repeating an assertion in a later turn therefore
  cannot confirm it. The system never fakes independence by treating "the agent
  reviewed it" or a user prompt as verification.

Resolving the same memory twice from one turn is idempotent (no evidence
inflation, no duplicate observation); a single turn may still record several
**distinct** memories (hard-capped at 8). Candidate cache size and canonical
claim/scope/trigger/evidence lengths are also hard-capped before persistence, so
an agent cannot grow the local state without bound or hide a secret past the
scanner's bounded input. `exi memory resolve` can only **add** an observation or a
distinct evidence source — it can never alter, supersede, retire, or delete an
existing observation. Each call is candidate-bound and one-shot-idempotent.

Retrieval (previous section) surfaces both `confirmed` technical observations
**and** `active` user-authoritative memories, clearly labelled and bounded;
candidates, expired/stale, retired, and superseded items stay excluded.

#### The commands the agent runs (never you)

```bash
# record a durable memory (the candidate id comes from the hook instruction;
# preference/constraint provenance is derived from the candidate)
exi memory resolve --candidate <id> \
  --kind preference \
  --scope "workflow/testing" \
  --claim "Run the test suite before committing" \
  --trigger commit
#   kinds: preference | constraint | environment | procedure | root-cause | decision
#   preference/constraint -> active immediately.
#   technical kinds require --evidence and need 2 actual independent sources.

# (optional, for the audit trail; NEVER required for an ordinary turn)
exi memory dismiss --candidate <id> --reason "nothing durable this turn"
# the reason is printed for the current agent but is not persisted
```

#### Audit commands (safe for you to run)

```bash
exi memory candidates          # session-cache candidates (hash + metadata only)
exi list --status active       # user-authoritative memories currently retrievable
exi list --status confirmed    # evidence-confirmed technical memories
exi audit                      # under-evidenced candidates, review-due, promotable
```

### `codex-guard` — Codex budget guard

#### What it checks per turn

| Check | soft (warn) | hard (deny) |
|---|---|---|
| Turn wall-clock time | 45 min | 120 min |
| Weekly Codex usage increase **this turn** | +3% | +5% |
| Weekly Codex usage increase **rolling 24h** | +12% | +20% |
| Tool calls in one turn | — | > 150 |
| Same tool+input fingerprint repeated | — | ≥ 3× |

All thresholds are configurable — see [Configuration](#configuration).
Weekly consumption is measured as the **sum of positive deltas** between
usage samples, so a weekly reset (used% drops) is never mistaken for
consumption, and post-reset burn is still counted.

Quota is read **only** via the configured `llm-quota`-compatible command
(default: `llm-quota --json --providers codex` on `PATH`; main `codex`
pool). If that is unavailable or the pool can't be resolved, quota checks
are skipped and the reason is reported — the time/count/repeat guards keep
running. See [Quota adapter](#quota-adapter) for the expected JSON shape.
The result, including an `unknown` reason, is cached for 30 seconds by
default to avoid starting the quota command on every tool call. Guard state
is separated by session/turn and file-locked, so concurrent Codex sessions
do not overwrite one another's counters.

#### Acknowledging a budget stop (`guard ok`)

A hard **rolling-24h** breach stops the turn at `UserPromptSubmit` so the
human can decide. Two things make that stop easy to miss: the stop also
swallows the prompt that carried it, and the Codex desktop app renders
neither `stopReason` nor `systemMessage`. Until old samples age out of the
24h window every further prompt would be dropped the same way. So:

- **Every stop is made visible.** It is appended as one JSON line to
  `<data dir>/blocks.log` (`event`, `kind`, `codes`, `reason`, session/turn
  ids — never the prompt), and on macOS a desktop notification is posted via
  `osascript` (`guard.block_notify_macos`, default `true`). Both are
  best-effort and can never change the hook's decision.
- **The user can resume with a bounded allowance.** A prompt that *starts
  with* one of `guard.ack_phrases` (default `"guard ok"`, `"予算OK"`; matched
  case-insensitively, full-width folded, as a plain prefix so
  `予算OK続けて` works) is let through, the stop reason tells the user so,
  and the agent receives an `additionalContext` note saying the prefix is a
  control word and that it should stay frugal. The ack suppresses **only**
  the `weekly_24h` finding, at all three events (prompt, tool call,
  compaction), and only while its allowance lasts: `guard.ack_grant_pct`
  (default `5.0`, measured as positive deltas since the ack, so a weekly
  reset is not "spending") or `guard.ack_grant_hours` (default `3.0`),
  whichever ends first. Then the guard stops again, reports why the previous
  ack ended, and asks for a fresh one. Time / tool-count / repeat / per-turn
  guards are never suppressed by an ack.

`codex-guard status` shows the current ack and any finding it suppresses.

#### `managed-run` — supervise a process this tool owns

```bash
codex-guard managed-run -- your-long-running-command --with args
codex-guard managed-run --dry-run -- <cmd>   # monitor only, log the kill it would send
codex-guard managed-run --status             # list owned runs
```

Launches the command in its own process group, then on a **hard**
time/24h-quota breach escalates `SIGTERM → grace → SIGKILL` — against
**its own process group only**. It refuses to signal any pgid it did not
create, the tool's own group, or pgid ≤ 1. It never touches unrelated PIDs.

Long-running shell commands must be launched through `managed-run` to get
mid-command enforcement. A normal `PreToolUse` hook only decides *before*
the tool starts and cannot stop that same call halfway through.

#### `status`

```bash
codex-guard status            # human-readable
codex-guard status --json     # quota + turn context + findings + thresholds
```

---

## Safety model

**Read this before relying on the guard for anything.** It reduces — it
does **not** eliminate — the "an agent ran for hours and ate the weekly
quota" failure mode. It can only act at the moments Codex actually invokes
a hook:

| Situation | Covered? |
|---|---|
| A tool call is about to run (`PreToolUse`) | ✅ can **deny** it |
| A new user turn starts (`UserPromptSubmit`) | ✅ resets per-turn counters, samples quota |
| Context compaction boundary (`PreCompact`) | ✅ best-effort hard stop |
| One tool call that itself runs for hours | ❌ `PreToolUse` cannot interrupt it after launch; start it through `managed-run` |
| Hosted tools such as web search | ❌ not covered by `PreToolUse` hooks |
| The model **reasons/plans for a long time without calling any tool** | ❌ no hook fires — cannot interrupt |
| The Codex **application itself** (not the CLI agent loop) | ❌ out of scope |
| Hooks not **trusted/approved** in Codex | ❌ hooks never run → **no protection at all** |
| Quota is `unknown` (adapter down / main pool unresolved) | ⚠️ quota checks skip; time/tool-count/repeat guards stay active |
| Usage telemetry updates late | ⚠️ quota guard reacts to the latest observable value, so detection can lag |

So: the guard is a **backstop at tool boundaries**, plus an independent
`managed-run` supervisor for processes it owns. It is honest about the gaps
rather than pretending to be a hard ceiling. There is **no** fallback to a
metered API anywhere — quota exhaustion is surfaced, never worked around.

This is a **belt, not a sandbox**: arbitrary same-user tampering with the
source or data (editing these files, deleting `feedback.jsonl`, calling the
Python API directly) **cannot be made cryptographically impossible** — a
local process running as you can always do what you can do. What the gate
buys is that the *supported management CLI* path — the one an agent would
naturally reach for — is human-gated rather than silently self-serviced.

Other safety properties:

* Every feedback hook **fails open**: an internal error surfaces on stderr
  and gets out of the way rather than blocking (and `Stop` still emits
  `{}`). The budget guard's separate fail-*closed* behavior is unchanged.
  The lone fail-*closed* exception is the admin-mutation detector.
* **Broken feedback data is never silently reset** — enforcement fails open
  and the corruption is surfaced.
* **Regex matching is bounded** so a pathological rule pattern cannot hang
  the hook: every runtime regex enforces a max pattern/input length and a
  short wall-clock deadline (`SIGALRM`). A `(a+)+$`-style catastrophe raises
  and the hook fails open (no enforcement that call) instead of spinning.
  No external dependency is used.
* No secrets or conversation bodies are stored or injected; only rules,
  memories (agent-authored, secret-scanned claims), evidence ids, counters,
  hashed fingerprints, and short-lived nonces. State is limited to counters,
  **hashed** tool-call fingerprints, weekly-usage percentages and the
  current ack (`data/guard-state.json`); the block log (`data/blocks.log`)
  records finding codes and reasons, never prompt text.
* **Durable-memory capture is candidate-bound and additive-only.** Autonomous
  `exi memory resolve` can only append an observation or a distinct evidence
  source; it can never disable/alter/supersede/retire/delete an existing
  observation. User-authoritative preferences become retrievable as an explicit
  `active` status (honest single source); verified technical facts keep the
  existing ≥2-independent-evidence `confirmed` discipline. No raw prompt is
  retained and no LLM/network/metered API is called from any hook.
* **Raw user prompts are never persisted.** The zero-click capture loop stores
  only a non-reversible prompt **hash**, timestamps, session/turn, and detection
  **cue categories** (a fixed vocabulary — never text lifted from the prompt) in
  the disposable session cache. The canonical rule text that lands in durable
  memory is authored by the active agent via `exi feedback resolve`, not copied
  from the prompt. Auto-resolution can only *add* an occurrence or a new rule —
  it can never disable, delete, or reconfigure a rule (those remain behind the
  admin gate), and it never edits `AGENTS.md`/`CLAUDE.md`/skills.
* Enforcement can only act where Codex fires a hook (see the table above);
  it is a graded nudge, not a hard ceiling.

---

## Install / config / hooks workflow

1. **Install** the package (see [Install](#install)).
2. **Preview the hook merge** — writes nothing, just confirms your existing
   `Stop` hook (if any) survives:
   ```bash
   codex-guard install-hooks --dry-run
   ```
3. **Install the hooks** — merges into `~/.codex/hooks.json` by default
   (override with `--path`), backing up the original to `hooks.json.bak`
   on the very first run:
   ```bash
   codex-guard install-hooks
   ```
   The merge is safe and idempotent: it **adds** the guard's
   `UserPromptSubmit`, `PreToolUse`, and `PreCompact` entries **and** the
   feedback engine's four hooks (`UserPromptSubmit`, `PreToolUse`,
   `PostToolUse`, `Stop`) — the guard and feedback commands share event
   names without shadowing each other — while preserving every existing
   hook object (including one installed by another tool) unchanged. The
   JSON file is re-serialized, so whitespace is not byte-for-byte identical.
   The original file is backed up once; writes are atomic; re-running does
   not duplicate entries.
4. **Trust the hooks in Codex.** You must approve these hooks inside Codex
   for them to run — until then the guard provides no protection. This is a
   Codex safety property, not a bug in this project.
5. **(Optional) point at your own quota adapter** — see below.
6. **(Optional) record feedback rules** — see `exi feedback record` above.

> The Codex `install-hooks` command installs **both** the budget guard and the
> feedback/memory hooks. The three other surfaces below install **only** the
> feedback/memory hooks (never the budget/quota guard).

---

## Multi-agent surfaces & hook install

The feedback/memory engine runs on four surfaces from one provider-neutral core
(`exi/feedback_core.py`). Each surface has a thin adapter that (1) normalizes
the raw payload into a request, **namespacing the session id by provider** so
several agents sharing one machine's data dir never collide, and (2) encodes the
neutral decision into that surface's exact output contract. All hooks fail
**open** on any internal error; the only "closed" outcomes are the intentional
warn/pause/deny enforcement decisions.

Install with the generator (idempotent, backs up once, `--dry-run` to preview,
`--scope user|project`, `--path` to target an explicit file):

```bash
# Claude Code — settings.json hooks (UserPromptSubmit/PreToolUse/PostToolUse/Stop)
exi-hook install claude --scope user            # ~/.claude/settings.json
exi-hook install claude --scope project         # ./.claude/settings.json

# GitHub Copilot in VS Code (Copilot Agent, currently Preview) — PascalCase hooks
exi-hook install copilot-vscode --scope user    # ~/.copilot/hooks/exi-feedback-vscode.json
exi-hook install copilot-vscode --scope project # ./.github/hooks/exi-feedback-vscode.json

# GitHub Copilot CLI — camelCase user or project hooks
exi-hook install copilot-cli --scope user       # ~/.copilot/hooks/exi-feedback-cli.json  (%USERPROFILE% on Windows)
exi-hook install copilot-cli --scope project    # ./.github/hooks/exi-feedback-cli.json
```

On **Windows (PowerShell)** the commands are identical (the generated hook
command is the `exi-hook` console script — a single token that resolves on
PATH in cmd, PowerShell, and POSIX shells, with no `bin/` launcher and no POSIX
quoting):

```powershell
exi-hook install claude --scope user
exi-hook install copilot-cli --scope user
exi-hook install copilot-vscode --scope project --project .
```

Each generated hook invokes `exi-hook <provider> <event>` (payload on stdin,
output on stdout). What each surface emits:

| Surface | Injection event | Injection output | Tool block | Stop block |
|---|---|---|---|---|
| Codex CLI | `UserPromptSubmit` | `hookSpecificOutput.additionalContext` | `hookSpecificOutput.permissionDecision=deny` | top-level `{"decision":"block"}` |
| Claude Code | `UserPromptSubmit` | `hookSpecificOutput.additionalContext` | `hookSpecificOutput.permissionDecision=deny` | top-level `{"decision":"block"}` (`stop_hook_active` honored) |
| Copilot VS Code | `UserPromptSubmit` | `hookSpecificOutput.additionalContext` | `hookSpecificOutput.permissionDecision=deny` | `hookSpecificOutput {hookEventName:"Stop",decision:"block"}` |
| Copilot CLI | `userPromptTransformed` | `modifiedTransformedPrompt` (original transformed prompt **verbatim** + bounded appended context; `{}` if nothing to add) | top-level `permissionDecision=deny` | `agentStop` top-level `{"decision":"block"}` (`stop_hook_active` honored) |

Copilot CLI injection rides `userPromptTransformed`, **not** `userPromptSubmitted`
— the CLI drops a config-file `userPromptSubmitted` hook's output. Event names
are matched case-insensitively for the CLI (camelCase or PascalCase compat).

### Portability & surface support

| Surface | macOS/Linux | Windows | Notes |
|---|---|---|---|
| Codex CLI | ✅ | ⚠️ partial | Feedback/memory and hook-based budget checks are portable; the optional process-group supervisor (`managed-run`) is POSIX-only. |
| Claude Code | ✅ | ✅ | Feedback/memory only. PascalCase hooks in `settings.json`. |
| Copilot VS Code (Agent) | ✅ | ✅ | Feedback/memory only. PascalCase `.github/hooks` JSON + `.claude/settings*.json` + user hooks. |
| Copilot CLI | ✅ | ✅ | Feedback/memory only. User hooks under `~/.copilot/hooks` (`%USERPROFILE%\.copilot\hooks` on Windows). |
| Copilot **cloud** coding agent | ⚠️ ephemeral | ⚠️ ephemeral | **Unsupported for durable memory.** Its filesystem is ephemeral and only repo `.github/hooks` are loaded, so cross-job memory does not persist. This project does **not** add external persistence or cloud sync — a single job still gets in-job enforcement, but nothing survives the job. |

**Cross-platform locking & paths.** All state mutations serialize on a
standard-library advisory lock (`exi/locking.py`): `fcntl.flock` on POSIX,
`msvcrt.locking` over one byte on Windows. No runtime module imports `fcntl` at
module top (which would fail on Windows import). Data/config default to XDG
paths on POSIX and `%LOCALAPPDATA%` on Windows.

Do not install both Copilot adapters into the same hook discovery scope unless
you have verified that your installed versions ignore the other surface's event
names. VS Code and Copilot CLI discover some of the same hook directories, but
their prompt and Stop output contracts differ. On the corporate Windows use
case, install `copilot-vscode` for VS Code Agent **or** `copilot-cli` for
Copilot CLI, matching the surface actually in use.

### Privacy boundaries

- **No private data enters the repo.** Rule/observation data lives only in the
  per-user data dir (XDG / `%LOCALAPPDATA%` / `EXI_DATA_DIR`), never in the
  working tree.
- **Raw prompts and transcripts are never persisted.** A detected feedback
  candidate and a per-turn memory candidate each store only a non-reversible
  prompt **hash** plus fixed metadata (session/turn/status/timestamps; feedback
  adds coarse cue *category labels* from our fixed vocabulary — never substrings
  of the prompt). No prompt body, assistant output, or tool output is ever
  written. The Copilot CLI transformed prompt is echoed back verbatim in
  `modifiedTransformedPrompt` but never written to disk. (Tested: a suite scans
  the whole data dir across all four surfaces to prove a secret in the prompt —
  or in the CLI transformed prompt — never lands in any file.)
- **No secrets in durable memory.** The canonical claim, scope, triggers, and
  evidence ids that land in the observation store are authored by the agent, not
  copied from the prompt, and are run through a conservative secret/credential
  rejecter (`secretscan`): a claim that looks like it carries a key/token/
  password is **refused** (not silently redacted) so the model re-states it
  without the secret. A candidate-bound `exi memory resolve` call can only add
  an observation/evidence source, never alter or delete one. Candidate binding
  is not, by itself, a proof that the model was uninfluenced by untrusted
  repository/tool content; see Limitations below.
- **Personal and corporate machines stay separate by default.** There is **no
  automatic cloud sync** and no cross-machine transport. Session ids are
  namespaced per provider so co-located agents share the local store safely, but
  nothing leaves the machine.
- **No LLM / network / metered API from any hook.** Detection and retrieval are
  deterministic and local.

---

## Configuration

Defaults are packaged inside the `exi` module as `exi/config.default.json`
(installed as package data, so they're present after any install — editable,
wheel, or running from a checkout). Override any subset by creating a
per-user `config.json`; it is deep-merged over the defaults. Keys:

- `guard.*` — thresholds from the table in [`codex-guard`](#codex-guard--codex-budget-guard).
- `guard.ack_phrases` (default `["guard ok", "予算OK"]`) /
  `guard.ack_grant_pct` (default `5.0`) / `guard.ack_grant_hours` (default
  `3.0`) — the user acknowledgement that resumes after a `weekly_24h` stop and
  its allowance; `0` disables a bound, an empty phrase list disables acks.
- `guard.block_log` (default `true`) — append every stop/deny to
  `<data dir>/blocks.log`. `guard.block_notify_macos` (default `true`) —
  post a macOS desktop notification on a stop (no-op elsewhere).
- `quota.cmd` / `quota.timeout_seconds` / `quota.cache_seconds` — the quota
  adapter command (list of argv), its subprocess timeout, and cache TTL.
- `managed_run.grace_seconds` / `managed_run.poll_seconds`.
- `feedback.inject_min_count` / `feedback.inject_max_chars` /
  `feedback.approval_ttl_seconds` / `feedback.stop_max_blocks`.
- `feedback.auto_capture` (default `true`) — the zero-click capture loop;
  set `false` to disable auto-detection of corrective prompts entirely.
- `feedback.candidate_ttl_seconds` (default 3600) — how long a pending
  feedback candidate lives before it expires (clamped to 60 … 7 days).
- `memory.inject_max_results` (default 5, hard ceiling 10) /
  `memory.inject_max_chars` (default 1500, hard ceiling 2000) /
  `memory.min_relevance` (default 2) — automatic memory-**retrieval** budget and
  relevance floor. Config can only *lower* the two hard ceilings.
- `memory.auto_capture` (default `true`) — autonomous durable-memory **capture**:
  the per-turn "assess this turn for durable knowledge" instruction and the
  body-less memory candidate that backs `exi memory resolve`. This is a
  **distinct** switch from `feedback.auto_capture` (the corrective-feedback
  loop) and from retrieval — retrieval alone is *not* "automatic memory". Set
  `false` to turn autonomous capture off entirely; defaults provide the
  autonomous behavior.
- `memory.candidate_ttl_seconds` (default 3600) — how long a pending memory
  candidate lives before it expires (clamped to 60 … 7 days).

**Default paths** (no environment variables needed):

| | POSIX (macOS/Linux) | Windows |
|---|---|---|
| User config (`config.json`) | `$XDG_CONFIG_HOME/codex-feedback-guard/config.json` → `~/.config/codex-feedback-guard/config.json` | `%LOCALAPPDATA%\codex-feedback-guard\config.json` (→ `~/AppData/Local/…` if unset) |
| Runtime data | `$XDG_DATA_HOME/codex-feedback-guard` → `~/.local/share/codex-feedback-guard` | `%LOCALAPPDATA%\codex-feedback-guard` |

`EXI_CONFIG` and `EXI_DATA_DIR` environment variables are **authoritative**
overrides — when set, they relocate the config file / data directory to any
path you choose, bypassing the XDG defaults above (also used by the test
suite to isolate state).

## Quota adapter

`codex-guard` never talks to any billing API directly. It shells out to
whatever command you configure under `quota.cmd` (default: `llm-quota
--json --providers codex`, resolved via `PATH`) and parses its stdout as
JSON. You need to provide this command yourself — it's expected to know how
to read *your* Codex account's usage (however you track that).

Expected JSON shape:

```json
{
  "providers": {
    "codex": {
      "ok": true,
      "mode": "normal",
      "windows": {
        "weekly": {
          "used_percent": 42.0,
          "resets_at": "2026-08-24T00:00:00+00:00"
        }
      }
    }
  }
}
```

- `providers.codex.ok` — `false` (or the key missing) means "can't resolve
  the main pool right now"; the guard treats quota as `unknown` and keeps
  time/count/repeat checks running.
- `providers.codex.mode` — a free-form string (`normal` / `conserve` /
  `critical` are the ones this project treats as meaningful) surfaced in
  `status` output.
- `providers.codex.windows.weekly.used_percent` — required for any
  weekly-burn check to run; missing → `unknown`.
- `resets_at` — surfaced in `status` output; not required for the guard
  logic itself.

If you don't have such a command, quota-based checks simply report
`unknown` and skip — time/tool-count/repeat-loop guards are unaffected.

## Feedback hooks

The same feedback/memory model runs on all four surfaces (see [Multi-agent
surfaces & hook install](#multi-agent-surfaces--hook-install)). See
[`exi feedback`](#exi-feedback--never-make-the-user-say-it-twice) above
for the full model (zero-click capture loop, severity ladder, enforcement
specs, pause approval flow, admin gate, Stop loop-guard), [Automatic
relevant-memory retrieval](#automatic-relevant-memory-retrieval), and
[Autonomous durable-memory capture](#autonomous-durable-memory-capture). In
short: the durable rule/memory text is only ever the canonical wording the agent
writes when resolving a candidate (or that you record by hand) — never the raw
prompt — enforcement specs are a closed, declarative allow-list (never arbitrary
code), and no hook ever calls an LLM, network, or metered API.

---

## Limitations

- **Not a sandbox.** See [Safety Model](#safety-model) — a same-user local
  process can always bypass this by editing files or the data store
  directly.
- **Coverage gaps at the hook level.** Long single tool calls, hosted
  tools, and model "thinking time" without a tool call are not observable
  or interruptible — see the coverage table above.
- **Quota adapter is BYO.** This project ships no quota-fetching logic of
  its own; you must have (or build) a small CLI that emits the JSON shape
  above for your account.
- **Some `managed-run` tests use short real sleeps** and spawn real
  subprocesses; they're not network-dependent but aren't instantaneous
  either.
- **The feedback detector is a conservative heuristic, not a classifier.** It
  is intentionally high-precision (a strong cue, or two distinct weak cues) so
  it stays quiet on ordinary prompts; it will therefore *miss* some genuinely
  corrective phrasings it doesn't recognize. When it does miss, nothing bad
  happens — no candidate, no injection — and you can still `exi feedback record`
  by hand. It runs with **no** model/API call, so it cannot reason about intent
  the way an LLM would.
- **Autonomous memory capture depends on the model's judgment.** There is no
  trigger-word detector by design (requiring a magic phrase would just be manual
  saving). The hook only *invites* the agent to assess each turn; whether a
  durable fact is actually recorded — and whether the canonical claim, scope,
  and kind are well-chosen — is up to the active model. A weak model may miss a
  worthwhile memory or mis-classify a technical fact as a preference. Nothing is
  auto-promoted into `AGENTS.md`/`CLAUDE.md`/skills; `exi audit` and
  `exi list --status active` and `exi list --status confirmed` let a human
  review what was captured.
- **The secret rejecter is a conservative heuristic, not a scanner.** It refuses
  clear credential shapes (known token prefixes, private-key headers, keyword=
  long-value assignments) but is not a guarantee against every possible secret;
  it is a backstop on top of "the claim is agent-authored, not copied from the
  prompt, and no raw prompt is ever persisted."
- **Candidate binding cannot eliminate model prompt injection.** The injected
  policy explicitly tells the active model to ignore instructions found in
  repository files and tool output when deciding what to remember, and
  technical memories still need two actual cited sources. However, without
  storing the raw conversation or calling a separate trusted classifier, the
  CLI cannot cryptographically prove why the model issued a valid
  candidate-bound command. Treat the local observation store as auditable state
  (`exi list` / `exi audit`), not as an infallible security boundary.
- **Automatic memory retrieval is lexical, not semantic.** Ranking is
  deterministic word/CJK-ngram/trigger/scope overlap plus FTS — it has no
  synonym or paraphrase understanding, again by design (no embeddings, no
  network, no metered API from a hook). A relevant observation phrased with no
  shared terms or triggers may not surface.
- **Copilot cloud coding agent is not supported for durable memory.** Its
  filesystem is ephemeral and only repo `.github/hooks` are loaded; cross-job
  memory does not persist, and this project deliberately adds no external
  persistence or cloud sync. See the [portability matrix](#portability--surface-support).
- **Live-product hook integration is not exercised in CI.** The adapters are
  built to the published hook contracts (GitHub/Anthropic docs, checked
  2026-08-20) and unit-tested against those exact payload/output shapes, but the
  automated suite does not drive a real VS Code Copilot Agent, Copilot CLI, or
  Claude Code process. Verify the wiring in your own environment after
  installing; exact tool-input shapes (e.g. VS Code `editFiles` arguments) can
  vary by product version.
- **Not a cryptographic boundary across surfaces either.** The administrative
  gate and pause approvals are same-user, out-of-band confirmations; a
  same-user process can still edit the data store directly.

## Testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Covers: normal allow, time soft/hard, per-turn quota, rolling-24h,
repeat-loop, quota-unknown (blocks on time but not on quota), weekly-reset
handling, hook merge preserving an existing `Stop` hook plus the four
feedback hooks, `managed-run` signaling only the owned process group,
state/store locking, independent evidence-source handling, and quota-cache
behavior. The feedback engine adds: count-from-humans-only and
duplicate-evidence rejection, strict spec validation, the warn/pause/deny
ladder, the nonce approval lifecycle (exact match, session/fingerprint/rule
binding, TTL, one-shot), hard-deny being non-bypassable, injection
budgeting, change tracking, the Stop block cap, fail-open on internal
error, and concurrent-write safety. The zero-click loop and memory retrieval
add: robust prompt extraction, the JA/EN detector (positive/negative cases,
code-span ignoring, cue-privacy), pending-candidate lifecycle (idempotent
upsert, session scoping, expiry, abandon), `resolve`/`dismiss` (one-shot,
duplicate-prompt-hash dedup, distinct-prompt increment, unknown/expired
handling), the combined single-document `UserPromptSubmit` output, Stop
blocking on an unresolved candidate sharing the 3-per-turn cap, a proof that no
raw prompt reaches any durable or state file, and memory relevance ranking
(English + CJK), scope handling, stale/candidate/retired filtering, result/char
bounds, and empty/corrupt fail-open.

Autonomous durable-memory capture adds: the store trust model (user-authoritative
`active` on a single source vs evidence-disciplined `confirmed` needing two
actual independent sources, turn-provenance excluded from technical evidence,
later-confirmation, active-never-downgraded,
normalized-claim dedup, no-inflation on duplicate source, kind validation), the
body-less memory-candidate lifecycle (idempotent upsert, no prompt body,
expiry, provider-namespaced isolation, per-claim resolution tracking), secret
rejection (known token shapes + keyword-assignments rejected, ordinary/CJK prose
passed, reasons never echo the secret), the `exi memory resolve/dismiss/
candidates` CLI (preference→active, technical evidence required, idempotent
re-resolve, multiple distinct memories per turn, unknown/expired/session-
mismatch/dismissed handling, transient-only dismiss reasons, secret-claim
rejection, resolve-cannot-alter-existing, `list --status active`), and the per-turn
instruction on **all four surfaces** — Codex, Claude, Copilot VS Code, and the
transformedPrompt-only Copilot CLI (verbatim-then-append) — plus candidate-id
agreement between hook and CLI, cross-provider candidate isolation, the combined
feedback+memory single-document shape, a data-dir scan proving no secret/raw
prompt is persisted on any surface, no Stop-block on an ordinary turn,
ASCII-safe output, `memory.auto_capture=false` disabling, and the total-context
bound.

The multi-agent work adds meaningful cross-platform coverage: the cross-platform
lock helper (POSIX backend + a **simulated Windows backend** driven by a fake
`msvcrt` on the POSIX runner — single-byte lock/retry/release), a check that no
runtime module imports `fcntl` at module top, platform path resolution
(XDG vs `%LOCALAPPDATA%`, env overrides authoritative), each surface's exact
output shape (Claude/VS Code/Copilot-CLI injection, tool block, Stop block, and
`stop_hook_active` self-limiting), tool-name/payload normalization (`editFiles`,
camelCase `toolArgs`), the `modifiedTransformedPrompt` verbatim-then-append
contract, session-namespace isolation across providers, a proof that no raw
prompt or transformed prompt is persisted, the **no-permanent-unknown-turn-cap**
fix (a later turn without a turn id gets its own Stop counter), idempotent hook
generators with one-time backup and dry-run, generated Windows-safe commands,
and concurrent state/store writes.

Run the suite with `ResourceWarning` promoted to an error to catch leaked file
handles (the whole suite must pass under this flag):

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
```

CI runs the portable suite on **Linux (3.11–3.13)** and **Windows (3.11,
3.13)** with no bash-only steps, and verifies the `exi` / `codex-guard` /
`exi-hook` console scripts resolve from an installed package on both. POSIX
process-group execution tests are explicitly skipped on Windows and continue
to run on Linux.

## Layout

```
agent-steward/
├── bin/{exi,codex-guard}      # dev-mode launchers for exi/codex-guard (console scripts preferred)
├── exi/                       # package
│   ├── feedback_core.py       #   provider-neutral policy engine
│   ├── feedback_hook.py       #   Codex adapter        (feedback-hook)
│   ├── feedback_adapters.py   #   Claude / Copilot-VSCode / Copilot-CLI adapters
│   ├── hookcli.py             #   exi-hook entry point (runtime + install)
│   ├── hookgen.py             #   installers/generators (Claude / Copilot)
│   ├── hookmerge.py           #   Codex hooks.json merge
│   ├── locking.py             #   cross-platform advisory lock (fcntl / msvcrt)
│   ├── secretscan.py          #   secret/credential rejecter for durable memory
│   ├── store.py feedback.py feedback_detect.py guard.py guardcli.py hook.py managed_run.py quota.py config.py
│   └── config.default.json    #   packaged defaults (package data)
├── tests/                     # unittest suite (runs on Linux 3.11-3.13 + Windows 3.11/3.13)
└── pyproject.toml             # console scripts: exi, codex-guard, exi-hook
```

Per-user config/data default to XDG paths outside the repo — see
[Configuration](#configuration) — and are not part of the package layout.

## License

MIT — see [LICENSE](LICENSE).
