from copy import deepcopy
from pathlib import Path
import runpy

SOURCE = Path(__file__).resolve().parent
OUTPUTS = {'zh-CN': 'advanced-guide.zh-CN.html', 'en': 'advanced-guide.html'}

UI = {
 'zh-CN': dict(page_title='mini agent-2 · 实现指南',
  hero='mini agent-2',
  lead='mini agent-1 的七章把工具循环接成了一个可运行的 agent。接下来，请求会失败，工具会中断，响应也可能只到一半。第8—13章沿着这些断点，展开重试、恢复、流式、权限、评估与会话组合。',
  handoff='<p>前一页停在 <a href="guide.zh-CN.html#ch7">mini agent-1 · 第7章</a>；这一页从 <a href="#ch8">第8章：失败与重试</a> 继续。两页对应同一份 mini_agent 代码包；原交互入口和本页的持久化运行入口分别组装。</p>',
  contents='目录', intro_title='运行代码与阅读方式',
  intro='<p>每个实现步骤对应实际源码，关键行用蓝色标记。代码默认折叠，保留真实行号；章末另有包含imports的完整文件。短流程用于解释关系，不作为运行通过的证据。</p><p>第8章更新共享请求层，第9—13章使用独立入口 <code>python -m mini_agent.advanced</code>。默认只读，无shell，工具串行；基础Session、自动压缩与动态技能尚未迁入该入口。</p>',
  goal='目标', problems='要解决的问题', theory='原理', choices='设计取舍', files='本章新增或修改', implementation='怎么实现', run='怎么运行', checks='验证要点', boundary='实现边界：', full_sources='完整源码', full_file='完整文件',
  offline='以下命令在仓库根目录执行，不调用真实模型。',
  new='新增', mod='修改', copy='复制', copy_label='复制代码',
  basics='mini agent-1', advanced='mini agent-2', brand='手搓一个 coding agent',
  basic_href='guide.zh-CN.html', switch='English', other='en',
  basic_last='← mini agent-1 · 第7章', advanced_contents='mini agent-2 · 目录'),
 'en': dict(page_title='mini agent-2 · Implementation guide',
  hero='mini agent-2',
  lead='The seven chapters in mini agent-1 connect the tool loop into a working agent. Requests can fail, tools can be interrupted, and responses can stop halfway through. Chapters 8–13 follow these breaks into retries, recovery, streaming, permissions, evaluation, and session composition.',
  handoff='<p>The previous page ends at <a href="guide.html#ch7">mini agent-1 · Chapter 7</a>. This page continues with <a href="#ch8">Chapter 8: Failures and retries</a>. Both pages use the same mini_agent package. The original interactive CLI and the journaled runtime use separate entry points.</p>',
  contents='Contents', intro_title='Runtime code and source excerpts',
  intro='<p>Every implementation step links to actual source, with key lines highlighted in blue. Code is collapsed by default and retains its original line numbers; complete files, including imports, appear at the end of each chapter. Short flow sketches explain relationships rather than establish execution results. Source code is reproduced verbatim.</p><p>Chapter 8 updates the shared request layer. Chapters 9–13 use <code>python -m mini_agent.advanced</code>, a separate entry point with read-only defaults, no shell, and serial tools. Basic Session files, automatic compaction, and dynamic skills are not integrated into this entry point.</p>',
  goal='Goal', problems='The problem', theory='How it works', choices='Design choices', files='Files added or changed', implementation='Implementation', run='Running it', checks='Verification points', boundary='Implementation limits: ', full_sources='Complete source files', full_file='Complete file',
  offline='Run these commands from the repository root. They make no real model requests.',
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
        for field in ('title', 'subtitle', 'goal', 'problems', 'theory', 'choices', 'checks', 'boundary'):
            if isinstance(c[field], list):
                assert len(c[field]) == len(en[field]), (c['num'], field)
            c[field] = en[field]
        assert len(c['files']) == len(en['files'])
        c['files'] = [(tag, path, description) for (tag, path, _), description in zip(c['files'], en['files'])]
        assert len(c['run']) == len(en['run'])
        c['run'] = [(label, command.replace('<日志路径>', '<journal-path>')) for (_, command), label in zip(c['run'], en['run'])]
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
