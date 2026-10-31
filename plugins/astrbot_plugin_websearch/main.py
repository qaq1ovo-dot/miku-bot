"""联网搜索：给初音加上「搜网页」和「读网页」两个工具。

不需要 API key，直接抓 Bing / 百度的搜索结果页，再配合正文抓取。
"""

from __future__ import annotations

import html
import re

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")


def _clean(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    return " ".join(text.split()).strip()


def _parse_bing(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url, title = m.group(1), _clean(m.group(2))
        snippet = ""
        sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if sm:
            snippet = _clean(sm.group(1))
        if title:
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _parse_baidu(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<div class="result[^"]*".*?(?=<div class="result|<div id="page")', page, re.S):
        m = re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url, title = m.group(1), _clean(m.group(2))
        snippet = ""
        sm = re.search(r'<span class="content-right_8Zs40">(.*?)</span>', block, re.S) or re.search(
            r'<div class="c-abstract[^"]*"[^>]*>(.*?)</div>', block, re.S
        )
        if sm:
            snippet = _clean(sm.group(1))
        if title:
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


@register(
    "astrbot_plugin_websearch",
    "dsh",
    "联网搜索 / 读网页（Bing、百度，无需 key）",
    "1.0.0",
)
class WebSearchPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}

    def _engine(self) -> str:
        return str(self.config.get("engine") or "bing").lower()

    async def _get(self, url: str, timeout: int = 20) -> str | None:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status != 200:
                        logger.warning("[websearch] %s -> %s", url, resp.status)
                        return None
                    return await resp.text(errors="ignore")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[websearch] 请求失败 %s: %s", url, exc)
            return None

    async def _search(self, query: str, count: int) -> list[dict]:
        from urllib.parse import quote

        engines = [self._engine(), "baidu" if self._engine() == "bing" else "bing"]
        for engine in engines:
            if engine == "baidu":
                page = await self._get(f"https://www.baidu.com/s?wd={quote(query)}&rn=10")
                items = _parse_baidu(page) if page else []
            else:
                page = await self._get(f"https://cn.bing.com/search?q={quote(query)}&count=10")
                items = _parse_bing(page) if page else []
            if items:
                return items[:count]
        return []

    @filter.llm_tool()
    async def search_web(self, event: AstrMessageEvent, query: str = "", count: int = 0):
        """搜索互联网上最新的信息。当需要查资料、新闻、价格、百科、人物近况等你不确定的内容时使用。

        Args:
            query(string): 搜索关键词，尽量简短精准
            count(number): 返回几条结果，1~8，默认 5
        """
        if not self.config.get("enable", True):
            return "搜索功能已关闭"
        query = (query or "").strip()
        if not query:
            return "没有给搜索关键词"
        try:
            want = int(count) if count else int(self.config.get("max_results", 5) or 5)
        except Exception:  # noqa: BLE001
            want = 5
        want = max(1, min(8, want))

        items = await self._search(query, want)
        if not items:
            return f"搜「{query}」没搜到结果，可以换个说法再试。"

        lines = [f"「{query}」的搜索结果："]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['title']}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
            if item.get("url"):
                lines.append(f"   {item['url']}")
        return "\n".join(lines)

    @filter.llm_tool()
    async def read_webpage(self, event: AstrMessageEvent, url: str = ""):
        """读取某个网页的正文内容。搜索结果里的链接如果看起来有用，可以用它打开细看。

        Args:
            url(string): 完整的 http/https 链接
        """
        if not self.config.get("enable", True):
            return "搜索功能已关闭"
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return "链接格式不对，需要 http:// 或 https:// 开头"

        page = await self._get(url, timeout=25)
        if not page:
            return f"打不开这个页面：{url}"

        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page)
        text = _clean(body)
        limit = int(self.config.get("fetch_chars", 2500) or 2500)
        if len(text) > limit:
            text = text[:limit] + "……（后面省略）"
        return f"网页内容（{url}）：\n{text}"
