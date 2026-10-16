#!/usr/bin/env python3
"""Conservative public-page scraper for Leisu. No private endpoints or access-control bypass."""
from __future__ import annotations
import hashlib, json, re, sys, urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import urljoin

BASE = "https://live.leisu.com"
DEFAULT_LIST = BASE + "/wanchang"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; V52PublicPageAudit/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": BASE + "/",
}

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text_parts = []
        self._href = None
        self._anchor_text = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._href = attrs.get("href")
            self._anchor_text = []
    def handle_data(self, data):
        s = data.strip()
        if not s:
            return
        self.text_parts.append(s)
        if self._href is not None:
            self._anchor_text.append(s)
    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor_text).strip()))
            self._href = None
            self._anchor_text = []

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype.lower() and b"<html" not in raw[:2000].lower():
            raise RuntimeError("BLOCKED_NON_HTML_RESPONSE")
    return raw

def parse_list(raw, source_url=DEFAULT_LIST):
    html = raw.decode("utf-8", errors="replace")
    p = PageParser()
    p.feed(html)
    rows = []
    seen = set()
    for href, label in p.links:
        m = re.search(r"/detail-(\d+)", href or "")
        if not m:
            continue
        event_id = m.group(1)
        if event_id in seen:
            continue
        seen.add(event_id)
        rows.append({
            "provider": "leisu_web",
            "event_id": event_id,
            "detail_url": urljoin(source_url, href),
            "anchor_label": label or None,
            "list_source_url": source_url,
            "list_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fields_verified_from_list": ["event_id", "detail_url"],
            "home": None, "away": None, "kickoff": None, "score_ft": None,
            "handicap_odds": None, "result": None,
            "status": "DISCOVERED_NEEDS_DETAIL_PARSE",
        })
    return rows

def parse_detail(raw, event_id, source_url):
    """Extract only fields with explicit, recognizable labels; do not guess layout."""
    html = raw.decode("utf-8", errors="replace")
    p = PageParser()
    p.feed(html)
    text = " ".join(p.text_parts)
    # Conservative score parsing: only explicit full-time labels are accepted.
    score = None
    patterns = [
        r"(?:全场|90分钟|全场比分)\s*[:：]?\s*(\d{1,2})\s*[-:]\s*(\d{1,2})",
        r"(?:FT|Full Time)\s*[:：]?\s*(\d{1,2})\s*[-:]\s*(\d{1,2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            score = [int(m.group(1)), int(m.group(2))]
            break
    return {
        "provider": "leisu_web", "event_id": str(event_id), "detail_url": source_url,
        "page_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "visible_text_excerpt": text[:3000],
        "score_ft": score,
        "fields_verified": ["event_id", "detail_url"] + (["score_ft"] if score else []),
        "status": "DETAIL_PARTIAL" if score is None else "DETAIL_SCORE_PARSED",
        "note": "Home/away, kickoff, odds and result are left unset unless a stable explicit label parser is verified.",
    }

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LIST
    raw = fetch(url)
    rows = parse_list(raw, url)
    print(json.dumps({
        "source_url": url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "html_bytes": len(raw), "detail_links_found": len(rows),
        "sample": rows[:10],
        "status": "LIST_PAGE_PARSED" if rows else "BLOCKED_NO_PUBLIC_DETAIL_LINKS",
        "limitations": ["A detail URL is not proof that all fields are available.",
                        "No API endpoint is called; no login or anti-bot controls are bypassed."]
    }, ensure_ascii=False, indent=2))
    if not rows:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
