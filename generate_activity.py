#!/usr/bin/env python3
"""Generate a self-hosted GitHub contribution + streak SVG.

Source: https://github.com/users/<login>/contributions
This is GitHub's public HTML fragment, not a documented API. The parser is
intentionally defensive and fails rather than publishing fabricated numbers.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import re
import sys
import urllib.error
import urllib.request

USERNAME = os.getenv("GITHUB_USERNAME", "RayaMandara")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Makassar")
OUTPUT = Path("assets/activity.svg")
PURPLE = "#8B5CF6"
PINK = "#EC4899"
CYAN = "#22D3EE"
BG = "#0D1117"
TEXT = "#E6EDF3"
MUTED = "#8B949E"
GRID = ["#21262D", "#3B2468", "#6236A3", "#8B5CF6", "#D946EF"]


def attrs_from_tag(tag: str) -> dict[str, str]:
    """Extract HTML attributes from a single opening tag."""
    return {
        key.lower(): (v1 or v2 or v3)
        for key, v1, v2, v3 in re.findall(
            r'([:\w-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
            tag,
        )
    }


def parse_count(text: str) -> int | None:
    if re.search(r"\bno\s+contributions?\b", text, re.I):
        return 0
    match = re.search(r"([\d,]+)\s+contributions?", text, re.I)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def parse_tooltips(html: str) -> dict[str, str]:
    """Map <tool-tip for="element-id"> text to its target cell ID."""
    tooltips: dict[str, str] = {}
    for match in re.finditer(r"<tool-tip\b([^>]*)>([\s\S]*?)</tool-tip>", html, re.I):
        attrs = attrs_from_tag(match.group(1))
        target_id = attrs.get("for", "")
        if target_id:
            clean_text = re.sub(r"<[^>]+>", " ", match.group(2))
            tooltips[target_id] = re.sub(r"\s+", " ", clean_text).strip()
    return tooltips


def parse_calendar(html: str) -> dict[date, int]:
    """Parse both current GitHub table + <tool-tip> markup and older SVG rect markup."""
    result: dict[date, int] = {}
    tooltips_by_id = parse_tooltips(html)

    # Current GitHub contribution table: <td data-date="..."> linked via id to <tool-tip for="...">.
    for match in re.finditer(r"<td\b[^>]*data-date=[\"'][^\"']+[\"'][\s\S]*?</td>", html, re.I):
        block = match.group(0)
        opening = re.search(r"<td\b[^>]*>", block, re.I)
        if not opening:
            continue
        attrs = attrs_from_tag(opening.group(0))
        raw_date = attrs.get("data-date", "")
        if not raw_date:
            continue
        try:
            day = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue

        count = None
        for key in ("data-count", "aria-label", "title"):
            if attrs.get(key):
                if key == "data-count" and attrs[key].isdigit():
                    count = int(attrs[key])
                else:
                    count = parse_count(attrs[key])
                if count is not None:
                    break

        if count is None:
            cell_id = attrs.get("id", "")
            if cell_id and cell_id in tooltips_by_id:
                count = parse_count(tooltips_by_id[cell_id])

        if count is None:
            # Fallback if tooltip text is nested directly inside the <td>.
            visible_text = re.sub(r"<[^>]+>", " ", block)
            visible_text = re.sub(r"\s+", " ", visible_text)
            count = parse_count(visible_text)

        if count is None:
            level = attrs.get("data-level", "0")
            if level == "0":
                count = 0
            else:
                raise RuntimeError(
                    f"Could not read exact tooltip count for {day}; "
                    "GitHub may have changed its contribution markup."
                )
        result[day] = count

    # Older markup: rect elements expose data-date and data-count directly.
    if not result:
        for tag_match in re.finditer(r"<rect\b[^>]*>", html, re.I):
            attrs = attrs_from_tag(tag_match.group(0))
            raw_date = attrs.get("data-date", "")
            raw_count = attrs.get("data-count", "")
            if not raw_date or not raw_count.isdigit():
                continue
            try:
                result[date.fromisoformat(raw_date[:10])] = int(raw_count)
            except ValueError:
                continue

    if len(result) < 300:
        raise RuntimeError(
            f"Expected roughly one year of daily contribution data, parsed {len(result)} days. "
            "GitHub may have changed its markup or returned an incomplete page."
        )
    return result


def fetch_calendar(username: str, start: date, end: date) -> dict[date, int]:
    url = (
        f"https://github.com/users/{username}/contributions"
        f"?from={start.isoformat()}&to={end.isoformat()}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RayaMandara-ProfileActivity/1.0",
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch GitHub contribution calendar: {exc}") from exc

    if status != 200 or ("ContributionCalendar" not in html and "data-date" not in html):
        raise RuntimeError("GitHub did not return the expected contribution calendar markup.")
    return parse_calendar(html)


def streaks(counts: dict[date, int], today: date) -> tuple[int, int]:
    active = {day for day, count in counts.items() if count > 0}

    # A current streak may end today, or yesterday if no contribution has
    # been made yet today. This avoids showing zero early in the local day.
    if today in active:
        cursor = today
    elif today - timedelta(days=1) in active:
        cursor = today - timedelta(days=1)
    else:
        cursor = None

    current = 0
    if cursor:
        while cursor in active:
            current += 1
            cursor -= timedelta(days=1)

    longest = 0
    run = 0
    previous = None
    for day in sorted(counts):
        if counts[day] > 0 and previous == day - timedelta(days=1):
            run += 1
        elif counts[day] > 0:
            run = 1
        else:
            run = 0
        longest = max(longest, run)
        previous = day
    return current, longest


def esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def render_svg(counts: dict[date, int], today: date, timezone: str) -> str:
    start = today - timedelta(days=364)
    days = [start + timedelta(days=i) for i in range(365)]
    # Align the first column to Sunday, as in GitHub's contribution graph.
    grid_start = start - timedelta(days=(start.weekday() + 1) % 7)
    grid_end = today + timedelta(days=(5 - today.weekday()) % 7)
    columns = ((grid_end - grid_start).days + 1) // 7

    total = sum(counts.get(day, 0) for day in days)
    current, longest = streaks(counts, today)
    active_days = sum(1 for day in days if counts.get(day, 0) > 0)

    width, height = 1120, 380
    cell, gap = 15, 4
    graph_width = columns * (cell + gap) - gap
    left = max(54, (width - graph_width) // 2 + 12)
    top = 196

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f'<title>{esc(USERNAME)} GitHub activity: {total} contributions, {current} day current streak, {longest} day longest streak</title>',
        f'<desc>Rolling 365-day contribution calendar generated from GitHub public profile data. Timezone: {esc(timezone)}.</desc>',
        '<defs><linearGradient id="accent" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#8B5CF6"/><stop offset=".55" stop-color="#EC4899"/><stop offset="1" stop-color="#22D3EE"/></linearGradient></defs>',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="20" fill="{BG}" stroke="#30363D"/>',
        '<text x="34" y="38" fill="#A78BFA" font-family="Arial,sans-serif" font-size="14" font-weight="700" letter-spacing="2">RAYA&apos;S CONTRIBUTION FIELD</text>',
        f'<text x="{width - 34}" y="38" text-anchor="end" fill="{MUTED}" font-family="Arial,sans-serif" font-size="12">LAST 365 DAYS · {esc(timezone)}</text>',
        '<rect x="34" y="54" width="1052" height="1" fill="#30363D"/>',
    ]

    cards = [
        (34, "TOTAL CONTRIBUTIONS", f"{total:,}", "in the last 365 days", PURPLE),
        (300, "CURRENT STREAK", f"{current} days", "consecutive contribution days", PINK),
        (566, "LONGEST STREAK", f"{longest} days", "personal best in this period", CYAN),
        (832, "ACTIVE DAYS", str(active_days), "days with at least one contribution", "#A78BFA"),
    ]
    for x, label, value, note, color in cards:
        parts.extend([
            f'<text x="{x}" y="88" fill="{MUTED}" font-family="Arial,sans-serif" font-size="11" font-weight="700" letter-spacing="1">{label}</text>',
            f'<text x="{x}" y="121" fill="{TEXT}" font-family="Arial,sans-serif" font-size="27" font-weight="700">{value}</text>',
            f'<text x="{x}" y="139" fill="{MUTED}" font-family="Arial,sans-serif" font-size="10">{note}</text>',
            f'<rect x="{x}" y="150" width="220" height="3" rx="2" fill="{color}" opacity=".85"/>',
        ])

    last_month_x = -999
    for day in days:
        if day.day == 1:
            x = left + ((day - grid_start).days // 7) * (cell + gap)
            if x - last_month_x >= 30:
                parts.append(
                    f'<text x="{x}" y="{top - 12}" fill="{MUTED}" font-family="Arial,sans-serif" font-size="11">{day.strftime("%b")}</text>'
                )
                last_month_x = x

    weekday_labels = [(0, "Mon"), (2, "Wed"), (4, "Fri")]
    for weekday, label in weekday_labels:
        y = top + ((weekday + 1) % 7) * (cell + gap) + 11
        parts.append(
            f'<text x="{left - 10}" y="{y}" text-anchor="end" fill="{MUTED}" font-family="Arial,sans-serif" font-size="10">{label}</text>'
        )

    for day in days:
        count = counts.get(day, 0)
        col = (day - grid_start).days // 7
        row = (day.weekday() + 1) % 7
        x = left + col * (cell + gap)
        y = top + row * (cell + gap)
        if count == 0:
            fill = GRID[0]
        elif count <= 2:
            fill = GRID[1]
        elif count <= 5:
            fill = GRID[2]
        elif count <= 9:
            fill = GRID[3]
        else:
            fill = GRID[4]
        tooltip = f"{count} contribution{'s' if count != 1 else ''} on {day.strftime('%b %d, %Y')}"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="{fill}">'
            f'<title>{esc(tooltip)}</title></rect>'
        )

    legend_y = top + 7 * (cell + gap) + 22
    legend_right = left + graph_width
    legend_x = legend_right - 132
    parts.append(
        f'<text x="{left}" y="{legend_y}" fill="#6E7681" font-family="Arial,sans-serif" font-size="10">Generated automatically · GitHub public contribution calendar</text>'
    )
    parts.append(
        f'<text x="{legend_x - 8}" y="{legend_y}" text-anchor="end" fill="{MUTED}" font-family="Arial,sans-serif" font-size="10">Less</text>'
    )
    for i, color in enumerate(GRID):
        parts.append(f'<rect x="{legend_x + i * 17}" y="{legend_y - 10}" width="12" height="12" rx="3" fill="{color}"/>')
    parts.append(
        f'<text x="{legend_x + 92}" y="{legend_y}" fill="{MUTED}" font-family="Arial,sans-serif" font-size="10">More</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", USERNAME):
        raise RuntimeError("GITHUB_USERNAME is not a valid GitHub login.")
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    start = today - timedelta(days=364)
    counts = fetch_calendar(USERNAME, start, today)

    # Fill missing dates with zero only after validating that the source
    # returned a complete rolling calendar.
    for offset in range(365):
        day = start + timedelta(days=offset)
        counts.setdefault(day, 0)

    svg = render_svg(counts, today, TIMEZONE)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(
        f"Updated {OUTPUT}: {sum(counts[d] for d in counts)} source contributions; "
        f"calendar window {start} to {today} ({TIMEZONE})."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"::error::{error}", file=sys.stderr)
        raise
