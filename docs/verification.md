# v2 verification — 2026-10-21

Validated locally on Windows with no model-provider requests:

| Check | Result |
| --- | --- |
| `python -B -m mini_agent.selfcheck` | 35 passed |
| `python -B -m mini_agent.retry_selfcheck` | 18 passed |
| `python -B -m mini_agent.advanced_selfcheck` | 21 passed, no skips |
| `python -B -m mini_agent.evaluation --repeats 3` | 9/9 scripted attempts; `real_model=false` |
| `python -B scripts/check_guides.py` | Four generated pages current; 340 links inspected, 210 source excerpts match |
| Python token/AST inspection | 28 files parse; zero comment tokens or docstrings |
| `git diff --check` | Passed |

The added regression covers five invalid completion states: missing, null, unknown, stop with tool calls, and tool_calls without calls. Each is run through Runtime with write permission and is rejected before tool dispatch or assistant/start/result persistence. No file is created.

The recovery suite includes real child-process crashes, a second-process lock check, Windows junctions, and local HTTP/SSE. It checks that unknown outcomes block recovery, recorded results are reused, and persistence failures do not silently continue.

A headless Microsoft Edge check opened all four guides from file URLs at widths 1280, 390, and 320. No page overflow or script errors occurred. All 210 displayed source ranges matched current files. Chapter 7 → Chapter 8 → Chapter 7 and language changes retained the appropriate chapter. Source expansion also passed. The browser harness and screenshots are kept with the local backup, outside the published source tree.

Independent review confirmed the request-only retry boundary, default read-only workspace tools, reserved journal/Git paths, serial journaled execution, complete-response gate, and unchanged original CLI logic. Python AST comparison found no executable changes in eight existing modules beyond removing docstrings.

These checks establish local control flow and documented source consistency. They do not measure real model quality or external-provider behavior.


## 2026-10-22 — Match mini agent-1 structure and style

mini agent-2 now uses the same five chapter sections as mini agent-1: problem, principle, design trade-offs, files introduced or changed, and implementation. Removed the separate running, verification, and complete-source sections in both languages. Implementation limits remain in design trade-offs. The extra reading instructions, warning panels, chapter navigation, and duplicated full files are gone.

The two guides share the language note, series chrome, code-summary layout, source filename/line display, and footer conventions. mini agent-1 and all runtime files remain unchanged.

`python -B scripts/check_guides.py` checks chapter headings against mini agent-1, duplicate IDs, 284 links, and 184 displayed source ranges across both bilingual guides. Rebuilding is deterministic. Headless Microsoft Edge checked widths 1280, 390, and 320, chapter/language routes, all 27 remaining code excerpts in each language, and the removal of the extra sections. At each width, 21 reading and code elements matched 10 computed style properties against mini agent-1. No page overflow or script errors occurred. Runtime tests were not repeated for these documentation-only changes.
