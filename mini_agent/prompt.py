# -*- coding: utf-8 -*-
"""System-prompt assembly (chapter 7).

Combine stable behavior instructions, a current environment snapshot, and
optional Markdown skills. Keep essential instructions first and project
knowledge last. The CLI builds this prompt once when App is initialized."""

import datetime
import os
import platform


# Layer 1: stable behavior instructions come first.
# These instructions define the agent's overall working style.
# They influence every turn, so keep changes deliberate.
BASE_PROMPT = """\
You are a coding agent working in a terminal.

How you work:
- Act, don't explain. If the user asks for a change, make the change.
- Read before you write. Never edit a file you have not just read.
- Prefer the `edit` tool over rewriting a whole file with `write`.
- When a tool returns an error, read the message and fix the call.
- Stop when the task is done. Do not keep poking around.

How you talk:
- Be terse. Report what you did, not what you are about to do.
- Use the user's language.
"""


def environment_block():
    """Read the working directory, platform, and date when called."""
    return (
        "\nEnvironment:\n"
        f"- working directory: {os.getcwd()}\n"
        f"- platform: {platform.system()} {platform.release()}\n"
        f"- today: {datetime.date.today().isoformat()}\n"
    )


# Layer 3: optional Markdown skills loaded from disk.
#
# A skill is a Markdown file describing how to handle a task.
# For example, git-commit.md can describe commit-message conventions.
# The agent receives this project-specific knowledge in its prompt.
#
# Markdown keeps project rules independent of Python implementation.
# Different working directories can provide different skills.

SKILLS_DIRNAME = "skills"


def load_skills(root=None):
    """Load nonempty Markdown files from the working directory's skills folder.
    
    Return dictionaries with name and body. A missing directory is allowed;
    this teaching implementation skips individual unreadable files."""
    root = root or os.getcwd()
    skills_dir = os.path.join(root, SKILLS_DIRNAME)
    if not os.path.isdir(skills_dir):
        return []

    skills = []
    for name in sorted(os.listdir(skills_dir)):
        if not name.lower().endswith(".md"):
            continue
        path = os.path.join(skills_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                body = f.read().strip()
        except OSError:
            continue                       # Skip an unreadable optional skill file.
        if body:
            skills.append({"name": os.path.splitext(name)[0], "body": body})
    return skills


def skills_block(skills=None, root=None):
    """Render skill contents, returning an empty string when no skills exist."""
    skills = load_skills(root) if skills is None else skills
    if not skills:
        return ""
    parts = ["\nSkills (follow these when relevant):"]
    for skill in skills:
        parts.append(f"\n--- {skill['name']} ---\n{skill['body']}")
    return "\n".join(parts) + "\n"


def build_system_prompt(skills=None, root=None):
    """Combine behavior instructions, environment information, and skills.
    
    Pass skills=[] to disable skills, or None to load them from disk."""
    return BASE_PROMPT + environment_block() + skills_block(skills, root)
