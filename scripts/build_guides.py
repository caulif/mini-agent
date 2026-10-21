import ast
from html import escape
import hashlib
import json
from pathlib import Path
import runpy
import sys
import re

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / 'guide_source'
LANGUAGES = runpy.run_path(str(SOURCE / 'languages.py'))
OUTPUTS = LANGUAGES['OUTPUTS']
UI = LANGUAGES['UI']


def source_code(spec, *, full=False, language='zh-CN'):
    ui = UI[language]
    path = ROOT / spec['path']
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    start, stop = 1, len(lines)
    if not full:
        node = ast.parse(text)
        if spec['symbol'] == 'CONFIG.update':
            node = next(n for n in node.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                        and ast.unparse(n.value.func) == 'CONFIG.update')
        else:
            for part in spec['symbol'].split('.'):
                matches = [n for n in ast.walk(node) if n is not node and isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == part]
                if len(matches) != 1:
                    raise ValueError(f'源码符号不唯一: {spec["path"]}:{spec["symbol"]}')
                node = matches[0]
        start = min([node.lineno, *(n.lineno for n in getattr(node, 'decorator_list', []))])
        stop = node.end_lineno
    code = '\n'.join(lines[start - 1:stop]) + '\n'
    focused = []
    for marker in spec.get('focus', ()):
        matches = [i for i, line in enumerate(code.splitlines(), 1) if marker in line]
        if not matches:
            raise ValueError(f'高亮未匹配: {spec["path"]}:{marker}')
        focused.append(matches[0])
    assert len(focused) <= 4
    rendered = highlight(code, PythonLexer(), HtmlFormatter(linenos='table', linenostart=start,
                         hl_lines=focused, cssclass='hl'))
    kind = 'file' if full else 'snippet'
    label = spec['title']
    return (f'<details class="codewrap" data-kind="{kind}"><summary>'
            f'<span class="sumcap">{escape(label)}</span><span class="sumfile">{escape(spec["path"])}</span>'
            f'<button class="copybtn" type="button" aria-label="{ui["copy_label"]}">{ui["copy"]}</button>'
            f'<span class="loc">{f"第{start}—{stop}行" if language == "zh-CN" else f"Lines {start}–{stop}"} · <a href="{escape(spec["path"])}">{ui["full_file"]}</a></span></summary>'
            f'<figure class="source" data-kind="{kind}" data-source-file="{escape(spec["path"])}" data-start="{start}" data-end="{stop}">'
            f'<div class="codeblock">{rendered}</div></figure>'
            + (f'<div class="codenote">{spec["note"]}</div>' if spec.get('note') else '') + '</details>'), dict(
                path=spec['path'], start=start, end=stop, kind=kind,
                symbol=None if full else spec['symbol'], sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def render(language='zh-CN'):
    chapters = LANGUAGES['load_chapters'](language)
    ui = UI[language]
    chapter_label = lambda n: LANGUAGES['chapter_label'](n, language)
    source_map, body = [], []
    css = (SOURCE / 'reference.css').read_text(encoding='utf-8')
    extras = (SOURCE / 'support.css').read_text(encoding='utf-8')
    script = (SOURCE / 'reader.js').read_text(encoding='utf-8')
    body.append(f'<div class="hero"><h1>{ui["hero"]}</h1><p class="lead">{ui["lead"]}</p>'
                f'<div class="tree toc" id="contents" style="font-size:13px"><div class="toc-title"><b>{ui["contents"]}</b></div>')
    for c in chapters:
        body.append(f'<div class="toc-row">{chapter_label(c["num"])} · <a href="#ch{c["num"]}">{escape(c["title"])}</a>　<span class="c">{escape(c["subtitle"])}</span></div>')
    body.append(f'</div></div><div class="scope-note series-handoff">{ui["handoff"]}</div><div class="scope-note"><b>{ui["intro_title"]}</b>{ui["intro"]}</div>')
    for c in chapters:
        body.append(f'<section class="chapter" id="ch{c["num"]}"><div class="chead"><span class="cnum">{chapter_label(c["num"])}</span>'
                    f'<h2>{escape(c["title"])}</h2><span class="csub">{escape(c["subtitle"])}</span></div>'
                    f'<div class="goal"><b>{ui["goal"]}</b>　{c["goal"]}</div><h3 class="sub">{ui["problems"]}</h3><ul>'
                    + ''.join(f'<li>{point}</li>' for point in c['problems']) + '</ul>'
                    + f'<h3 class="sub">{ui["theory"]}</h3><div class="theory">' + c['theory'] + '</div>'
                    + f'<h3 class="sub">{ui["choices"]}</h3><ul>' + ''.join(f'<li>{x}</li>' for x in c['choices']) + '</ul>'
                    + f'<h3 class="sub">{ui["files"]}</h3><div class="files">')
        for tag, path, description in c['files']:
            body.append(f'<div class="frow"><span class="tag {"new" if tag == "新增" else "mod"}">{ui["new"] if tag == "新增" else ui["mod"]}</span>'
                        f'<a class="mono" href="{escape(path)}">{escape(path)}</a><span class="fdesc">{description}</span></div>')
        body.append(f'</div><h3 class="sub">{ui["implementation"]}</h3><div class="steps">')
        for i, step in enumerate(c['steps'], 1):
            body.append(f'<div class="step"><h5>{LANGUAGES["step_label"](i, step["title"], language)}</h5><ul>'
                        + ''.join(f'<li>{x}</li>' for x in step['points']) + '</ul>')
            for spec in step['snippets']:
                html, record = source_code(spec, language=language)
                body.append(html); source_map.append(dict(chapter=c['num'], **record))
            body.append('</div>')
        body.append(f'</div><h3 class="sub">{ui["run"]}</h3><p>{ui["offline"]}</p><div class="run">')
        for label, command in c['run']:
            body.append(f'<div class="runrow"><div class="runlabel">{escape(label)}</div><pre>{escape(command)}</pre></div>')
        body.append(f'</div><h3 class="sub">{ui["checks"]}</h3><ul class="verify">' + ''.join(f'<li>{x}</li>' for x in c['checks'])
                    + f'</ul><div class="warnbox"><b>{ui["boundary"]}</b>{c["boundary"]}</div>'
                    + f'<h3 class="sub">{ui["full_sources"]}</h3><div class="steps full-files">')
        for _, path, title in c['files']:
            html, record = source_code(dict(path=path, title=title + ' · ' + ui['full_file']), full=True, language=language)
            body.append('<div class="step">' + html + '</div>'); source_map.append(dict(chapter=c['num'], **record))
        body.append('</div><div class="chapter-nav">')
        if c['num'] > 8:
            body.append(f'<a href="#ch{c["num"] - 1}">← {chapter_label(c["num"] - 1)}</a>')
        else:
            body.append(f'<a href="{escape(ui["basic_href"])}#ch7">{ui["basic_last"]}</a>')
        body.append(f'<a href="#contents">{ui["contents"]}</a>')
        if c['num'] < 13:
            body.append(f'<a href="#ch{c["num"] + 1}">{chapter_label(c["num"] + 1)} →</a>')
        body.append('</div></section>')
    body.append(f'<footer><a href="{escape(ui["basic_href"])}#ch7">{ui["basic_last"]}</a> · <a href="#contents">{ui["advanced_contents"]}</a></footer>')
    html = (f'<!DOCTYPE html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            + ''.join(f'<link rel="alternate" hreflang="{lang}" href="{file}">' for lang, file in OUTPUTS.items())
            + f'<title>{ui["page_title"]}</title><style>' + css + '\n' + extras + '</style></head><body>'
            f'<header class="top"><div class="top-in"><div class="brand">mini<span> agent-2</span> · {ui["brand"]}</div>'
            f'<nav><a href="{escape(ui["basic_href"])}">{ui["basics"]}</a><a href="#contents" aria-current="page">{ui["advanced"]}</a></nav>'
            f'<a class="language-switch" data-target="{OUTPUTS[ui["other"]]}" href="{OUTPUTS[ui["other"]]}" lang="{ui["other"]}" hreflang="{ui["other"]}">{ui["switch"]}</a>'
            '</div></header><main class="wrap">' + ''.join(body) + '</main><script>' + script + '</script></body></html>')
    return html, source_map


def symbol_nodes(text):
    found = {}

    def walk(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + child.name
                found[key] = child
                walk(child, key + '.')
            else:
                walk(child, prefix)

    walk(ast.parse(text))
    return found


def basic_source(spec):
    text = (ROOT / spec['path']).read_text(encoding='utf-8')
    lines = text.splitlines()
    nodes = symbol_nodes(text)
    if 'symbols' in spec:
        first, last = (nodes[name] for name in spec['symbols'])
        start = min([first.lineno, *(n.lineno for n in first.decorator_list)])
        stop = last.end_lineno
    else:
        scope = nodes.get(spec.get('scope'))
        active = [(i, line) for i, line in enumerate(lines, 1) if line.strip()
                  and (scope is None or scope.lineno <= i <= scope.end_lineno)]

        def locate(name):
            sequence = spec[name]
            matches = [i for i in range(len(active) - len(sequence) + 1)
                       if [line for _, line in active[i:i + len(sequence)]] == sequence]
            occurrence = spec[name + '_occurrence']
            if occurrence >= len(matches):
                raise ValueError(f'Source anchor missing: {spec["path"]}: {name}: {sequence}')
            offset = matches[occurrence]
            return active[offset if name == 'first' else offset + len(sequence) - 1][0]

        start, stop = locate('first'), locate('last')
    if not 1 <= start <= stop <= len(lines):
        raise ValueError(f'Invalid source range: {spec["path"]}:{start}-{stop}')
    return '\n'.join(lines[start - 1:stop]) + '\n', start, stop


def render_basic(language):
    specs = json.loads((SOURCE / 'agent1_sources.json').read_text(encoding='utf-8'))
    template = (SOURCE / f'agent1.{language}.html').read_text(encoding='utf-8')
    records = []

    def replace(match):
        kind, key, caption = match.groups()
        spec = specs[key]
        code, first, last = basic_source(spec)
        path = escape(spec['path'])
        location = f'第 {first}–{last} 行' if language == 'zh-CN' else f'Lines {first}–{last}'
        records.append(dict(path=spec['path'], start=first, end=last, kind=kind.lower()))
        if kind == 'INLINE':
            return (f'<p class="exnote">{caption} (<a href="{path}"><code>{path}</code></a> {location})</p>'
                    f'<pre class="ascii" data-source="{path}" data-first="{first}" data-last="{last}">{escape(code.rstrip())}</pre>')
        rendered = highlight(code, PythonLexer(), HtmlFormatter(linenos='table', linenostart=first, cssclass='hl'))
        copy = '复制' if language == 'zh-CN' else 'Copy'
        hint = '点击展开关键代码' if language == 'zh-CN' else 'Expand code'
        return (f'<details class="codewrap"><summary><span class="sumcap">{caption}</span>'
                f'<span class="sumfile"><a href="{path}">{path}</a>:{first}–{last}</span>'
                f'<span class="sumhint">{hint}</span><button class="copybtn" type="button" aria-label="{copy}">{copy}</button>'
                f'</summary><div class="codeblock" data-source="{path}" data-first="{first}" data-last="{last}">'
                f'<div class="fname">{path}<span class="rng">{location}</span></div>{rendered}</div></details>')

    html = re.sub(r'\{\{(SOURCE|INLINE):([^|]+)\|([\s\S]*?)\}\}', replace, template)
    if '{{SOURCE:' in html or '{{INLINE:' in html:
        raise ValueError('Unresolved guide source')
    return html, records


def build(check=False):
    outputs = {}
    mapping = {}
    for language, name in OUTPUTS.items():
        outputs[name], mapping[name] = render(language)
        basic_name = 'guide.zh-CN.html' if language == 'zh-CN' else 'guide.html'
        outputs[basic_name], mapping[basic_name] = render_basic(language)
    outputs['docs/source-map.json'] = json.dumps(mapping, ensure_ascii=False, indent=2) + '\n'
    for name, content in outputs.items():
        content = '\n'.join(line.rstrip() for line in content.splitlines()) + '\n'
        path = ROOT / name
        if check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                raise ValueError(f'Generated guide is stale: {name}')
        else:
            path.write_text(content, encoding='utf-8', newline='\n')
    print(('Checked' if check else 'Built') + ' both bilingual guides')


if __name__ == '__main__':
    build(check='--check' in sys.argv)
