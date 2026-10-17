"""Content-based illustration anchors; never reuse stale numeric offsets."""
import hashlib
import re
from collections import Counter


LEGACY_END = 'legacy_end'


def _blocks(body):
    result, pending, start, offset, fence = [], [], 0, 0, ''

    def flush():
        if pending:
            text = ''.join(pending).rstrip()
            result.append({'text': text, 'start': start, 'end': start + len(text),
                           'heading': None, 'code': text.startswith(('```', '~~~'))})
            pending.clear()

    for line in body.splitlines(keepends=True):
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line.rstrip('\r\n')) if not fence else None
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            if not fence:
                flush()
                fence = marker[1][0]
                start = offset
            elif marker[1][0] == fence:
                fence = ''
            pending.append(line)
            if not fence:
                flush()
        elif heading:
            flush()
            text = line.rstrip()
            result.append({'text': text, 'start': offset, 'end': offset + len(text),
                           'heading': (len(heading[1]), heading[2]), 'code': False})
        elif not line.strip() and not fence:
            flush()
        else:
            if not pending:
                start = offset
            pending.append(line)
        offset += len(line)
    flush()
    return result


def _signature(text, author):
    return bool(re.fullmatch(r'(?:—+|–+|--\s*)[^\n]{1,60}', text) or
                re.match(r'^(?:作者|署名)\s*[:：]', text) or
                (author and text.strip() == author.strip()))


def _key(kind, path, text):
    payload = '\x00'.join([str(kind), *[f'{level}:{title}' for level, title in path], text])
    return ('s_' if kind == 'section' else 'p_') + hashlib.sha256(payload.encode()).hexdigest()


def choices(body, author=''):
    """Return unique chapter/paragraph targets with offsets recomputed from current text."""
    blocks = _blocks(body)
    path, paragraphs, headings = [], [], []
    for block in blocks:
        if _signature(block['text'], author):
            break
        if block['heading']:
            level, title = block['heading']
            path = [item for item in path if item[0] < level] + [(level, title)]
            headings.append({**block, 'path': list(path), 'level': level, 'title': title})
            continue
        text = block['text']
        # A target is a complete prose/list block; never insert into a code fence,
        # an existing image, a caption, a signature, or a standalone reference link.
        if (block['code'] or re.match(r'^!\[', text) or re.match(r'^图注[:：]', text) or
                re.fullmatch(r'[-*_]{3,}', text) or
                re.fullmatch(r'(?:https?://\S+|\[[^\]]+\]\(https?://\S+\))', text)):
            continue
        paragraphs.append({**block, 'path': list(path)})
    result = []
    paragraph_counts = Counter(_key('paragraph', p['path'], p['text']) for p in paragraphs)
    for h in headings:
        ending = next((other['start'] for other in headings if other['start'] > h['start'] and other['level'] <= h['level']), len(body))
        candidates = [p for p in paragraphs if h['end'] <= p['start'] < ending]
        if candidates:
            last = candidates[-1]
            if paragraph_counts[_key('paragraph', last['path'], last['text'])] != 1:
                continue
            result.append({'id': _key('section', h['path'], last['text']), 'kind': 'section',
                           'label': '章节末：' + h['title'], 'section': h['title'],
                           'excerpt': last['text'][:180], 'end': last['end'],
                           'weight': sum(len(p['text']) for p in candidates), 'level': h['level']})
    for index, p in enumerate(paragraphs):
        section = p['path'][-1][1] if p['path'] else '开篇'
        result.append({'id': _key('paragraph', p['path'], p['text']), 'kind': 'paragraph',
                       'label': f'段落后：{section} · {p["text"][:42]}', 'section': section,
                       'excerpt': p['text'][:180], 'end': p['end'], 'weight': len(p['text'])})
    counts = Counter(item['id'] for item in result)
    # Repeated identical anchors are ambiguous. Never guess by first occurrence.
    return [item for item in result if counts[item['id']] == 1]


def public_choices(body, author=''):
    return [{key: item[key] for key in ('id', 'kind', 'label', 'section', 'excerpt')}
            for item in choices(body, author)]


def locate(body, placement, author=''):
    if placement == LEGACY_END:
        return {'id': LEGACY_END, 'label': '旧计划：追加到文章末尾（可能在署名之后）',
                'excerpt': body[-180:], 'end': len(body)}
    match = next((item for item in choices(body, author) if item['id'] == placement), None)
    if not match:
        from .workflows import fail
        fail('插图位置已变化或尚未选择，请按当前正文重新选择章节或段落；没有追加到文末', 409)
    return match


def default_targets(body, author=''):
    options = choices(body, author)
    sections = [p for p in options if p['kind'] == 'section' and
                not re.search(r'写在最后|结语|总结|参考|延伸阅读|致谢|署名', p['section'])]
    if sections:
        main_level = min(p['level'] for p in sections)
        if main_level == 1 and sum(p['level'] == 1 for p in sections) == 1 and any(p['level'] == 2 for p in sections):
            main_level = 2
        sections = [p for p in sections if p['level'] == main_level]
        # Prefer substantial chapters; a short article gets at most one body image.
        substantial = [p for p in sections if p['weight'] >= 160]
        return (substantial or sections[:1])[:4]
    paragraphs = [p for p in options if p['kind'] == 'paragraph']
    return [max(paragraphs, key=lambda p: p['weight'])] if paragraphs else []


def image_block(url, caption, title):
    # User-provided descriptions are plain text, never executable Markdown/HTML.
    def plain(value):
        return re.sub(r'[\[\]<>*_`\\]', '', value).replace('\r', ' ').replace('\n', ' ').strip()
    label = plain(caption or title)
    return '![' + label + '](' + url + ')' + ('\n\n图注：' + plain(caption) if caption.strip() else '')


def insert(body, block, placement, author=''):
    target = locate(body, placement, author)
    at = target['end']
    return body[:at].rstrip() + '\n\n' + block + '\n\n' + body[at:].lstrip('\r\n'), target
