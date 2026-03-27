"""
History Service — Scan history, behavioral pattern analysis, and credibility scoring.

Parses and analyzes user activity history to detect behavioral patterns,
flag anomalies, and generate credibility scores.
"""

import json
import os
import datetime
import logging
from collections import Counter

logger = logging.getLogger(__name__)

HISTORY_FILE = "scan_history.json"


def _load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_history(data: list):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4, default=str)


def add_scan_entry(entry: dict):
    """Add a new scan entry to history."""
    entry["timestamp"] = datetime.datetime.now().isoformat()
    entry["id"] = len(_load_history()) + 1
    history = _load_history()
    history.append(entry)
    _save_history(history)
    return entry


def get_history(limit: int = 100) -> list:
    """Get scan history, most recent first."""
    history = _load_history()
    history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return history[:limit]


def get_session_stats() -> dict:
    """Calculate aggregate statistics from scan history."""
    history = _load_history()

    if not history:
        return {
            "total_scans": 0,
            "real_count": 0,
            "fake_count": 0,
            "avg_confidence": 0,
            "credibility_score": 100,
            "anomalies": [],
            "patterns": [],
            "timeline": [],
        }

    total = len(history)
    real_count = sum(1 for h in history if h.get("result") == "REAL IMAGE")
    fake_count = sum(1 for h in history if h.get("result") == "DEEPFAKE IMAGE")

    confidences = [h.get("confidence", 0) for h in history if h.get("confidence")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    # ── Pattern Detection ─────────────────────────────────
    patterns = []
    anomalies = []

    # Pattern: High fake ratio
    if total >= 3:
        fake_ratio = fake_count / total
        if fake_ratio > 0.7:
            patterns.append({
                "type": "high_fake_ratio",
                "label": "High Deepfake Rate",
                "description": f"{round(fake_ratio * 100)}% of scans detected as deepfake",
                "severity": "warning",
                "icon": "fa-triangle-exclamation",
            })
        elif fake_ratio < 0.2 and total >= 5:
            patterns.append({
                "type": "mostly_authentic",
                "label": "Mostly Authentic",
                "description": f"{round((1 - fake_ratio) * 100)}% of scans are authentic images",
                "severity": "positive",
                "icon": "fa-circle-check",
            })

    # Pattern: Confidence trend
    if len(confidences) >= 3:
        recent_conf = confidences[:5]
        avg_recent = sum(recent_conf) / len(recent_conf)
        if avg_recent > 0.95:
            patterns.append({
                "type": "high_confidence",
                "label": "High Model Confidence",
                "description": f"Recent scans show {round(avg_recent * 100, 1)}% avg confidence",
                "severity": "positive",
                "icon": "fa-bullseye",
            })
        elif avg_recent < 0.6:
            patterns.append({
                "type": "low_confidence",
                "label": "Low Confidence Detected",
                "description": "Model is uncertain about recent images — results may be unreliable",
                "severity": "warning",
                "icon": "fa-circle-question",
            })

    # Anomaly: Rapid successive scans
    if len(history) >= 2:
        timestamps = []
        for h in history:
            try:
                timestamps.append(datetime.datetime.fromisoformat(h["timestamp"]))
            except (KeyError, ValueError):
                pass

        if len(timestamps) >= 2:
            timestamps.sort(reverse=True)
            diffs = [(timestamps[i] - timestamps[i+1]).total_seconds() for i in range(min(5, len(timestamps)-1))]
            if any(d < 5 for d in diffs):
                anomalies.append({
                    "type": "rapid_scans",
                    "label": "Rapid Scanning Detected",
                    "description": "Multiple scans within 5 seconds — possible automated usage",
                    "severity": "warning",
                    "icon": "fa-bolt",
                })

    # Anomaly: Same result streak
    if len(history) >= 5:
        recent_results = [h.get("result") for h in history[:5]]
        if len(set(recent_results)) == 1:
            anomalies.append({
                "type": "result_streak",
                "label": f"Result Streak: {recent_results[0]}",
                "description": "Last 5 scans returned identical results",
                "severity": "info",
                "icon": "fa-arrows-repeat",
            })

    # ── Credibility Score ─────────────────────────────────
    credibility = 100

    # Penalize high fake ratio
    if total >= 3:
        fake_ratio = fake_count / total
        if fake_ratio > 0.5:
            credibility -= (fake_ratio - 0.5) * 40

    # Penalize low confidence
    if avg_confidence < 0.7:
        credibility -= (0.7 - avg_confidence) * 30

    # Bonus for consistency
    if total >= 5 and avg_confidence > 0.9:
        credibility = min(100, credibility + 5)

    credibility = max(0, min(100, round(credibility, 1)))

    # ── Timeline (last 7 days) ────────────────────────────
    timeline = []
    now = datetime.datetime.now()
    for i in range(7):
        day = now - datetime.timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%a")

        day_scans = [h for h in history if h.get("timestamp", "").startswith(day_str)]
        day_real = sum(1 for h in day_scans if h.get("result") == "REAL IMAGE")
        day_fake = sum(1 for h in day_scans if h.get("result") == "DEEPFAKE IMAGE")

        timeline.append({
            "date": day_str,
            "label": day_label,
            "total": len(day_scans),
            "real": day_real,
            "fake": day_fake,
        })

    timeline.reverse()

    return {
        "total_scans": total,
        "real_count": real_count,
        "fake_count": fake_count,
        "avg_confidence": round(avg_confidence * 100, 1),
        "credibility_score": credibility,
        "anomalies": anomalies,
        "patterns": patterns,
        "timeline": timeline,
    }
