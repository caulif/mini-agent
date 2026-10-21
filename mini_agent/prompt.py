import datetime
import os
import platform


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
    return (
        "\nEnvironment:\n"
        f"- working directory: {os.getcwd()}\n"
        f"- platform: {platform.system()} {platform.release()}\n"
        f"- today: {datetime.date.today().isoformat()}\n"
    )


SKILLS_DIRNAME = "skills"


def load_skills(root=None):
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
            continue
        if body:
            skills.append({"name": os.path.splitext(name)[0], "body": body})
    return skills


def skills_block(skills=None, root=None):
    skills = load_skills(root) if skills is None else skills
    if not skills:
        return ""
    parts = ["\nSkills (follow these when relevant):"]
    for skill in skills:
        parts.append(f"\n--- {skill['name']} ---\n{skill['body']}")
    return "\n".join(parts) + "\n"


def build_system_prompt(skills=None, root=None):
    return BASE_PROMPT + environment_block() + skills_block(skills, root)
