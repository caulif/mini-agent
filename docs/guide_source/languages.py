from copy import deepcopy
from pathlib import Path
import runpy

SOURCE = Path(__file__).resolve().parent
OUTPUTS = {'zh-CN': 'advanced-guide.zh-CN.html', 'en': 'advanced-guide.html'}

UI = {
 'zh-CN': dict(page_title='mini agent-2 · 实现指南',
  hero='mini agent-2',
  lead='接着 mini agent-1，补上重试、恢复、流式和工具权限。',
  handoff='<p>前一页停在 <a href="guide.zh-CN.html#ch7">mini agent-1 · 第7章</a>；这一页从 <a href="#ch8">第8章：失败与重试</a> 继续。两页对应同一份 mini_agent 代码包；原交互入口和本页的持久化运行入口分别组装。</p>',
  contents='目录',
  goal='目标', problems='要解决的问题', theory='原理', choices='设计取舍', files='本章新增或修改', implementation='怎么实现',
  new='新增', mod='修改', copy='复制', copy_label='复制代码',
  basics='mini agent-1', advanced='mini agent-2', brand='手搓一个 coding agent',
  basic_href='guide.zh-CN.html', switch='English', other='en',
  basic_last='← mini agent-1 · 第7章', advanced_contents='mini agent-2 · 目录'),
 'en': dict(page_title='mini agent-2 · Implementation guide',
  hero='mini agent-2',
  lead='Continue mini agent-1 with retries, recovery, streaming, and tool permissions.',
  handoff='<p>The previous page ends at <a href="guide.html#ch7">mini agent-1 · Chapter 7</a>. This page continues with <a href="#ch8">Chapter 8: Failures and retries</a>. Both pages use the same mini_agent package. The original interactive CLI and the journaled runtime use separate entry points.</p>',
  contents='Contents',
  goal='Goal', problems='The problem', theory='How it works', choices='Design trade-offs', files='Files introduced or changed', implementation='Implementation',
  new='New', mod='Changed', copy='Copy', copy_label='Copy code',
  basics='mini agent-1', advanced='mini agent-2', brand='Build a coding agent',
  basic_href='guide.html', switch='中文', other='zh-CN',
  basic_last='← mini agent-1 · Chapter 7', advanced_contents='mini agent-2 · Contents')
}


def load_chapters(language):
    chapters = runpy.run_path(str(SOURCE / 'chapters.py'))['CHAPTERS']
    if language == 'zh-CN':
        return chapters
    if language != 'en':
        raise ValueError(f'Unsupported language: {language}')
    translated = runpy.run_path(str(SOURCE / 'chapters_en.py'))['TEXT']
    assert set(translated) == {c['num'] for c in chapters}
    output = deepcopy(chapters)
    for c in output:
        en = translated[c['num']]
        for field in ('title', 'subtitle', 'goal', 'problems', 'theory', 'choices', 'boundary'):
            if isinstance(c[field], list):
                assert len(c[field]) == len(en[field]), (c['num'], field)
            c[field] = en[field]
        assert len(c['files']) == len(en['files'])
        c['files'] = [(tag, path, description) for (tag, path, _), description in zip(c['files'], en['files'])]
        assert len(c['steps']) == len(en['steps'])
        for step, (title, points, snippets) in zip(c['steps'], en['steps']):
            assert len(step['points']) == len(points)
            assert len(step['snippets']) == len(snippets)
            step.update(title=title, points=points)
            for spec, (caption, note) in zip(step['snippets'], snippets):
                spec.update(title=caption, note=note)
    return output


def chapter_label(number, language):
    return f'第 {number} 章' if language == 'zh-CN' else f'Chapter {number}'


def step_label(number, title, language):
    return f'第 {number} 步：{title}' if language == 'zh-CN' else f'Step {number}: {title}'
