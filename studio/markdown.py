"""Small, deliberately safe Markdown renderer. Raw HTML is displayed as text."""
import html
import re
from urllib.parse import urlsplit


def safe_url(value, image=False):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    if image and re.fullmatch(r"/media/[a-f0-9]{32}\.(png|jpg|gif|webp)", value):
        return value
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""


def inline(text):
    tokens = []

    def token(value):
        tokens.append(value)
        return f"\x00{len(tokens)-1}\x00"

    text = text.replace("\x00", "")
    text = re.sub(r"`([^`]+)`", lambda m: token("<code>" + html.escape(m[1]) + "</code>"), text)

    def link(m):
        is_image, label, url = m[1], m[2], safe_url(m[3], bool(m[1]))
        if not url:
            return html.escape(label)
        url, label = html.escape(url, quote=True), html.escape(label)
        if is_image:
            return token(f'<img src="{url}" alt="{label}" loading="lazy" referrerpolicy="no-referrer">')
        return token(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')

    text = re.sub(r"(!?)\[([^\]\n]*)\]\(([^\s)]+)\)", link, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m[1])], text)


def render(source):
    blocks, paragraph, code, quotation = [], [], [], []
    fenced, listing = False, None

    def flush():
        if paragraph:
            blocks.append("<p>" + "<br>".join(inline(x) for x in paragraph) + "</p>")
            paragraph.clear()

    def end_list():
        nonlocal listing
        if listing:
            blocks.append(f"</{listing}>")
            listing = None

    def end_quote():
        if quotation:
            parts = []
            for part in "\n".join(quotation).split("\n\n"):
                if part.strip():
                    parts.append("<p>" + "<br>".join(inline(x) for x in part.splitlines()) + "</p>")
            blocks.append("<blockquote>" + "".join(parts) + "</blockquote>")
            quotation.clear()

    for line in source.splitlines():
        if not fenced and (line == ">" or line.startswith("> ")):
            flush()
            end_list()
            quotation.append(line[2:] if line.startswith("> ") else "")
            continue
        end_quote()
        if line.startswith("```"):
            flush()
            end_list()
            if fenced:
                blocks.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = []
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        item = re.match(r"^\s*(?:([-*+])|\d+\.)\s+(.+)$", line)
        if not item:
            end_list()
        if not line.strip():
            flush()
        elif re.fullmatch(r'@\[([^\]\n]{0,120})\]\(/video/[a-f0-9]{32}\.mp4\)',line.strip()):
            flush();end_list()
            match=re.fullmatch(r'@\[([^\]\n]{0,120})\]\((/video/[a-f0-9]{32}\.mp4)\)',line.strip())
            blocks.append('<figure class="article-video"><video controls preload="metadata" playsinline src="'+match[2]+'"></video><figcaption>'+html.escape(match[1])+'</figcaption></figure>')
        elif heading:
            flush()
            level = len(heading[1])
            blocks.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        elif item:
            flush()
            kind = "ul" if item[1] else "ol"
            if listing != kind:
                end_list()
                listing = kind
                blocks.append(f"<{kind}>")
            blocks.append(f"<li>{inline(item[2])}</li>")
        elif re.fullmatch(r"\s*(---+|\*\*\*+)\s*", line):
            flush()
            blocks.append("<hr>")
        else:
            paragraph.append(line)
    flush()
    end_quote()
    end_list()
    if fenced:
        blocks.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    return "\n".join(blocks)
