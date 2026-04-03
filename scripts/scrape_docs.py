#!/usr/bin/env python3
"""Scrape Atlassian, Adaptavist, and Appfire documentation for RAG indexing.

Uses Playwright (headless Chromium) for React-rendered Atlassian pages and
requests for static sites. Automatically detects index/navigation pages and
crawls their child links one level deep.

Prerequisites:
    pip install playwright beautifulsoup4
    playwright install chromium

Usage:
    python scripts/scrape_docs.py
    python scripts/scrape_docs.py --output ./docs --delay 2.0
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests as req
from bs4 import BeautifulSoup, Tag


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PLAYWRIGHT_DOMAINS = {"atlassian.com", "atlassian.net"}


@dataclass
class Source:
    """A documentation page to scrape."""
    name: str
    url: str


SOURCES: list[Source] = [
    # -- 1. Atlassian DC → Cloud migration --
    Source("Atlassian Migration Resources",
           "https://support.atlassian.com/migration/resources/"),
    Source("Atlassian DC vs Cloud Differences",
           "https://support.atlassian.com/migration/docs/differences-administering-jira-server-or-data-center-and-cloud/"),
    Source("Atlassian App Migration Guidance",
           "https://support.atlassian.com/migration/docs/app-specific-migration-guidance/"),

    # -- 2. Jira Cloud REST API --
    Source("Jira Cloud REST API v3",
           "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/"),
    Source("Jira Software Cloud REST API",
           "https://developer.atlassian.com/cloud/jira/software/rest/"),
    Source("Jira Cloud API Authentication",
           "https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/"),

    # -- 3. ScriptRunner for Jira Cloud --
    Source("ScriptRunner for Jira Cloud",
           "https://docs.adaptavist.com/sr4jc/"),
    Source("ScriptRunner DC vs Cloud Differences",
           "https://docs.adaptavist.com/sr4js/8.x/scriptrunner-migration/migrating-to-cloud/"
           "platform-differences-between-scriptrunner-for-jira-server-and-jira-cloud"),
    Source("ScriptRunner Feature Parity",
           "https://docs.adaptavist.com/sr4js/8.x/scriptrunner-migration/platform-features/feature-parity"),
    Source("ScriptRunner Cloud Script Console",
           "https://docs.adaptavist.com/sr4jc/latest/script-console/"),

    # -- 4. JSU (Jira Suite Utilities) Cloud --
    Source("JSU Cloud Documentation",
           "https://appfire.atlassian.net/wiki/spaces/JSUCLOUD/pages/1721834794"),
    Source("JSU DC Documentation",
           "https://appfire.atlassian.net/wiki/spaces/JSU/pages/12682250"),

    # -- 5. Jira Cloud Native Automation --
    Source("Jira Cloud Automation",
           "https://support.atlassian.com/jira-software-cloud/docs/automate-your-work-with-automation/"),
    Source("Jira Automation Triggers",
           "https://support.atlassian.com/jira-software-cloud/docs/automation-triggers/"),
    Source("Jira Automation Conditions",
           "https://support.atlassian.com/jira-software-cloud/docs/automation-conditions/"),
    Source("Jira Automation Branches",
           "https://support.atlassian.com/jira-software-cloud/docs/automation-branches/"),
    Source("Jira Automation Actions",
           "https://support.atlassian.com/jira-software-cloud/docs/automation-actions/"),
    Source("Jira Cloud Migration Assistant",
           "https://support.atlassian.com/migration/docs/migrate-jira-data-with-the-jira-cloud-migration-assistant/"),

    # -- 6. Jira Cloud Webhooks --
    Source("Jira Cloud Webhooks",
           "https://developer.atlassian.com/cloud/jira/platform/webhooks/"),
    Source("Jira Webhook Event Reference",
           "https://developer.atlassian.com/cloud/jira/platform/webhooks-event-reference/"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _needs_playwright(url: str) -> bool:
    """Return True if the domain is React-rendered and needs headless Chromium."""
    hostname = urlparse(url).hostname or ""
    return any(hostname == d or hostname.endswith(f".{d}")
               for d in PLAYWRIGHT_DOMAINS)


def _sanitize(text: str, max_len: int = 60) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = re.sub(r'[^\w\-]', '_', text)
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug[:max_len]


def _normalize_url(url: str) -> str:
    """Strip query/fragment for deduplication."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

_MAIN_SELECTORS = [
    'main', 'article', '[role="main"]', '#main-content',
    '#content', '.wiki-content', '.article-content',
    '[data-testid="article-content"]',
]

_STRIP_TAGS = [
    'script', 'style', 'nav', 'footer', 'aside',
    'noscript', 'iframe', 'svg', 'button', 'form',
]


def _find_main(soup: BeautifulSoup) -> Tag:
    """Locate the primary content container."""
    for sel in _MAIN_SELECTORS:
        el = soup.select_one(sel)
        if el:
            return el
    return soup.find('body') or soup


def _html_to_markdown(container: Tag) -> str:
    """Convert an HTML element tree to approximate Markdown.

    Mutates the container in-place — extract links before calling this.
    """
    for tag_name in _STRIP_TAGS:
        for t in container.find_all(tag_name):
            t.decompose()

    for level in range(1, 7):
        for h in container.find_all(f'h{level}'):
            text = h.get_text(strip=True)
            if text:
                h.replace_with(f"\n\n{'#' * level} {text}\n\n")

    for pre in container.find_all('pre'):
        code = pre.get_text()
        if code.strip():
            pre.replace_with(f"\n\n```\n{code.strip()}\n```\n\n")

    for table in container.find_all('table'):
        rows: list[str] = []
        for tr in table.find_all('tr'):
            cells = [td.get_text(strip=True)
                     for td in tr.find_all(['td', 'th'])]
            if any(cells):
                rows.append(' | '.join(cells))
        table.replace_with('\n' + '\n'.join(rows) + '\n')

    for li in container.find_all('li'):
        text = li.get_text(strip=True)
        if text:
            li.replace_with(f"\n- {text}")

    text = container.get_text('\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _extract_links(container: Tag, base_url: str) -> list[str]:
    """Extract and deduplicate same-domain links from the content area."""
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[str] = []

    for a in container.find_all('a', href=True):
        href = a['href']
        if href.startswith(('#', 'mailto:', 'javascript:')):
            continue
        full = _normalize_url(urljoin(base_url, href))
        if (urlparse(full).netloc == base_host
                and full.rstrip('/') != base_url.rstrip('/')
                and full not in seen):
            seen.add(full)
            out.append(full)
    return out


def _filter_children(links: list[str], parent_url: str) -> list[str]:
    """Keep only links under the parent URL's path hierarchy."""
    path = urlparse(parent_url).path.rstrip('/')
    segs = [s for s in path.split('/') if s]

    if len(segs) >= 2:
        prefix = '/' + '/'.join(segs[:-1])
    elif segs:
        prefix = '/' + segs[0]
    else:
        return links

    return [l for l in links if urlparse(l).path.startswith(prefix + '/')]


def _is_index(text: str, links: list[str]) -> bool:
    """Heuristic: page is an index if it has many links and little body text."""
    return len(text) < 3000 and len(links) > 10


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class DocScraper:
    """Fetches documentation pages and saves them as Markdown files."""

    def __init__(self, output_dir: Path, delay: float = 1.5) -> None:
        self.output_dir = output_dir
        self.delay = delay
        self._visited: set[str] = set()
        self._pw: object | None = None
        self._browser: object | None = None

    # -- Fetching ----------------------------------------------------------

    def _ensure_playwright(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("\nERROR: playwright is required for Atlassian pages.")
            print("  pip install playwright && playwright install chromium")
            sys.exit(1)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)

    def _fetch_playwright(self, url: str) -> str:
        self._ensure_playwright()
        page = self._browser.new_page(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 720},
        )
        try:
            try:
                page.goto(url, wait_until="networkidle", timeout=45_000)
            except Exception:
                # networkidle can time out on pages with persistent connections;
                # the DOM content is almost always loaded by this point.
                pass
            page.wait_for_timeout(2000)
            return page.content()
        finally:
            page.close()

    @staticmethod
    def _fetch_requests(url: str) -> str:
        resp = req.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; JiraMigrationDocBot/1.0)"
        })
        resp.raise_for_status()
        return resp.text

    def _fetch(self, url: str, retries: int = 2) -> str:
        """Fetch a URL with automatic retry on transient failure."""
        for attempt in range(retries + 1):
            try:
                if _needs_playwright(url):
                    return self._fetch_playwright(url)
                return self._fetch_requests(url)
            except Exception:
                if attempt < retries:
                    wait = self.delay * (attempt + 1)
                    print(f"(retry in {wait:.0f}s) ", end='', flush=True)
                    time.sleep(wait)
                else:
                    raise

    def _close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    # -- Processing --------------------------------------------------------

    def _process(self, html: str, url: str) -> tuple[str, list[str]]:
        """Parse HTML into markdown text and a list of same-domain links."""
        soup = BeautifulSoup(html, 'html.parser')
        main = _find_main(soup)
        links = _extract_links(main, url)       # extract before mutation
        md = _html_to_markdown(main)             # mutates main
        return md, links

    def _save(self, name: str, url: str, md: str, slug: str = "") -> Path:
        """Write a markdown file with YAML frontmatter."""
        fname = _sanitize(name)
        if slug:
            fname += f"__{_sanitize(slug)}"
        fname += ".md"
        fp = self.output_dir / fname
        fp.write_text(
            f"---\nsource: {name}\nurl: {url}\n---\n\n{md}",
            encoding='utf-8',
        )
        return fp

    # -- Main loop ---------------------------------------------------------

    def run(self, sources: list[Source]) -> None:
        """Scrape all sources and save as Markdown files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        saved = 0

        for src in sources:
            norm = _normalize_url(src.url)
            if norm in self._visited:
                continue

            print(f"\n{'=' * 60}")
            print(f"[{src.name}]")
            print(f"  {src.url}")

            try:
                html = self._fetch(src.url)
                self._visited.add(norm)
            except Exception as e:
                print(f"  ERROR: {e}")
                time.sleep(self.delay)
                continue

            md, links = self._process(html, src.url)

            # --- Index page: crawl children one level deep ----------------
            if _is_index(md, links):
                children = _filter_children(links, src.url)
                print(f"  Index page detected "
                      f"({len(children)} child links, {len(md):,} chars body)")

                child_saved = 0
                for i, child_url in enumerate(children, 1):
                    cnorm = _normalize_url(child_url)
                    if cnorm in self._visited:
                        continue

                    slug = (urlparse(child_url).path.rstrip('/').split('/')[-1]
                            or "page")
                    print(f"    [{i}/{len(children)}] {slug} ... ",
                          end='', flush=True)

                    time.sleep(self.delay)

                    try:
                        child_html = self._fetch(child_url)
                        self._visited.add(cnorm)
                    except Exception as e:
                        print(f"ERROR: {e}")
                        continue

                    child_md, _ = self._process(child_html, child_url)
                    if len(child_md) < 100:
                        print("(too short, skipped)")
                        continue

                    self._save(src.name, child_url, child_md, slug)
                    print(f"saved ({len(child_md):,} chars)")
                    child_saved += 1
                    saved += 1

                print(f"  {child_saved} child page(s) saved")

            # --- Content page: save directly ------------------------------
            else:
                if len(md) < 100:
                    print("  (too short, skipped)")
                    time.sleep(self.delay)
                    continue

                fp = self._save(src.name, src.url, md)
                print(f"  Saved: {fp.name} ({len(md):,} chars)")
                saved += 1

            time.sleep(self.delay)

        self._close()
        print(f"\n{'=' * 60}")
        print(f"Done. {saved} file(s) saved to {self.output_dir}/")
        print(f"\nNext step:")
        print(f"  python scripts/index_docs.py {self.output_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape documentation sites into Markdown for RAG indexing.",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=Path("docs"),
        help="Output directory (default: ./docs).",
    )
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Seconds between requests (default: 1.5).",
    )
    args = parser.parse_args()

    print("Documentation Scraper")
    print(f"  Sources: {len(SOURCES)}")
    print(f"  Output:  {args.output.resolve()}")
    print(f"  Delay:   {args.delay}s")

    scraper = DocScraper(output_dir=args.output, delay=args.delay)
    scraper.run(SOURCES)


if __name__ == "__main__":
    main()
