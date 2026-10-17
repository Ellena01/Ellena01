#!/usr/bin/env python3
"""
Build the two self-hosted activity SVGs for the GitHub profile README.

    assets/helen-metrics.svg    -> compact metric strip (section 01)
    assets/helen-activity.svg   -> contribution heatmap + language panel (section 05)

Why this exists: the previous README depended on four third-party image
services (github-readme-stats, streak-stats, komarev, activity-graph) for
numbers that are trivially available from the official GitHub API. If any of
those services goes down the profile fills with broken images. This script
renders the same information locally from a single GraphQL call, so every
number on the page is real, sourced and refreshable.

Usage
-----
    python3 scripts/build_activity_panel.py --fetch     # hit the GitHub API (needs `gh` + GH_TOKEN)
    python3 scripts/build_activity_panel.py             # re-render from data/activity.json

Only the Python standard library is used. No pip install, no build step.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
SNAPSHOT = DATA / "activity.json"

LOGIN = "Ellena01"

# Repositories that are forks or course clones are excluded from the language
# mix, so the bar reflects code that was actually written here.
EXCLUDED_REPOS = {"build-your-own-x", "skills-introduction-to-github", "222", LOGIN}

# ---------------------------------------------------------------- palette ----
BG = "#10070C"
PANEL = "#150A11"
BORDER = "#451126"
WINE = "#701A3D"
ROSE = "#B34A76"
PINK = "#FF2A85"
TEXT = "#F8F0F3"
MUTED = "#C9D1D9"
FAINT = "#8C6B79"

SERIF = "Georgia, 'Times New Roman', Times, serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

LEVEL_COLORS = {
    "NONE": "#1B0E15",
    "FIRST_QUARTILE": "#451126",
    "SECOND_QUARTILE": "#701A3D",
    "THIRD_QUARTILE": "#B34A76",
    "FOURTH_QUARTILE": "#FF2A85",
}

LANG_COLORS = {
    "TypeScript": PINK,
    "Python": "#C2446F",
    "PLpgSQL": "#8E2E52",
    "CSS": "#61193A",
    "JavaScript": "#3E1027",
    "HTML": "#2A0A1A",
    "Dockerfile": "#2A0A1A",
    "Other": "#2A0A1A",
}


# ------------------------------------------------------------------ fetch ----
def gh(args: list[str]) -> str:
    """Run a `gh api` call and return stdout."""
    result = subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


CALENDAR_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    repositories(privacy: PUBLIC) { totalCount }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""


def fetch(login: str) -> dict:
    calendar_raw = gh([
        "graphql",
        "-f", f"query={CALENDAR_QUERY}",
        "-F", f"login={login}",
    ])
    calendar = json.loads(calendar_raw)["data"]["user"]

    repos_raw = json.loads(gh([f"users/{login}/repos?per_page=100&type=owner"]))
    languages: Counter[str] = Counter()
    counted: list[str] = []
    for repo in repos_raw:
        name = repo["name"]
        if repo.get("fork") or name in EXCLUDED_REPOS or repo.get("size", 0) == 0:
            continue
        try:
            langs = json.loads(gh([f"repos/{login}/{name}/languages"]))
        except RuntimeError:
            continue
        if langs:
            languages.update(langs)
            counted.append(name)

    prs = json.loads(gh([
        "-X", "GET", "search/issues",
        "-f", f"q=author:{login} type:pr",
        "-f", "per_page=1",
    ]))["total_count"]

    days: list[dict] = []
    for week in calendar["contributionsCollection"]["contributionCalendar"]["weeks"]:
        days.extend(week["contributionDays"])

    return {
        "login": login,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "joined": calendar["createdAt"],
        "public_repos": calendar["repositories"]["totalCount"],
        "pull_requests": prs,
        "total_contributions": calendar["contributionsCollection"]["contributionCalendar"][
            "totalContributions"
        ],
        "languages": dict(languages.most_common()),
        "language_repos": sorted(counted),
        "days": [
            {"d": d["date"], "c": d["contributionCount"], "l": d["contributionLevel"]}
            for d in days
        ],
    }


# ------------------------------------------------------------------ maths ----
def streaks(days: list[dict]) -> tuple[int, int, int, tuple[str, int] | None]:
    """current streak, longest streak, active days, busiest day"""
    current = 0
    for day in reversed(days):
        if day["c"] > 0:
            current += 1
        else:
            break
    longest = run = 0
    for day in days:
        run = run + 1 if day["c"] > 0 else 0
        longest = max(longest, run)
    active = sum(1 for day in days if day["c"] > 0)
    busiest = max(days, key=lambda d: d["c"]) if days else None
    best = (busiest["d"], busiest["c"]) if busiest and busiest["c"] else None
    return current, longest, active, best


def language_share(languages: dict, top: int = 5) -> list[tuple[str, float]]:
    total = sum(languages.values()) or 1
    ordered = sorted(languages.items(), key=lambda kv: -kv[1])
    shares = [(name, 100.0 * value / total) for name, value in ordered[:top]]
    rest = 100.0 - sum(share for _, share in shares)
    if rest > 0.05:
        shares.append(("Other", rest))
    return shares


def fmt_date(iso: str) -> str:
    return dt.date.fromisoformat(iso[:10]).strftime("%-d %b %Y")


# ------------------------------------------------------------------- svg ----
def svg_open(width: int, height: int, title: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{html.escape(title)}</title>",
        f"<desc id=\"d\">{html.escape(desc)}</desc>",
        "<defs>",
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#10070C"/>'
        f'<stop offset="0.6" stop-color="#170A12"/>'
        f'<stop offset="1" stop-color="#2A0F1C"/></linearGradient>',
        '<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{BORDER}"/>'
        f'<stop offset="0.5" stop-color="{ROSE}"/>'
        f'<stop offset="1" stop-color="{PINK}"/></linearGradient>',
        '<radialGradient id="glow" cx="0.5" cy="0" r="1">'
        f'<stop offset="0" stop-color="{PINK}" stop-opacity="0.18"/>'
        f'<stop offset="1" stop-color="{PINK}" stop-opacity="0"/></radialGradient>',
        "</defs>",
    ]


def card(x: float, y: float, w: float, h: float) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{PANEL}" '
        f'stroke="{BORDER}" stroke-width="1"/>'
    )


# --------------------------------------------------------------- metrics ----
def build_metrics(data: dict) -> str:
    days = data["days"]
    current, longest, active, best = streaks(days)
    since = dt.date.fromisoformat(data["joined"][:10]).year
    tiles = [
        (str(data["total_contributions"]), "CONTRIBUTIONS · LAST 12 MONTHS"),
        (str(active), "DAYS WITH COMMITS"),
        (str(data["public_repos"]), "PUBLIC REPOSITORIES"),
        (str(data["pull_requests"]), "PULL REQUESTS OPENED"),
    ]
    w, h = 900, 132
    pad, tile_w = 24, (900 - 48) / 4
    out = svg_open(
        w,
        h,
        "Helen Oyiza — GitHub activity metrics",
        f"{data['total_contributions']} contributions in the last 12 months, {active} active days, "
        f"{data['public_repos']} public repositories, {data['pull_requests']} pull requests.",
    )
    out.append(card(0.5, 0.5, w - 1, h - 1))
    out.append(f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="13" fill="url(#glow)"/>')

    for index, (value, label) in enumerate(tiles):
        cx = pad + tile_w * index + tile_w / 2
        out.append(
            f'<text x="{cx:.1f}" y="62" text-anchor="middle" font-family="{SERIF}" '
            f'font-size="40" font-weight="700" fill="{TEXT}">{value}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="86" text-anchor="middle" font-family="{MONO}" '
            f'font-size="11.5" letter-spacing="1.1" fill="{ROSE}">{html.escape(label)}</text>'
        )
        if index:
            x = pad + tile_w * index
            out.append(
                f'<line x1="{x:.1f}" y1="30" x2="{x:.1f}" y2="96" stroke="{BORDER}" '
                f'stroke-width="1" stroke-opacity="0.8"/>'
            )

    out.append(
        f'<line x1="{pad}" y1="104" x2="{w-pad}" y2="104" stroke="{BORDER}" stroke-opacity="0.7"/>'
    )
    stamp = (
        f"LIVE FROM THE GITHUB API · SNAPSHOT {fmt_date(data['generated_at'])} · "
        f"BUILDING IN PUBLIC SINCE {since}"
    )
    out.append(
        f'<text x="{w-pad}" y="120" text-anchor="end" font-family="{MONO}" font-size="10.5" '
        f'letter-spacing="1.1" fill="{FAINT}">{html.escape(stamp)}</text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


# -------------------------------------------------------------- activity ----
def build_activity(data: dict) -> str:
    days = data["days"]
    current, longest, active, best = streaks(days)

    cell, gap, pitch = 12, 3, 15
    left, top = 52, 62
    weeks = [days[i : i + 7] for i in range(0, len(days), 7)]
    w = 900
    grid_w = len(weeks) * pitch
    right = left + grid_w

    out = svg_open(
        w,
        320,
        "Helen Oyiza — contribution calendar and language distribution",
        f"Contribution heatmap for the last 12 months: {data['total_contributions']} contributions "
        f"across {active} days. Longest streak {longest} days. Language mix by bytes across "
        f"self-authored public repositories.",
    )
    out.append(card(0.5, 0.5, w - 1, 319))

    # header
    out.append(
        f'<text x="28" y="37" font-family="{MONO}" font-size="12" letter-spacing="3" '
        f'fill="{ROSE}">COMMIT LOG</text>'
    )
    out.append(
        f'<text x="{w-28}" y="37" text-anchor="end" font-family="{MONO}" font-size="11" '
        f'letter-spacing="1.4" fill="{MUTED}">{data["total_contributions"]} CONTRIBUTIONS · '
        f'LAST 12 MONTHS</text>'
    )
    out.append(f'<line x1="28" y1="48" x2="{w-28}" y2="48" stroke="{BORDER}" stroke-width="1"/>')

    # weekday labels
    for row, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(
            f'<text x="34" y="{top + row * pitch + cell - 2}" font-family="{MONO}" font-size="10" '
        f'fill="{FAINT}">{label}</text>'
        )

    # month labels
    previous_month = None
    for index, week in enumerate(weeks):
        month = dt.date.fromisoformat(week[0]["d"]).month
        if month != previous_month:
            label = dt.date.fromisoformat(week[0]["d"]).strftime("%b").upper()
            out.append(
                f'<text x="{left + index * pitch}" y="{top - 8}" font-family="{MONO}" '
                f'font-size="10" letter-spacing="0.8" fill="{FAINT}">{label}</text>'
            )
            previous_month = month

    # cells
    for index, week in enumerate(weeks):
        for row, day in enumerate(week):
            x = left + index * pitch
            y = top + row * pitch
            fill = LEVEL_COLORS.get(day["l"], LEVEL_COLORS["NONE"])
            tip = (
                f'<title>{day["d"]}: {day["c"]} contribution'
                f'{"s" if day["c"] != 1 else ""}</title>'
                if day["c"]
                else ""
            )
            out.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" '
                f'fill="{fill}">{tip}</rect>'
            )

    # legend
    legend_y = top + 7 * pitch + 26
    out.append(
        f'<text x="34" y="{legend_y}" font-family="{MONO}" font-size="10" letter-spacing="1" '
        f'fill="{FAINT}">LESS</text>'
    )
    for index, key in enumerate(
        ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
    ):
        out.append(
            f'<rect x="{70 + index * 16}" y="{legend_y - 9}" width="11" height="11" rx="2.5" '
            f'fill="{LEVEL_COLORS[key]}"/>'
        )
    out.append(
        f'<text x="{70 + 5 * 16 + 4}" y="{legend_y}" font-family="{MONO}" font-size="10" '
        f'letter-spacing="1" fill="{FAINT}">MORE</text>'
    )
    best_text = f" · BUSIEST DAY {best[1]} ({fmt_date(best[0])})" if best else ""
    out.append(
        f'<text x="{w-28}" y="{legend_y}" text-anchor="end" font-family="{MONO}" font-size="10" '
        f'letter-spacing="1" fill="{FAINT}">'
        f"{active} ACTIVE DAYS · LONGEST STREAK {longest} DAYS{best_text}</text>"
    )

    # language distribution
    shares = language_share(data["languages"])
    bar_y = legend_y + 24
    out.append(f'<line x1="28" y1="{bar_y - 14}" x2="{w-28}" y2="{bar_y - 14}" '
               f'stroke="{BORDER}" stroke-width="1"/>')
    out.append(
        f'<text x="28" y="{bar_y + 2}" font-family="{MONO}" font-size="10" letter-spacing="2" '
        f'fill="{ROSE}">LANGUAGE MIX · BYTES ACROSS {len(data["language_repos"])} '
        f'SELF-AUTHORED REPOSITORIES</text>'
    )
    bar_top = bar_y + 14
    bar_x = 28.0
    bar_width = w - 56
    for name, share in shares:
        seg = bar_width * share / 100.0
        out.append(
            f'<rect x="{bar_x:.2f}" y="{bar_top}" width="{max(seg, 1.2):.2f}" height="14" '
            f'rx="2" fill="{LANG_COLORS.get(name, LANG_COLORS["Other"])}">'
            f"<title>{html.escape(name)} {share:.1f}%</title></rect>"
        )
        bar_x += seg

    legend_x = 28.0
    for name, share in shares:
        label = f"{name} {share:.1f}%"
        width = 6.6 * len(label)
        out.append(
            f'<rect x="{legend_x:.1f}" y="{bar_top + 26}" width="8" height="8" rx="2" '
            f'fill="{LANG_COLORS.get(name, LANG_COLORS["Other"])}"/>'
        )
        out.append(
            f'<text x="{legend_x + 13:.1f}" y="{bar_top + 34}" font-family="{MONO}" '
            f'font-size="11" fill="{MUTED}">{html.escape(label)}</text>'
        )
        legend_x += width + 30

    out.append(
        f'<text x="{w-28}" y="300" text-anchor="end" font-family="{MONO}" '
        f'font-size="10" letter-spacing="1.2" fill="{FAINT}">'
        f"FORKS AND COURSE CLONES EXCLUDED · SNAPSHOT {html.escape(fmt_date(data['generated_at']))}"
        f"</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ main ----
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="pull fresh data from the GitHub API")
    parser.add_argument("--login", default=LOGIN)
    args = parser.parse_args()

    if args.fetch:
        data = fetch(args.login)
        DATA.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"fetched fresh activity data -> {SNAPSHOT.relative_to(ROOT)}")
    elif SNAPSHOT.exists():
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        print(f"rendering from snapshot {SNAPSHOT.relative_to(ROOT)}")
    else:
        print("no snapshot found and --fetch not passed", file=sys.stderr)
        return 1

    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "helen-metrics.svg").write_text(build_metrics(data), encoding="utf-8")
    (ASSETS / "helen-activity.svg").write_text(build_activity(data), encoding="utf-8")
    print("wrote assets/helen-metrics.svg and assets/helen-activity.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
