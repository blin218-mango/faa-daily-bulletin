#!/usr/bin/env python3
import os
import re
import sys
import html
import json
import time
import math
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional

import requests
import xmltodict

NAS_XML_URL = "https://nasstatus.faa.gov/api/airport-status-information"

# -------- Helpers --------

def fetch_xml(url: str, timeout: int = 30) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        # Ensure we got XML
        if "xml" not in r.headers.get("Content-Type", "") and not r.text.strip().startswith("<"):
            print("Warning: response did not look like XML; continuing anyway.", file=sys.stderr)
        return r.text
    except Exception as e:
        print(f"ERROR fetching {url}: {e}", file=sys.stderr)
        return None

def walk(obj, path=()):
    """Yield (path, key, value) for every dict item in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (i,))
    else:
        yield (path[:-1], path[-1] if path else None, obj)

def find_all_key(obj, key_name: str) -> List[Any]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key_name:
                out.append(v)
            out.extend(find_all_key(v, key_name))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(find_all_key(v, key_name))
    return out

def ensure_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

def to_int_safe(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else None

# -------- Translation to plain English --------

TYPE_ALIASES = {
    "Ground Delay Programs": "Ground Delay Program",
    "Ground Delay Program": "Ground Delay Program",
    "Ground Stops": "Ground Stop",
    "Ground Stop": "Ground Stop",
    "Airport Closures": "Airport Closure",
    "Airport Closure": "Airport Closure",
    "Departure Delays": "Departure Delay",
    "Departure Delay": "Departure Delay",
    "Arrival Delays": "Arrival Delay",
    "Arrival Delay": "Arrival Delay",
    "Deicing": "Deicing",
}

def friendly_reason(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    txt = str(raw)
    # Strip NOTAM-like shouty codes and punctuation when it's all-caps and starts with !
    txt = re.sub(r"!\w+[^ ]* ?", "", txt).strip()
    # Common simplifications
    repl = [
        (r"\bGDP\b", "Ground Delay Program"),
        (r"\bGS\b", "Ground Stop"),
        (r"\bAFP\b", "Airspace Flow Program (en‑route constraint)"),
        (r"WX|WEATHER", "weather"),
        (r"VOLUME", "high traffic volume"),
        (r"LOW CEILINGS?", "low clouds"),
        (r"LOW VIS(IBILITY)?", "low visibility"),
        (r"DEICE|DEICING", "deicing operations"),
        (r"RWY|RUNWAY", "runway"),
        (r"TFR", "temporary flight restriction"),
    ]
    for pat, rep in repl:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)
    # Squash whitespace
    txt = re.sub(r"\s+", " ", txt).strip(" -:;")
    return txt or raw

def summarize_event(evt: Dict[str, Any]) -> str:
    typ = evt.get("type")
    ap = evt.get("airport")
    avg = evt.get("avg_delay")
    reason = evt.get("reason")
    start = evt.get("start")
    # Build a sentence
    base = ""
    if typ == "Ground Stop":
        base = f"{ap}: Ground stop in effect"
    elif typ == "Ground Delay Program":
        base = f"{ap}: Ground delay program"
    elif typ == "Airport Closure":
        base = f"{ap}: Airport closed"
    elif typ == "Departure Delay":
        base = f"{ap}: Departure delays"
    elif typ == "Arrival Delay":
        base = f"{ap}: Arrival delays"
    elif typ == "Deicing":
        base = f"{ap}: Deicing in effect"
    else:
        base = f"{ap}: {typ or 'Event'}"
    if avg:
        base += f" (~{avg} min avg delay)"
    if reason:
        base += f" — {reason}"
    if start:
        base += f" [since {start}]"
    return base

# -------- Parse NAS XML into a list of event dicts --------

def parse_events_from_xml(xml_text: str) -> List[Dict[str, Any]]:
    data = xmltodict.parse(xml_text)
    # Find all Delay_type blocks regardless of exact nesting
    delay_types = find_all_key(data, "Delay_type")
    events: List[Dict[str, Any]] = []

    for dt in delay_types:
        # Each dt is either a dict or list of dicts
        for dt_item in ensure_list(dt):
            name = dt_item.get("Name") if isinstance(dt_item, dict) else None
            std_type = TYPE_ALIASES.get(name or "", name or "Event")

            if not isinstance(dt_item, dict):
                continue
            # Find *_List keys with Airport entries
            for k, v in list(dt_item.items()):
                if isinstance(k, str) and k.endswith("_List"):
                    airports = v.get("Airport") if isinstance(v, dict) else None
                    for ap in ensure_list(airports):
                        if not isinstance(ap, dict):
                            continue
                        rec = {
                            "type": std_type,
                            "airport": ap.get("ARPT") or ap.get("Airport_Code") or ap.get("IATA") or "UNK",
                            "avg_delay": to_int_safe(ap.get("Average_Delay") or ap.get("Avg_Delay") or ap.get("Delay")),
                            "trend": ap.get("Trend"),
                            "reason": friendly_reason(ap.get("Reason")),
                            "start": ap.get("Start_Time") or ap.get("StartTime") or ap.get("Date"),
                            "end": ap.get("End_Time") or ap.get("EndTime"),
                        }
                        events.append(rec)
    # De-dup and sort by severity (avg_delay desc) then airport
    seen = set()
    deduped = []
    for e in events:
        key = (e["type"], e["airport"], e.get("reason") or "")
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    deduped.sort(key=lambda e: (-(e["avg_delay"] or 0), e["airport"]))
    return deduped

# -------- HTML rendering --------

def render_html(events: List[Dict[str, Any]], generated_at: datetime) -> str:
    # Tally counts by type
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1

    # Top 8 noteworthy
    top = events[:8]
    today = generated_at.astimezone().strftime("%A, %B %d, %Y %I:%M %p %Z")

    def esc(s): return html.escape(str(s))

    def pill(label, n):
        return f'<span class="pill">{esc(label)}: {esc(n)}</span>'

    pills = "".join(pill(k, v) for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    items = "\n".join(f"<li>{esc(summarize_event(e))}</li>" for e in top) or "<li>No active airport events right now.</li>"

    # Full list grouped by type
    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)

    sections = []
    for typ in sorted(by_type.keys()):
        lis = "\n".join(f"<li>{esc(summarize_event(e))}</li>" for e in by_type[typ])
        sections.append(f"<section><h3>{esc(typ)}</h3><ul>{lis}</ul></section>")

    sections_html = "\n".join(sections) if sections else "<p>No active events.</p>"

    css = """
    :root { --bg:#0b1220; --card:#121a2b; --text:#e7eefc; --muted:#b7c0d9; --accent:#7fb3ff; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji"; background: var(--bg); color: var(--text); }
    header { padding: 28px 20px; border-bottom: 1px solid #223; background: linear-gradient(180deg, #0b1220, #0b1220cc); position: sticky; top:0; backdrop-filter: blur(6px); }
    h1 { margin:0 0 6px; font-size: 24px; }
    .sub { color: var(--muted); font-size: 14px; }
    main { max-width: 920px; margin: 0 auto; padding: 18px 16px 64px; }
    .card { background: var(--card); border: 1px solid #223; border-radius: 14px; padding: 16px; margin: 16px 0; box-shadow: 0 10px 25px rgba(3,10,30,.25); }
    .row { display: flex; gap: 12px; flex-wrap: wrap; }
    .pill { display:inline-block; padding:6px 10px; border-radius: 999px; border:1px solid #234; background:#0e1730; color:#cfe0ff; font-size: 12px; }
    h2 { margin:10px 0 8px; font-size: 18px; }
    h3 { margin:10px 0 8px; font-size: 16px; color: var(--accent); }
    ul { margin: 0 0 0 18px; }
    footer { color: var(--muted); font-size: 12px; padding: 24px 16px; text-align: center; }
    a { color: var(--accent); }
    """

    html_doc = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FAA Daily Bulletin</title>
<style>{css}</style>
<header>
  <h1>FAA Daily Delay & Event Bulletin</h1>
  <div class="sub">Generated {esc(today)} — Source: FAA NAS Status (Active Airport Events)</div>
  <div class="row" style="margin-top:10px;">{pills}</div>
</header>
<main>
  <div class="card">
    <h2>Today’s Headlines</h2>
    <ul>
      {items}
    </ul>
  </div>
  <div class="card">
    <h2>All Active Airport Events</h2>
    {sections_html}
  </div>
</main>
<footer>
  Built automatically from the FAA NAS Status XML API. This is an unofficial summary; verify details with your airline and the FAA.
</footer>
</html>
"""
    return html_doc

def write_html(html_text: str, out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)

def main():
    xml_text = fetch_xml(NAS_XML_URL)
    now = datetime.now(timezone.utc)

    if not xml_text:
        # Fallback page
        html_text = f"""<!doctype html><meta charset='utf-8'><title>FAA Bulletin</title>
        <pre>Could not retrieve FAA data at {now.isoformat()}Z. Will try again next run.</pre>"""
        write_html(html_text, os.path.join("docs", "index.html"))
        print("Wrote fallback HTML.")
        return

    try:
        events = parse_events_from_xml(xml_text)
    except Exception as e:
        print(f"ERROR parsing XML: {e}", file=sys.stderr)
        events = []

    html_text = render_html(events, now)
    write_html(html_text, os.path.join("docs", "index.html"))
    print(f"Generated docs/index.html with {len(events)} events.")

if __name__ == "__main__":
    main()
