# mini agent

English | [简体中文](README.zh-CN.md)

A handwritten coding agent with two bilingual implementation guides: mini agent-1 (chapters 1–7) and mini agent-2 (chapters 8–13). Inspired by the source code of [pi](https://github.com/badlogic/pi-mono) and [Codex](https://github.com/openai/codex), it makes the core loop visible: ask the model, execute tools, append results to history, and repeat until there are no more tool calls.

Read the [English tutorial online](https://caulif.github.io/mini-agent/guide.html) or switch to [简体中文](https://caulif.github.io/mini-agent/guide.zh-CN.html). Both languages cover the same implementation. Continue with [mini agent-2](https://caulif.github.io/mini-agent/advanced-guide.html) for retries, recovery, streaming, permissions, and offline evaluation. CLI messages are in English; Python source has no comments or docstrings.

For offline reading, clone or download the repository and open `guide.html` in your browser. GitHub's file view displays HTML source instead of the rendered tutorial.

The runtime uses only the Python standard library. The original CLI retains its file and shell tools. The mini agent-2 entry point has separate journals, serial file tools, and explicit write permission; it has no shell tool. Neither entry point is an OS sandbox.

## Quick start

Use Python 3.10 or newer. Clone the repository and enter its directory:

```sh
git clone https://github.com/caulif/mini-agent.git
cd mini-agent
```

Windows PowerShell:

```powershell
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_API_KEY = "sk-..."
$env:MODEL_ID = "gpt-4o-mini"
python -m mini_agent
```

macOS / Linux (Bash):

```sh
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
export MODEL_ID="gpt-4o-mini"
python3 -m mini_agent
```

`OPENAI_BASE_URL` can point to a compatible Chat Completions endpoint. The default model is `gpt-4o-mini`.

To work in another directory, add this repository's absolute path to `PYTHONPATH`, then start the agent from your target directory. File tools and sessions use the current working directory.

## Language

The CLI is English-only. The tutorial opens in English and offers a Chinese language link. Model replies follow the user's language; file contents and command output retain their original text.

## Usage

```powershell
python -m mini_agent --help
python -m mini_agent -c                  # Continue the latest session
python -m mini_agent --resume <id>       # Resume by ID or prefix
python -m mini_agent -v --max-turns 8    # Show tools; limit model calls
python -m mini_agent -p "List Python files in this directory"
```

Type a task to begin. Input entered while the agent runs is queued for the next model call. `Ctrl+C` interrupts the current turn; at the idle prompt, `Ctrl+C` or `/exit` exits.

```text
/help    /new     /list    /resume <id>
/skills  /compact /stats   /exit
```

Sessions are saved under `.mini_agent/sessions/` in the working directory. Files matching `skills/*.md` in that directory are loaded into the system prompt at startup; restart after changing them.

## mini agent-2

The original CLI and Session format remain available. Use the separate entry point for chapters 8–13:

```sh
python -m mini_agent.advanced "Read README.md" --root .
python -m mini_agent.advanced "Create hello.txt containing hello" --allow-write --stream
python -m mini_agent.advanced --resume <journal-path> --status
python -m mini_agent.advanced --resume <journal-path>
```

New journals live under the task root’s `.mini_agent/advanced/`. They cannot load the original `.mini_agent/sessions/` files. A resumed run uses the saved root; write access must be granted again with `--allow-write`. `--status` requires no API key and makes no model request. An unknown tool outcome blocks recovery until you inspect the workspace and pass `--acknowledge-unknown`; old tools are never replayed automatically.

Only HTTP 429/502/503/504 are retried, at most twice after the first request. `MINI_AGENT_REQUEST_MAX_RETRIES=0` disables retries. Timeouts and disconnects remain unknown; streaming requests are not retried. Tools run only after a complete, validated response. Exit codes: 0 completed, 2 turn limit, 1 failure, 130 interrupted. Completion does not establish task correctness.

## Local checks

```sh
python -B -m mini_agent.selfcheck
python -B -m mini_agent.retry_selfcheck
python -B -m mini_agent.advanced_selfcheck
python -B -m mini_agent.evaluation --repeats 3
python -B scripts/check_guides.py
```

Checks use temporary workspaces, subprocesses, and local HTTP/SSE servers; they do not call a model. The scripted evaluation checks control flow and file outcomes, not model accuracy. Runtime code uses only the standard library. Rebuilding both guides with `python -B scripts/build_guides.py` additionally requires Pygments; sources are in `docs/guide_source/`.

The previous version is preserved on the `v1` branch. `main` contains both entry points and both guides.

## License

Original code and tutorial content are licensed under [MIT](LICENSE). Third-party excerpts retain their respective licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
