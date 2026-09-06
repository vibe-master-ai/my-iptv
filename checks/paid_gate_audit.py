#!/usr/bin/env python3
"""Audit the verified snapshot for decodable streams that are gated in practice.

This is deliberately a separate audit from the generator's ordinary playback
check.  A stream can be HTTP 200 and decode video while returning a tariff,
subscription, DRM, or provider error slate.  The audit records HTTP/manifest
evidence for every entry and samples one startup frame plus OCR for every
technically working entry.  It applies conservative removal decisions only to
explicit entitlement/error evidence; a clean sample means "not detected", not
proof of channel identity or distribution rights.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_streams
import iptv_filter as m

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "working.m3u"
OUTPUT = ROOT / "paid_gate_audit.json"
FRAME_DIR = ROOT / "identity_evidence" / "paid-gate"
MAX_BODY = 64 * 1024

# These are visible-slate/manifest terms, rather than ordinary programme
# words.  They are intentionally broad enough to flag a candidate for review,
# while the final removal decision requires the evidence to be explicit.
ENTITLEMENT_MARKERS = (
    "не включен в ваш тариф", "не включён в ваш тариф",
    "канал не включен", "канал не включён", "тарифный план",
    "не подключен к вашему тарифу", "доступен по подписке",
    "требуется подписка", "подписка требуется", "авторизация требуется",
    "канал недоступен", "оплатите подписку", "підписка потрібна",
    "оплатить подписку", "оплатить підписку",
    "канал не доступний", "тарифний план", "потрібна авторизація",
    "not included in your plan", "not included in your package",
    "subscription required", "channel is not included", "subscribe to watch",
    "subscription needed", "authorization required", "authenticate to watch",
    "pay for a subscription", "pay for subscription", "subscription to watch",
    "premium subscription", "pay tv", "pay-tv",
)
ERROR_MARKERS = (
    "access denied", "unauthorized", "forbidden", "geo blocked", "geoblocked",
    "not available in your region", "channel unavailable", "service unavailable",
    "в доступе отказано", "доступ запрещен", "доступ запрещён",
    "регионе недоступен", "сервіс недоступний", "доступ заборонено",
    "drm", "widevine", "fairplay", "playready",
)
PROMO_MARKERS = (
    "180+ телеканалов", "180+ telekanallar", "qr-код", "qr-kodni",
    "смотрите телеканалы",
)
OCR_MARKERS = tuple(dict.fromkeys(ENTITLEMENT_MARKERS + ERROR_MARKERS + PROMO_MARKERS))


def _text_flags(text: str) -> list[str]:
    haystack = (text or "").casefold()
    return sorted({marker for marker in OCR_MARKERS if marker in haystack})


def _http_sample(item: tuple[list[str], str]) -> dict:
    meta, url = item
    headers = check_streams.headers(meta)
    result = {"http_status": None, "content_type": "", "redirect_url": "",
              "manifest_flags": [], "http_error": ""}
    # curl gives this audit a hard process-level timeout.  A few IPTV servers
    # leave a chunked response open indefinitely, which can outlive Python's
    # socket read timeout; the byte range keeps the probe small when honored.
    marker = "__IPTV_AUDIT_STATUS__"
    cmd = ["curl", "-sS", "-L", "--connect-timeout", "5", "--max-time", "10",
           "--range", "0-65535", "--limit-rate", "256k", "-w",
           f"\n{marker}%{{http_code}}\n{marker}URL:%{{url_effective}}\n{marker}TYPE:%{{content_type}}",
           "-o", "-", url]
    for key, value in headers.items():
        cmd[-1:-1] = ["-H", f"{key}: {value}"]
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=15)
        payload = run.stdout or b""
        marker_bytes = ("\n" + marker).encode()
        first_marker = payload.find(marker_bytes)
        if first_marker < 0:
            result["http_error"] = (run.stderr or b"curl returned no status")[-500:].decode("utf-8", errors="replace")
            return result
        body = payload[:first_marker]
        # Parse all metadata from the original output to tolerate an empty body.
        full_text = payload.decode("utf-8", errors="replace")
        status_match = re.search(rf"{marker}(\d{{3}})", full_text)
        url_match = re.search(rf"{marker}URL:(.*?)\n", full_text)
        type_match = re.search(rf"{marker}TYPE:(.*?)(?:\n|$)", full_text)
        if status_match:
            result["http_status"] = int(status_match.group(1))
        result["redirect_url"] = url_match.group(1) if url_match else ""
        result["content_type"] = type_match.group(1) if type_match else ""
        if body.lstrip().startswith((b"#EXTM3U", b"<?xml", b"<MPD")):
            result["manifest_flags"] = _text_flags(body[:MAX_BODY].decode("utf-8", errors="replace"))
        elif b"<html" in body[:4096].lower() or b"<!doctype html" in body[:4096].lower():
            result["manifest_flags"] = ["html_error_page"]
        if run.returncode != 0:
            result["http_error"] = (run.stderr or b"curl failed")[-500:].decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        result["http_error"] = "curl timeout"
    except Exception as exc:  # network failures are evidence, not exceptions
        result["http_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
    return result


def _frame_sample(item: tuple[list[str], str], index: int) -> dict:
    meta, url = item
    stamp = f"paid-gate-{index:04d}"
    frame_path = FRAME_DIR / f"{stamp}.jpg"
    headers = check_streams.headers(meta)
    opts = ["-rw_timeout", "10000000", "-headers",
            "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *opts, "-i", url,
           "-t", "5", "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2",
           str(frame_path)]
    result = {"frame_status": "not_sampled", "ocr": "", "ocr_flags": [],
              "frame_path": ""}
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
        if run.returncode != 0 or not frame_path.exists() or frame_path.stat().st_size < 256:
            result["frame_status"] = "failed"
            result["frame_error"] = (run.stderr or "ffmpeg produced no frame")[-800:]
            return result
        result["frame_status"] = "sampled"
        # Tesseract is optional on runner machines.  Keep the image only if OCR
        # flags it, so this audit does not add hundreds of binary artifacts.
        try:
            ocr = subprocess.run(["tesseract", str(frame_path), "stdout", "--psm", "6"],
                                 capture_output=True, text=True, timeout=8)
            text = re.sub(r"\s+", " ", ocr.stdout or "").strip()
            result["ocr"] = text[:600]
            result["ocr_flags"] = _text_flags(text)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            result["ocr_error"] = type(exc).__name__
        if result["ocr_flags"]:
            result["frame_path"] = str(frame_path.relative_to(ROOT))
        else:
            frame_path.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        result["frame_status"] = "timeout"
    except Exception as exc:
        result["frame_status"] = "error"
        result["frame_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
    return result


def _classification(technical: dict, http: dict, frame: dict) -> tuple[str, str]:
    flags = sorted(set(http.get("manifest_flags", []) + frame.get("ocr_flags", [])))
    status = technical.get("status", "")
    code = http.get("http_status")
    if code in (401, 403, 451):
        return "inaccessible_auth_or_geo", f"HTTP {code}"
    if code in (None, 0):
        return "inaccessible_network", http.get("http_error", "HTTP probe timed out")
    if isinstance(code, int) and code >= 400:
        return "inaccessible_http", f"HTTP {code}"
    if flags and any(f in ENTITLEMENT_MARKERS or f in ERROR_MARKERS or f == "html_error_page"
                     for f in flags):
        if any(f in ENTITLEMENT_MARKERS for f in flags):
            return "entitlement_or_paywall", "; ".join(flags)
        return "error_or_auth_slate", "; ".join(flags)
    if status != "working":
        return "technical_failure", technical.get("reason", status)
    if flags:
        return "provider_promo_or_identity_review", "; ".join(flags)
    return "decodable_no_gate_marker", "No paywall/auth/error marker in HTTP/manifest or sampled OCR"


def _technical_baseline(entries: list[tuple[list[str], str]], recheck: bool) -> list[dict]:
    """Reuse timestamped generated results unless a fresh pass is requested."""
    if not recheck:
        source = json.loads((ROOT / "results.json").read_text())
        by_url = {row.get("url"): row for row in source}
        baseline = []
        for i, (meta, url) in enumerate(entries):
            row = dict(by_url.get(url, {
                "url": url, "name": m.split_extinf(meta[0])[1],
                "status": "unconfirmed", "reason": "No generated result",
            }))
            row["index"] = i
            row["audit_technical_source"] = "checks/results.json"
            row["audit_technical_checked_at"] = row.get("checked_at", "")
            baseline.append(row)
        return baseline
    technical: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(check_streams.check, (i, *entry)): i
                   for i, entry in enumerate(entries)}
        for n, future in enumerate(cf.as_completed(futures), 1):
            technical.append(future.result())
            if n % 25 == 0:
                print(f"technical {n}/{len(entries)}", flush=True)
    technical.sort(key=lambda row: row["index"])
    return technical


def audit(recheck_technical: bool = False) -> list[dict]:
    entries = m.parse_entries(SNAPSHOT.read_text())
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    technical = _technical_baseline(entries, recheck_technical)
    if not recheck_technical:
        print("technical baseline: checks/results.json", flush=True)
    http: list[dict] = [{} for _ in entries]
    with cf.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_http_sample, entry): i for i, entry in enumerate(entries)}
        for future in cf.as_completed(futures):
            i = futures[future]
            http[i] = future.result()
    # A single edge can rotate between a stale 404/5xx and a live response.
    # Retry only explicit HTTP/error-slate results, and retain every attempt in
    # the JSON so a later removal is auditable rather than inferred.
    retry_indices = [i for i, sample in enumerate(http)
                     if sample.get("http_status") in (None, 0)
                     or (isinstance(sample.get("http_status"), int)
                         and sample["http_status"] >= 400)]
    for i in retry_indices:
        attempts = []
        for _ in range(2):
            attempts.append(_http_sample(entries[i]))
        http[i]["retries"] = attempts
        good = next((attempt for attempt in attempts
                     if isinstance(attempt.get("http_status"), int)
                     and 200 <= attempt["http_status"] < 300
                     and not attempt.get("manifest_flags")), None)
        if good:
            # Do not attach the attempts list to one of its own members:
            # that creates a circular object and prevents JSON serialization.
            selected = dict(good)
            selected["retries"] = [dict(attempt) for attempt in attempts]
            http[i] = selected
    frames: list[dict] = [{} for _ in entries]
    working = [i for i, row in enumerate(technical) if row.get("status") == "working"]
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_frame_sample, entries[i], i): i for i in working}
        for n, future in enumerate(cf.as_completed(futures), 1):
            frames[futures[future]] = future.result()
            if n % 25 == 0:
                print(f"frames {n}/{len(working)}", flush=True)
    output: list[dict] = []
    for i, entry in enumerate(entries):
        t, u = technical[i], entry[1]
        classification, reason = _classification(t, http[i], frames[i])
        row = dict(t)
        row.update({"snapshot_index": i, "url": u,
                    "http": http[i], "frame": frames[i],
                    "classification": classification, "classification_reason": reason})
        output.append(row)
    payload = {"audit_started_at": started,
               "audit_finished_at": datetime.now(timezone.utc).isoformat(),
               "snapshot": str(SNAPSHOT.name), "entries": output,
               "summary": dict(Counter(r["classification"] for r in output))}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recheck-technical", action="store_true",
                        help="run ffprobe/ffmpeg for every snapshot URL")
    audit(parser.parse_args().recheck_technical)
