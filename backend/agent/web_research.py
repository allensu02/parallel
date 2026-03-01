"""Web research utility — real web search + page content extraction.

Uses DuckDuckGo HTML search (via httpx) and page fetching for content.
Fast, reliable, and requires no API keys.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import unquote

import httpx


# ---------------------------------------------------------------------------
# Search via DuckDuckGo HTML endpoint
# ---------------------------------------------------------------------------

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Regex patterns for parsing DDG HTML results
_RESULT_LINK = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.+?)</a>',
    re.S,
)
_RESULT_SNIPPET = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div|span)',
    re.S,
)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _STRIP_TAGS_RE.sub("", html).strip()


def _clean_ddg_url(raw_url: str) -> str:
    """Extract the actual URL from DDG's redirect wrapper."""
    if "uddg=" in raw_url:
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            return unquote(match.group(1))
    return raw_url


async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search the web via DuckDuckGo HTML endpoint.

    Returns list of {title, url, snippet} dicts.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
            headers=_HEADERS,
        ) as client:
            resp = await client.post(_DDG_URL, data={"q": query, "b": ""})
            if resp.status_code != 200:
                print(f"[WebResearch] DDG returned {resp.status_code}")
                return []

        html = resp.text

        # Extract links and titles
        links = _RESULT_LINK.findall(html)
        snippets = _RESULT_SNIPPET.findall(html)

        results: list[dict[str, str]] = []
        for i, (raw_url, raw_title) in enumerate(links[:max_results]):
            url = _clean_ddg_url(raw_url)
            title = _strip_tags(raw_title)
            snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""

            # Skip DDG internal links
            if not url.startswith("http"):
                continue

            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })

        return results

    except Exception as exc:
        print(f"[WebResearch] Search failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Page content extraction
# ---------------------------------------------------------------------------

_COLLAPSE_WS = re.compile(r"\s{2,}")


def _html_to_text(html: str, max_chars: int = 3000) -> str:
    """Crude but fast HTML-to-text extraction."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    # Strip tags
    text = _STRIP_TAGS_RE.sub(" ", text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = _COLLAPSE_WS.sub(" ", text).strip()
    return text[:max_chars]


async def fetch_page_text(url: str, timeout: float = 8.0) -> str:
    """Fetch a URL and extract readable text content.

    Returns extracted text or empty string on failure.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                return _html_to_text(resp.text)
    except Exception as exc:
        print(f"[WebResearch] Fetch failed for {url[:60]}: {exc}")
    return ""


# ---------------------------------------------------------------------------
# Full research pipeline: search → fetch → combine
# ---------------------------------------------------------------------------


async def research_topic(
    query: str,
    task_context: str = "",
    max_search_results: int = 5,
    max_pages_to_fetch: int = 3,
) -> dict[str, Any]:
    """Do comprehensive web research on a topic.

    1. Search the web via DuckDuckGo
    2. Fetch top result pages for detailed content
    3. Return structured research data

    Returns:
        {
            "query": str,
            "search_results": [{title, url, snippet}],
            "page_contents": [{url, title, text}],
            "combined_text": str,
        }
    """
    # Step 1: Search
    search_results = await web_search(query, max_results=max_search_results)
    if not search_results:
        return {
            "query": query,
            "search_results": [],
            "page_contents": [],
            "combined_text": f"No search results found for: {query}",
        }

    # Step 2: Fetch top pages concurrently
    urls_to_fetch = [r["url"] for r in search_results[:max_pages_to_fetch] if r.get("url")]
    page_tasks = [fetch_page_text(url) for url in urls_to_fetch]
    page_texts = await asyncio.gather(*page_tasks, return_exceptions=True)

    page_contents = []
    for url, text_or_exc in zip(urls_to_fetch, page_texts):
        if isinstance(text_or_exc, str) and text_or_exc:
            title = next((r["title"] for r in search_results if r["url"] == url), "")
            page_contents.append({"url": url, "title": title, "text": text_or_exc})

    # Step 3: Combine into a research brief
    parts = []
    parts.append(f"Web search results for: {query}\n")
    for i, r in enumerate(search_results, 1):
        parts.append(f"\n[{i}] {r['title']}")
        parts.append(f"    URL: {r['url']}")
        parts.append(f"    {r['snippet']}")

    if page_contents:
        parts.append("\n\n--- Detailed page content ---\n")
        for pc in page_contents:
            parts.append(f"\n## {pc['title']} ({pc['url'][:60]})")
            parts.append(pc["text"][:2000])

    combined = "\n".join(parts)

    return {
        "query": query,
        "search_results": search_results,
        "page_contents": page_contents,
        "combined_text": combined,
    }
