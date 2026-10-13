# mini agent

English | [简体中文](README.zh-CN.md)

A handwritten coding agent with a seven-chapter implementation guide. Inspired by the source code of [pi](https://github.com/badlogic/pi-mono) and [Codex](https://github.com/openai/codex), it makes the core loop visible: ask the model, execute tools, append results to history, and repeat until there are no more tool calls.

Read the [English tutorial online](https://caulif.github.io/mini-agent/guide.html) or switch to [简体中文](https://caulif.github.io/mini-agent/guide.zh-CN.html). Both editions cover the same seven chapters and code excerpts. Source comments, docstrings, and CLI messages are in English.

For offline reading, clone or download the repository and open `guide.html` in your browser. GitHub's file view displays HTML source instead of the rendered tutorial.

The runtime uses only the Python standard library. Its tools can read, write, and edit files under the working directory, and execute shell commands. The command blacklist is a teaching example, not a sandbox.

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

## License

Original code and tutorial content are licensed under [MIT](LICENSE). Third-party excerpts retain their respective licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
