from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PAGES = ('guide.html', 'guide.zh-CN.html', 'advanced-guide.html', 'advanced-guide.zh-CN.html')


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.links = []
        self.ids = set()
        self.sources = []
        self.capture = None
        self.chapters = {}
        self.heading = None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'href' in attrs:
            self.links.append(attrs['href'])
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                raise AssertionError(f'Duplicate ID: {attrs["id"]}')
            self.ids.add(attrs['id'])
        if tag in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            return
        self.stack.append((tag, attrs))
        if 'chapter' in attrs.get('class', '').split():
            self.chapters[attrs['id']] = []
        if tag == 'h3' and attrs.get('class') == 'sub':
            chapter = next((a['id'] for _, a in reversed(self.stack)
                            if 'chapter' in a.get('class', '').split()), None)
            if chapter:
                self.heading = dict(chapter=chapter, text='')
        if tag == 'pre':
            numbered = any(a.get('class') in {'linenos', 'linenodiv'} for _, a in self.stack)
            source = next((a for _, a in reversed(self.stack)
                           if 'data-source' in a or 'data-source-file' in a), None)
            if source is not None and not numbered:
                self.capture = dict(path=source.get('data-source', source.get('data-source-file')),
                                    start=int(source.get('data-first', source.get('data-start'))),
                                    end=int(source.get('data-last', source.get('data-end'))), text='')

    def handle_endtag(self, tag):
        if tag == 'h3' and self.heading is not None:
            self.chapters[self.heading['chapter']].append(self.heading['text'])
            self.heading = None
        if tag == 'pre' and self.capture is not None:
            self.sources.append(self.capture)
            self.capture = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        if self.heading is not None:
            self.heading['text'] += text
        if self.capture is not None:
            self.capture['text'] += text


def check():
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_guides.py'), '--check'], check=True)
    pages = {name: Page((ROOT / name).read_text(encoding='utf-8')) for name in PAGES}
    expected_counts = (65, 65, 27, 27)
    for basic, advanced in (('guide.html', 'advanced-guide.html'), ('guide.zh-CN.html', 'advanced-guide.zh-CN.html')):
        expected = pages[basic].chapters['ch1']
        if len(expected) != 5 or len(pages[advanced].chapters) != 6:
            raise AssertionError('Unexpected chapter structure')
        for chapter, headings in pages[advanced].chapters.items():
            if headings != expected:
                raise AssertionError(f'{advanced}#{chapter}: headings differ from mini agent-1')
    report = {}
    for (name, page), count in zip(pages.items(), expected_counts):
        if len(page.sources) != count:
            raise AssertionError(f'{name}: expected {count} source excerpts, got {len(page.sources)}')
        for link in page.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            path = (ROOT / unquote(parsed.path)).resolve() if parsed.path else ROOT / name
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise AssertionError(f'{name}: invalid local link: {link}')
            if parsed.fragment and path.suffix == '.html':
                target = pages.get(path.name) or Page(path.read_text(encoding='utf-8'))
                if unquote(parsed.fragment) not in target.ids:
                    raise AssertionError(f'{name}: missing target fragment: {link}')
        for source in page.sources:
            lines = (ROOT / source['path']).read_text(encoding='utf-8').splitlines()
            expected = '\n'.join(lines[source['start'] - 1:source['end']]).rstrip() + '\n'
            if source['text'].rstrip() + '\n' != expected:
                raise AssertionError(f'{name}: stale displayed source: {source["path"]}:{source["start"]}')
        report[name] = dict(local_links_checked=len(page.links), source_excerpts_checked=len(page.sources))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    check()
