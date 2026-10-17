"""Adapt delivery-only fields to platform editor limits; preserve saved source text."""
import re


def delivery_summary(platform, text):
    if platform != 'juejin':
        return text
    # The web editor counts UTF-16 units; non-BMP symbols count as two.
    if len(text.encode('utf-16-le')) // 2 <= 100:
        return text
    clipped=text.encode('utf-16-le')[:198].decode('utf-16-le',errors='ignore').rstrip()
    sentences=list(re.finditer(r'[。！？.!?](?:[”’"])?',clipped))
    if sentences and sentences[-1].end() >= 35:
        return clipped[:sentences[-1].end()]
    return clipped.rstrip('，、；：,;:')+'…'
