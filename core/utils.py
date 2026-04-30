"""通用工具函数。"""

import html
import re
from urllib.parse import urlparse


def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return all([parsed.scheme, parsed.netloc])
    except ValueError:
        return False


def is_valid_umo(umo: str) -> bool:
    pattern = r"([^:]+):\s*([^:]+):\s*(.+)"
    return re.match(pattern, umo) is not None


def render_text_to_plain(text: str) -> str:
    """把 Soulter 仓库残留的 HTML 片段转纯文本(动态 desc/summary 偶尔含 <br>)。"""
    if not text:
        return ""
    plain = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    plain = re.sub(r"<a [^>]*>(.*?)</a>", r"\1", plain, flags=re.IGNORECASE)
    plain = re.sub(r"<img [^>]*>", "", plain, flags=re.IGNORECASE)
    plain = re.sub(r"</?[^>]+>", "", plain)
    plain = html.unescape(plain)
    lines = [line.strip() for line in plain.splitlines()]
    return "\n".join(line for line in lines if line)
