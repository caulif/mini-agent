# -*- coding: utf-8 -*-
"""
mini_agent · 系统提示词组装（第 7 章新增）

前面几章的系统提示词就是 llm.py 里那一行写死的字符串。
这一章把它变成"组装"出来的东西，因为有三个需求：

  1. **环境信息是动态的** —— 当前工作目录、今天日期、操作系统。
     写死的话 agent 就不知道自己在哪、今天是哪天。
  2. **技能是可扩展的** —— 把若干份 .md 说明文件拼进来，
     等于给 agent 加"专项知识"，而不用改代码。
  3. **顺序是有讲究的** —— 大模型的注意力在开头和结尾最强，
     最关键的约束（你该用什么风格干活）要放开头，
     易变的背景信息放后面。
"""

import datetime
import os
import platform

# ── 第 1 层：骨架（最关键的约束，放最前面）──────────────────
# 这几句话决定了 agent 的整体行为风格。改动这里要慎重，
# 它比任何一次工具调用都更影响最终表现。
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
    """第 2 层：当前环境。每次都重新算 —— 日期会变，目录会变。"""
    return (
        "\nEnvironment:\n"
        f"- working directory: {os.getcwd()}\n"
        f"- platform: {platform.system()} {platform.release()}\n"
        f"- today: {datetime.date.today().isoformat()}\n"
    )


# ── 第 3 层：技能（可选，从外部 .md 文件加载）─────────────────
#
# 一个"技能"就是一个 markdown 文件，讲清某类任务该怎么做。
# 比如 "git-commit.md" 里写好本项目的提交信息规范，
# agent 遇到提交任务时就有了这份额外知识。
#
# 为什么用文件而不是写进代码？因为改一个 .md 不需要碰代码，
# 而且可以按项目分别准备 —— 换项目就换一套技能。

SKILLS_DIRNAME = "skills"


def load_skills(root=None):
    """扫描 skills/ 目录，读回所有 .md 文件。

    返回列表，每项 {"name": 文件名（去扩展名）, "body": 内容}。
    目录不存在时返回空列表 —— 技能是可选增强，缺了不能报错。
    """
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
            continue                       # 单个文件读不了，跳过就好
        if body:
            skills.append({"name": os.path.splitext(name)[0], "body": body})
    return skills


def skills_block(skills=None, root=None):
    """第 3 层：技能正文。没有技能时返回空串。"""
    skills = load_skills(root) if skills is None else skills
    if not skills:
        return ""
    parts = ["\nSkills (follow these when relevant):"]
    for skill in skills:
        parts.append(f"\n--- {skill['name']} ---\n{skill['body']}")
    return "\n".join(parts) + "\n"


def build_system_prompt(skills=None, root=None):
    """拼出最终的系统提示词：骨架 + 环境 + 技能。

    顺序就是 骨架 → 环境 → 技能，理由见文件开头第 3 条。
    skills 传 [] 表示明确不要技能（测试时用），
    传 None 表示"你去磁盘上找"。
    """
    return BASE_PROMPT + environment_block() + skills_block(skills, root)
