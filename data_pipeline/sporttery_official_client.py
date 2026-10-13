#!/usr/bin/env python3
"""Read-only client for documented-in-repository Sporttery public web endpoints.

The Sporttery web API may return HTTP 567 from datacenter networks. This client
does not attempt to evade WAF controls or rotate proxies. It reports access
failures to the caller and leaves source IDs unresolved.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://webapi.sporttery.cn/gateway"
ENDPOINTS = {
    "results": BASE + "/uniform/football/getUniformMatchResultV1.qry",
    "match_list": BASE + "/uniform/football/getMatchListV1.qry",
    "match_info": BASE + "/uniform/football/getMatchInfoV1.qry",
    "fixed_bonus": BASE + "/uniform/football/getFixedBonusV1.qry",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

class SportteryAccessError(RuntimeError):
    def __init__(self, endpoint: str, status: int | None, message: str):
        self.endpoint = endpoint
        self.status = status
        super().__init__(f"{endpoint}: HTTP {status}: {message}" if status else f"{endpoint}: {message}")

def _get(endpoint: str, params: dict[str, Any], timeout: int = 25) -> dict[str, Any]:
    url = ENDPOINTS[endpoint] + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        # Explicitly do not retry or try to bypass HTTP 567/WAF.
        raise SportteryAccessError(endpoint, exc.code, str(exc.reason)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SportteryAccessError(endpoint, None, str(exc)) from exc
    if not isinstance(payload, dict):
        raise SportteryAccessError(endpoint, None, "unexpected non-object JSON response")
    if payload.get("success") is False:
        raise SportteryAccessError(endpoint, None, str(payload.get("errorMessage") or "success=false"))
    return payload

def get_results(start_date: str, end_date: str | None = None) -> list[dict[str, Any]]:
    """Historical result query; date strings use YYYY-MM-DD."""
    payload = _get("results", {"startDate": start_date, "endDate": end_date or start_date})
    value = payload.get("value") or {}
    rows = value.get("matchResult") or []
    flattened: list[dict[str, Any]] = []
    for row in rows:
        children = row.get("subMatchList") or [row]
        for child in children:
            item = dict(row)
            item.update(child)
            flattened.append(item)
    return flattened

def get_match_list() -> list[dict[str, Any]]:
    """Official published match list; usually current sales/fixture coverage."""
    payload = _get("match_list", {"clientCode": "3001"})
    value = payload.get("value") or {}
    rows = value.get("matchList") or value.get("matchInfoList") or []
    flattened: list[dict[str, Any]] = []
    for row in rows:
        children = row.get("subMatchList") or [row]
        for child in children:
            item = dict(row)
            item.update(child)
            flattened.append(item)
    return flattened

def get_match_info(match_id: str | int) -> dict[str, Any]:
    payload = _get("match_info", {"clientCode": "3001", "matchId": str(match_id)})
    return payload.get("value") or {}

def get_fixed_bonus(match_id: str | int) -> dict[str, Any]:
    payload = _get("fixed_bonus", {"clientCode": "3001", "matchId": str(match_id)})
    return payload.get("value") or {}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Probe official Sporttery public endpoints without bypassing access controls.")
    parser.add_argument("--start-date", default="2026-09-24")
    parser.add_argument("--end-date", default="2026-09-24")
    args = parser.parse_args()
    summary = {"tested": [], "results_count": None, "match_list_count": None, "errors": []}
    try:
        summary["results_count"] = len(get_results(args.start_date, args.end_date))
        summary["tested"].append("results")
    except SportteryAccessError as exc:
        summary["errors"].append(str(exc))
    try:
        summary["match_list_count"] = len(get_match_list())
        summary["tested"].append("match_list")
    except SportteryAccessError as exc:
        summary["errors"].append(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
