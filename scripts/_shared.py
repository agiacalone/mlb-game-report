"""Shared helpers and renderer for the mlb-game-report toolchain.

Used by ``mlb-fetch`` (writes the dataset CSVs) and ``mlb-render`` (reads them,
emits the Markdown report). The ``render_markdown`` function is the single
source of truth for Markdown output — ``mlb-report`` invokes it indirectly by
running fetch then render. Keep everything here stdlib-only.
"""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import date as _date, datetime
from pathlib import Path
from urllib.parse import quote_plus as urllib_quote
from zoneinfo import ZoneInfo

# --- Constants --------------------------------------------------------------

API = "https://statsapi.mlb.com/api/v1"
API11 = "https://statsapi.mlb.com/api/v1.1"

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th",
           6: "6th", 7: "7th", 8: "8th", 9: "9th", 10: "10th",
           11: "11th", 12: "12th"}
ORDINAL_WORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
                6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}

# Statcast fielding position codes (1=P through 9=RF)
POS = {1: "P", 2: "C", 3: "1B", 4: "2B", 5: "3B", 6: "SS", 7: "LF", 8: "CF", 9: "RF"}

# Scorebook tag → CSS category for traffic-light color in the rendered HTML.
# (The plain-text tag is preserved for the Markdown; the category is an HTML
# affordance for visual scanning. CSV analysis tools use the `event_tag`
# column directly.)
SB_CATEGORY = {
    # Outcomes in PBP and batting notes
    "HR":   "hr",
    "1B":   "hit",  "2B":  "hit",  "3B":  "hit",
    "BB":   "walk", "IBB": "walk", "HBP": "walk",
    "SB":   "hit",  "CS":  "out",
    "SF":   "sac",  "SH":  "sac",  "SF-DP": "sac", "SH-DP": "sac",
    "K":    "k",
    "\ua4d8": "k",  # ꓘ — called strikeout
    "Kc":   "k",    # legacy, safe
    "K-DP": "k",
    "E":    "err",
    "GIDP": "dp",   "DP":  "dp",  "TP": "dp",
    "GO":   "out",  "FO":  "out", "LO": "out", "PO": "out",
    "FC":   "fc",
    "CI":   "ci",   "BI":  "ci", "FI": "ci",
    # Pitching-notes label pills
    "WP":   "err",
    "PB":   "err",
    "Balk": "err",
    "Pickoffs": "out",
    "Ejections": "dp",
    "Disengagement violations": "err",
    "Pitch timer violations": "err",
    # Pitcher decisions
    "W":    "hit",   # win — green
    "L":    "k",     # loss — red
    "S":    "hr",    # save — gold
}


def pos_tag(pos: str) -> str:
    """Neutral-color position pill (HP/1B/2B/SS/LF/CF/…). Compact, monospace,
    team-agnostic — distinguishable from team badges at a glance."""
    if not pos:
        return ""
    return f'<span class="pos-tag">{pos}</span>'


def stat_label(name: str) -> str:
    """Neutral-color pill for stat abbreviations (IP, H, ER, BB, K, RBI, HR, …).
    Same shape as position pills so labels scan consistently across all blocks."""
    if not name:
        return ""
    return f'<span class="pos-tag">{name}</span>'


# Umpire labels we wrap with position pills when they appear at word boundaries.
_UMPIRE_POS_RE = re.compile(r"\b(HP|1B|2B|3B|LF|RF)\b")


def tag_umpires(s: str) -> str:
    """Wrap the position labels in an umpire string ("HP Muchlinski · 1B Morales · …")
    with position pills. The names/dots/separators stay as-is."""
    if not s:
        return s
    return _UMPIRE_POS_RE.sub(lambda m: pos_tag(m.group(1)), s)


def sb_tag_html(tag: str) -> str:
    """Render a scorebook tag as a colored pill for HTML. The Markdown still
    has the bare letters inside the span — it looks the same as inline code
    in plain-text viewers."""
    cat = SB_CATEGORY.get(tag, "")
    cls = f"sb-tag sb-{cat}" if cat else "sb-tag"
    return f'<span class="{cls}">{tag}</span>'


# Scorebook-style tags per plate-appearance outcome.
EVENT_TAG: dict[str, str] = {
    "Single": "1B",
    "Double": "2B",
    "Triple": "3B",
    "Home Run": "HR",
    "Walk": "BB",
    "Intent Walk": "IBB",
    "Strikeout": "K",
    "Strikeout - DP": "K-DP",
    "Strikeout Double Play": "K-DP",
    "Hit By Pitch": "HBP",
    "Sac Fly": "SF",
    "Sac Fly Double Play": "SF-DP",
    "Sac Bunt": "SH",
    "Sacrifice Bunt DP": "SH-DP",
    "Fielders Choice": "FC",
    "Fielders Choice Out": "FC",
    "Grounded Into DP": "GIDP",
    "Double Play": "DP",
    "Triple Play": "TP",
    "Field Error": "E",
    "Catcher Interference": "CI",
    "Batter Interference": "BI",
    "Fan Interference": "FI",
    "Groundout": "GO",
    "Flyout": "FO",
    "Lineout": "LO",
    "Pop Out": "PO",
    "Forceout": "FC",
    "Bunt Groundout": "GO",
    "Bunt Lineout": "LO",
    "Bunt Pop Out": "PO",
}

NOTABLE_KEYS = ["WP", "PB", "Balk", "HBP", "IBB", "SB", "CS", "E", "DP",
                "Disengagement violations", "Pitch timer violations",
                "Pickoffs", "Ejections"]

# MLB 3-letter abbreviation → Baseball-Reference code. Most match; these are
# the ones that differ. Used only to construct deep-links to BR boxscores —
# we do NOT fetch or scrape anything from BR.
BR_ABBR: dict[str, str] = {
    "LAA": "ANA",  # Angels in BR are "ANA" (Anaheim)
    "SD":  "SDP",
    "SF":  "SFG",
    "TB":  "TBR",
    "KC":  "KCR",
    "WSH": "WSN",
    "CWS": "CHW",
    "ATH": "OAK",
}


def br_box_url(home_abbr: str, date_iso: str, game_num: int = 0) -> str:
    """Build a Baseball-Reference boxscore URL for a game. Format:
    /boxes/<HOME>/<HOME>YYYYMMDD<gameNum>.shtml"""
    br = BR_ABBR.get(home_abbr, home_abbr)
    date_nodash = date_iso.replace("-", "")
    return f"https://www.baseball-reference.com/boxes/{br}/{br}{date_nodash}{game_num}.shtml"


def fangraphs_box_url(date_iso: str, away_short: str) -> str:
    """FanGraphs per-game boxscore URL. Pattern:
    https://www.fangraphs.com/boxscore.aspx?date=YYYY-MM-DD&team={AWAY}&dh=0&season=YYYY
    where {AWAY} is the visiting team's short name (Angels, Padres, Blue Jays, etc.)."""
    year = date_iso.split("-")[0]
    return (
        f"https://www.fangraphs.com/boxscore.aspx?date={date_iso}"
        f"&team={urllib_quote(away_short)}&dh=0&season={year}"
    )

# Notable-event keys that belong in the NOTES section (game-context only).
# Batting/pitching-specific items now render as their own notes blocks near
# the boxes, so we filter them out here.
NOTES_SECTION_KEYS = ["Disengagement violations", "Pitch timer violations", "Ejections"]

CSS_FILE = Path(__file__).resolve().parent / "newspaper.css"
LIBRARY = Path.home() / "games-attended"

# --- Small utilities --------------------------------------------------------

_SUFFIX_RE = re.compile(r"\b(Jr|Sr|II|III|IV)\.")


def fmt_date(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return _date(y, m, d).strftime("%B %-d, %Y")


def weekday(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return _date(y, m, d).strftime("%A")


def sun_event(lat: float, lon: float, d: _date, tz: ZoneInfo, zenith: float = 90.833,
              sunrise: bool = False) -> datetime | None:
    """NOAA sunrise/sunset on local date ``d``. zenith=90.833 is official sunset,
    96.0 is civil twilight end. Returns ``None`` at extreme latitudes."""
    N = d.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = N + (((6 if sunrise else 18) - lng_hour) / 24.0)
    M = (0.9856 * t) - 3.289
    L = M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634
    L = L % 360
    RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
    RA = RA % 360
    Lq = (L // 90) * 90
    RAq = (RA // 90) * 90
    RA = (RA + (Lq - RAq)) / 15.0
    sinDec = 0.39782 * math.sin(math.radians(L))
    cosDec = math.cos(math.asin(sinDec))
    cosH = (math.cos(math.radians(zenith)) - (sinDec * math.sin(math.radians(lat)))) / (cosDec * math.cos(math.radians(lat)))
    if cosH > 1 or cosH < -1:
        return None
    H = math.degrees(math.acos(cosH))
    if sunrise:
        H = 360 - H
    H = H / 15.0
    T = H + RA - (0.06571 * t) - 6.622
    UT = (T - lng_hour) % 24
    offset_hours = tz.utcoffset(datetime(d.year, d.month, d.day, 12)).total_seconds() / 3600
    local = (UT + offset_hours) % 24
    hh = int(local)
    mm = int(round((local - hh) * 60))
    if mm == 60:
        hh += 1
        mm = 0
    hh %= 24
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)


def parse_first_pitch(first_pitch_str: str, game_date_iso: str, tz: ZoneInfo) -> datetime | None:
    s = first_pitch_str.rstrip(". ").strip()
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            y, m, d = map(int, game_date_iso.split("-"))
            return datetime(y, m, d, t.hour, t.minute, tzinfo=tz)
        except ValueError:
            continue
    return None


def fmt_local_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_ip(ip) -> str:
    if ip is None or ip == "":
        return "0"
    s = str(ip)
    if "." in s:
        whole, frac = s.split(".", 1)
        return whole + {"0": "", "1": "⅓", "2": "⅔"}.get(frac[:1], "." + frac)
    return s


def ord_abbr(n: int) -> str:
    return ORDINAL.get(n, f"{n}th")


def ord_word(n: int) -> str:
    return ORDINAL_WORD.get(n, f"{n}th")


def short_name(full: str) -> str:
    parts = full.split()
    return parts[-1] if len(parts) > 1 else full


def last_name(full: str) -> str:
    parts = full.split()
    if len(parts) >= 2 and parts[-1] in ("Jr.", "Sr.", "II", "III", "IV"):
        return " ".join(parts[-2:])
    return parts[-1] if parts else full


def strip_period(s: str) -> str:
    return s.rstrip(". ").strip()


def first_sentence(s: str) -> str:
    s = s.strip()
    protected = _SUFFIX_RE.sub(lambda m: m.group(1) + "\u0000", s)
    m = re.match(r"^(.+?\.)(\s|$)", protected)
    out = m.group(1) if m else protected
    return out.replace("\u0000", ".")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# Stadium-relative wind directions → compass-like Unicode arrows.
# MLB's feed reports wind as "{speed} mph, {direction}" where direction is
# one of: "Out To RF", "Out To LF", "Out To CF", "In From RF", "In From LF",
# "In From CF", "L To R", "R To L", "Calm", "Varies", "None", etc.
# Convention: home plate is at the bottom of the field, outfield up top.
_WIND_ARROWS: list[tuple[str, str]] = [
    # Order matters — more specific (RF/LF/CF) before "out"/"in" general.
    ("out to rf", "\u2197"),  # ↗
    ("out to lf", "\u2196"),  # ↖
    ("out to cf", "\u2191"),  # ↑
    ("out to r",  "\u2197"),  # ↗
    ("out to l",  "\u2196"),  # ↖
    ("in from rf", "\u2199"), # ↙
    ("in from lf", "\u2198"), # ↘
    ("in from cf", "\u2193"), # ↓
    ("in from r",  "\u2199"), # ↙
    ("in from l",  "\u2198"), # ↘
    ("l to r",     "\u2192"), # →
    ("r to l",     "\u2190"), # ←
]


def wind_arrow(wind: str) -> str:
    """Return a wind string with the direction replaced by a Unicode arrow.
    E.g. "9 mph, Out To RF" → "9 mph ↗". Calm/Varies/None get no arrow."""
    if not wind:
        return wind
    low = wind.lower()
    for needle, arrow in _WIND_ARROWS:
        if needle in low:
            # Strip the direction phrase (preserve punctuation/spacing).
            # Match case-insensitively.
            pat = re.compile(re.escape(needle), re.IGNORECASE)
            stripped = pat.sub("", wind).rstrip(" ,").strip()
            return f"{stripped} {arrow}".strip()
    return wind  # Calm / Varies / no direction — leave as-is


# --- CSV helpers ------------------------------------------------------------

def _cast(v: str):
    """Best-effort string → scalar for CSV reads. Empty string stays as ''.
    Integer-looking values become int; everything else stays as-is (we preserve
    the original text for fractional stats like AVG .247 and ERA 1.80 so the
    rendered tables match the live-feed output byte-for-byte)."""
    if v == "":
        return ""
    if v in ("True", "False"):
        return v == "True"
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return v


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: _cast(v) for k, v in row.items()} for row in csv.DictReader(f)]


# --- Dataset schemas --------------------------------------------------------

GAME_FIELDS = [
    "gamePk", "date", "game_type", "status", "venue", "venue_id", "city", "state",
    "lat", "lon", "elevation", "azimuth", "tz", "dayNight", "first_pitch",
    "sunrise", "sunset", "civil_twilight_end", "duration", "attendance", "weather",
    "end_time",
    "wind", "umpires", "away_team", "away_team_id", "away_team_short", "away_abbr",
    "home_team", "home_team_id", "home_team_short", "home_abbr",
    "away_score", "home_score",
    "winning_pitcher", "losing_pitcher", "save_pitcher",
    "away_record", "home_record", "attended", "seat_section", "seat_row",
    "seat_number", "companions", "captivating_play_idx",
    # Additions beyond the spec, needed to render without the raw feed:
    "notable_events_json",
]
LINESCORE_FIELDS = ["inning", "away_runs", "home_runs"]
BATTING_FIELDS = ["team", "order", "name", "player_id", "position",
                  "ab", "r", "h", "rbi", "bb", "k", "hr", "avg_season",
                  "hr_season", "is_starter"]
PITCHING_FIELDS = ["team", "order", "name", "player_id", "decision",
                   "ip", "h", "r", "er", "bb", "k", "hr", "era_season", "pitches"]
PLAY_FIELDS = ["idx", "inning", "half", "batter", "batter_id", "pitcher",
               "pitcher_id", "event", "event_tag", "description", "balls",
               "strikes", "pitch_count", "away_score_after", "home_score_after",
               "is_scoring_play", "captivating_index", "rbi",
               # Per-play wall-clock (ISO 8601 UTC) — for precise broadcast
               # timestamps without linear approximation.
               "start_time_utc", "end_time_utc",
               # Win-probability fields from /winProbability endpoint.
               # wp_home/wp_away are AFTER this play; wpa_home is the
               # change contributed by this play for the home side
               # (negative = play helped away). leverage_index is the
               # Tom Tango LI at the start of the plate appearance.
               "wp_home", "wp_away", "wpa_home", "leverage_index"]
PITCH_FIELDS = ["play_idx", "pitch_num", "type_code", "type_desc", "speed_mph",
                "spin_rpm", "call", "px", "pz", "ev_mph", "la_deg",
                "distance_ft", "trajectory", "hit_location", "hardness"]


def write_dataset(dirpath: Path, dataset: dict) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    write_csv(dirpath / "game.csv", GAME_FIELDS, [dataset["game"]])
    write_csv(dirpath / "linescore.csv", LINESCORE_FIELDS, dataset["linescore"])
    write_csv(dirpath / "batting.csv", BATTING_FIELDS, dataset["batting"])
    write_csv(dirpath / "pitching.csv", PITCHING_FIELDS, dataset["pitching"])
    write_csv(dirpath / "plays.csv", PLAY_FIELDS, dataset["plays"])
    if dataset.get("pitches"):
        write_csv(dirpath / "pitches.csv", PITCH_FIELDS, dataset["pitches"])


def read_dataset(dirpath: Path) -> dict:
    game_rows = read_csv(dirpath / "game.csv")
    if not game_rows:
        raise SystemExit(f"No game.csv in {dirpath}")
    return {
        "game": game_rows[0],
        "linescore": read_csv(dirpath / "linescore.csv"),
        "batting": read_csv(dirpath / "batting.csv"),
        "pitching": read_csv(dirpath / "pitching.csv"),
        "plays": read_csv(dirpath / "plays.csv"),
        "pitches": read_csv(dirpath / "pitches.csv"),
        "_dir": dirpath,
    }


# --- Statcast formatting ----------------------------------------------------

def statcast_line_for_play(play_idx: int, pitches: list[dict]) -> str | None:
    """Format the one-line Statcast annotation for a play from its pitch rows."""
    play_pitches = [p for p in pitches if p.get("play_idx") == play_idx]
    if not play_pitches:
        return None
    last_pitch = play_pitches[-1]
    hit_rows = [p for p in play_pitches if p.get("ev_mph") not in (None, "") or p.get("trajectory") or p.get("distance_ft") not in (None, "") or p.get("hardness")]
    hit = hit_rows[-1] if hit_rows else None

    bits: list[str] = []
    ptype = last_pitch.get("type_desc") or last_pitch.get("type_code")
    speed = last_pitch.get("speed_mph")
    spin = last_pitch.get("spin_rpm")
    if ptype and speed not in (None, ""):
        s = f"{ptype} {float(speed):.1f} mph"
        if spin not in (None, ""):
            s += f", {int(float(spin))} rpm"
        bits.append(s)
    elif speed not in (None, ""):
        bits.append(f"{float(speed):.1f} mph")

    if hit:
        ls = hit.get("ev_mph")
        la = hit.get("la_deg")
        dist = hit.get("distance_ft")
        traj = hit.get("trajectory")
        loc = hit.get("hit_location")
        hard = hit.get("hardness")
        parts: list[str] = []
        numeric_present = any(v not in (None, "") for v in (ls, la, dist))
        if ls not in (None, ""):
            parts.append(f"EV {float(ls):.1f} mph")
        if la not in (None, ""):
            parts.append(f"LA {float(la):.0f}°")
        if dist not in (None, ""):
            parts.append(f"{int(float(dist))} ft")
        if hard and (ls in (None, "")):
            parts.append(f"{str(hard).lower()}-hit")
        if traj:
            parts.append(str(traj).replace("_", " "))
        if loc not in (None, ""):
            try:
                parts.append(f"to {POS[int(float(loc))]}")
            except (ValueError, KeyError):
                parts.append(f"to {loc}")
        if parts:
            label = "Batted" if numeric_present else "Contact"
            bits.append(f"{label}: " + ", ".join(parts))

    return " · ".join(bits) if bits else None


# --- Renderer ---------------------------------------------------------------

# --- Compact-pitch-type helper (drops trailing "Fastball") -----------------

def _compact_ptype(ptype: str) -> str:
    if not ptype:
        return ptype
    s = str(ptype)
    # "Four-Seam Fastball" → "Four-Seam"; "Cutter Fastball" → "Cutter".
    # Keep "Splitter", "Slider", etc. untouched.
    if s.endswith(" Fastball"):
        return s[: -len(" Fastball")]
    return s


def statcast_line_compact(play_idx: int, pitches: list[dict]) -> str | None:
    """Dense one-segment Statcast annotation for inline PBP use.

    Differences from :func:`statcast_line_for_play`:
    - Strips the "Fastball" suffix from common pitch types
    - Drops the "mph" unit from EV (still shown on pitch speed)
    - Drops the "Batted:"/"Contact:" prefix
    - Drops "to " before the fielder position
    """
    play_pitches = [p for p in pitches if p.get("play_idx") == play_idx]
    if not play_pitches:
        return None
    last_pitch = play_pitches[-1]
    hit_rows = [p for p in play_pitches if p.get("ev_mph") not in (None, "") or p.get("trajectory") or p.get("distance_ft") not in (None, "") or p.get("hardness")]
    hit = hit_rows[-1] if hit_rows else None

    bits: list[str] = []
    ptype = _compact_ptype(last_pitch.get("type_desc") or last_pitch.get("type_code") or "")
    speed = last_pitch.get("speed_mph")
    spin = last_pitch.get("spin_rpm")
    if ptype and speed not in (None, ""):
        s = f"{ptype} {float(speed):.1f} mph"
        if spin not in (None, ""):
            s += f", {int(float(spin))} rpm"
        bits.append(s)
    elif speed not in (None, ""):
        bits.append(f"{float(speed):.1f} mph")

    if hit:
        ls = hit.get("ev_mph")
        la = hit.get("la_deg")
        dist = hit.get("distance_ft")
        traj = hit.get("trajectory")
        loc = hit.get("hit_location")
        hard = hit.get("hardness")
        parts: list[str] = []
        numeric_present = any(v not in (None, "") for v in (ls, la, dist))
        if ls not in (None, ""):
            parts.append(f"EV {float(ls):.1f}")
        if la not in (None, ""):
            parts.append(f"LA {float(la):.0f}°")
        if dist not in (None, ""):
            parts.append(f"{int(float(dist))} ft")
        if traj:
            parts.append(str(traj).replace("_", " "))
        if loc not in (None, ""):
            try:
                parts.append(f"{POS[int(float(loc))]}")
            except (ValueError, KeyError):
                parts.append(f"{loc}")
        if not numeric_present and hard:
            # Historical fallback: "medium-hit ground ball, 1B"
            prefix = f"{str(hard).lower()}-hit"
            # Prepend hardness to the first non-numeric bit (usually trajectory).
            parts = [prefix + (" " + parts[0] if parts else "")] + parts[1:]
        if parts:
            bits.append(", ".join(parts))

    return " · ".join(bits) if bits else None


# --- Weather + wind glyphs --------------------------------------------------

def weather_glyph(weather: str) -> str:
    w = (weather or "").lower()
    # Order matters: check dome/thunder/snow before generic cloud/rain.
    if "dome" in w or "roof closed" in w:
        return "\u26e8"  # ⛨
    if "thunder" in w or "storm" in w:
        return "\u26c8"  # ⛈
    if "snow" in w:
        return "\u2744"  # ❄
    if "drizzle" in w:
        return "\U0001f326"  # 🌦
    if "rain" in w or "shower" in w:
        return "\U0001f327"  # 🌧
    if "fog" in w or "mist" in w:
        return "\U0001f32b"  # 🌫
    if "partly cloudy" in w:
        return "\u26c5"  # ⛅
    if "cloud" in w or "overcast" in w:
        return "\u2601"  # ☁
    if "clear" in w or "sunny" in w or "fair" in w:
        return "\u2600"  # ☀
    return "\u2022"  # •


def _top_batters(batting: list[dict], team_short: str, n: int = 3) -> list[dict]:
    rows = [b for b in batting if b.get("team") == team_short
            and b.get("ab") not in (None, "") and int(b.get("ab") or 0) > 0]
    rows.sort(key=lambda r: (int(r.get("h") or 0), int(r.get("rbi") or 0), int(r.get("hr") or 0)),
              reverse=True)
    return rows[:n]


def render_markdown(dataset: dict) -> tuple[str, dict]:
    g = dataset["game"]
    linescore = dataset["linescore"]
    batting = dataset["batting"]
    pitching = dataset["pitching"]
    plays = dataset["plays"]
    pitches = dataset["pitches"]

    away_name = g["away_team"]
    home_name = g["home_team"]
    away_short = g["away_team_short"]
    home_short = g["home_team_short"]
    away_abbr = g.get("away_abbr") or ""
    home_abbr = g.get("home_abbr") or ""
    # Team pill badges used across the document (AT A GLANCE, top performers,
    # box scores, NOTES lineups). Defined up here so any rendering helper
    # below can reference them.
    away_pill = f'<span class="team-badge team-{away_abbr}">{away_abbr}</span>' if away_abbr else away_short
    home_pill = f'<span class="team-badge team-{home_abbr}">{home_abbr}</span>' if home_abbr else home_short
    away_r = int(g["away_score"] or 0)
    home_r = int(g["home_score"] or 0)
    winner_name = home_name if home_r > away_r else away_name
    loser_name = away_name if home_r > away_r else home_name
    w_score, l_score = (home_r, away_r) if home_r > away_r else (away_r, home_r)
    winner_short = home_short if home_r > away_r else away_short
    loser_short = away_short if home_r > away_r else home_short
    at_home = home_r > away_r

    wp = g.get("winning_pitcher") or ""
    lp = g.get("losing_pitcher") or ""
    sv = g.get("save_pitcher") or ""

    # Headline verb varies by margin to avoid repetition across a series.
    # Score is always away-home (baseball convention). Verb is picked
    # deterministically from gamePk hash so the same game always gets the
    # same verb (no surprises on re-render).
    margin = w_score - l_score
    SHUTOUT_VERBS = ["BLANK", "SHUT OUT", "WHITEWASH", "SILENCE", "SMOTHER"]
    CLOSE_VERBS = ["EDGE", "NIP", "CLIP", "TOP", "SLIP PAST", "OUTLAST"]              # 1–2 runs
    SOLID_VERBS = ["TOP", "BEAT", "DOWNS", "BEST", "HANDLE", "DEFEAT", "OUTSCORE"]    # 3–5 runs
    ROUT_VERBS  = ["ROUT", "MAUL", "POUND", "HAMMER", "CRUSH", "TROUNCE",
                   "BATTER", "BULLY", "LEVEL", "SHELLAC"]                             # 6+ runs
    if l_score == 0:
        pool = SHUTOUT_VERBS
    elif margin <= 2:
        pool = CLOSE_VERBS
    elif margin <= 5:
        pool = SOLID_VERBS
    else:
        pool = ROUT_VERBS
    try:
        seed = int(g.get("gamePk") or 0)
    except (TypeError, ValueError):
        seed = 0
    verb = pool[seed % len(pool)]
    headline = f"{winner_short.upper()} {verb} {loser_short.upper()}, {away_r}-{home_r}"

    w_side_short = winner_short
    tops = _top_batters(batting, w_side_short, 1)
    subhead_bits: list[str] = []
    if tops:
        t = tops[0]
        bit = f"{last_name(t['name'])} {t.get('h',0)}-for-{t.get('ab',0)}"
        if int(t.get("hr") or 0):
            bit += f", {t['hr']} HR"
        if int(t.get("rbi") or 0):
            bit += f", {t['rbi']} RBI"
        subhead_bits.append(bit)

    # Winning starter = first pitcher (order=1) of winning team
    w_pitchers = sorted([p for p in pitching if p.get("team") == winner_short],
                        key=lambda p: int(p.get("order") or 999))
    starter = w_pitchers[0] if w_pitchers else None
    if starter and starter["name"] == wp:
        subhead_bits.append(f"{last_name(wp)} fans {starter.get('k',0)} over {fmt_ip(starter.get('ip'))} IP")
    subhead = "### " + " · ".join(subhead_bits) if subhead_bits else ""

    venue = g.get("venue") or ""
    dateline = f"**{venue.upper() if venue else 'MLB'}, {fmt_date(g['date'])}.**"

    # --- AP lede ---
    verb = "blanked" if l_score == 0 else "defeated"
    day = weekday(g["date"])
    clauses: list[str] = []
    if tops:
        t = tops[0]
        hit_clause = f"{last_name(t['name'])} went {t.get('h',0)} for {t.get('ab',0)}"
        extras: list[str] = []
        if int(t.get("hr") or 0):
            extras.append("a home run")
        if int(t.get("rbi") or 0):
            extras.append(f"{t['rbi']} RBI")
        if extras:
            hit_clause += " with " + " and ".join(extras)
        clauses.append(hit_clause)
    if wp and starter and starter["name"] == wp:
        ip = fmt_ip(starter.get("ip"))
        runs = int(starter.get("r") or 0)
        ks = int(starter.get("k") or 0)
        if runs == 0:
            clauses.append(f"{last_name(wp)} tossed {ip} scoreless innings with {ks} strikeouts")
        else:
            clauses.append(f"{last_name(wp)} worked {ip} innings, striking out {ks}")
    who = " and ".join(clauses) if clauses else ""
    body = f"the {winner_name} {verb} the {loser_name}, {w_score}–{l_score}, on {day}"
    lede_sentence = f"{who} as {body}." if who else f"The {winner_name} {verb} the {loser_name}, {w_score}–{l_score}, on {day}."
    lede_para = f"{dateline} {lede_sentence}"

    # --- Takeaways ---
    bullets: list[str] = []
    # Pitching
    pit_parts: list[str] = []
    if wp:
        if starter and starter["name"] == wp:
            ip = fmt_ip(starter.get("ip"))
            line_ = (f"**{last_name(wp)} (W)** — {ip} IP, {starter.get('h',0)} H, "
                     f"{starter.get('r',0)} R, {starter.get('er',0)} ER, "
                     f"{starter.get('bb',0)} BB, {starter.get('k',0)} K")
            if starter.get("era_season") not in (None, "", "—"):
                line_ += f" · season ERA {starter['era_season']}"
            pit_parts.append(line_)
        else:
            pit_parts.append(f"**{last_name(wp)} (W)** in relief")
    if sv:
        pit_parts.append(f"{last_name(sv)} (S)")
    if lp:
        lp_row = next((p for p in pitching if p["name"] == lp), None)
        if lp_row:
            ip = fmt_ip(lp_row.get("ip"))
            pit_parts.append(f"**{last_name(lp)} (L)** — {ip} IP, {lp_row.get('er',0)} ER")
        else:
            pit_parts.append(f"**{last_name(lp)} (L)**")
    if pit_parts:
        bullets.append("**Pitching.** " + " · ".join(pit_parts) + ".")

    # Offense: HRs from plays (season-HR tag from batting.csv)
    batter_season_hr = {int(b["player_id"]): b.get("hr_season")
                        for b in batting if b.get("player_id") not in (None, "")}
    hrs: list[str] = []
    for pl in plays:
        if pl.get("event") == "Home Run":
            batter = last_name(pl["batter"])
            half = pl["half"]
            team = away_short if half == "top" else home_short
            bid = pl.get("batter_id")
            season_hr = batter_season_hr.get(int(bid)) if bid not in (None, "") else None
            tag = f"({season_hr})" if season_hr not in (None, "") else ""
            hrs.append(f"{batter} {tag}, {team}".strip())
    off_parts: list[str] = []
    if hrs:
        off_parts.append("HR: " + "; ".join(hrs))
    w_top = _top_batters(batting, winner_short, 2)
    hot = []
    for t in w_top:
        h = int(t.get("h") or 0)
        rbi = int(t.get("rbi") or 0)
        if h >= 2 or rbi >= 1:
            s = f"{last_name(t['name'])} {h}-for-{t.get('ab',0)}"
            if rbi:
                s += f", {rbi} RBI"
            hot.append(s)
    if hot:
        off_parts.append(f"{winner_short}: " + "; ".join(hot))
    l_top = _top_batters(batting, loser_short, 1)
    if l_top:
        t = l_top[0]
        h = int(t.get("h") or 0)
        rbi = int(t.get("rbi") or 0)
        if h >= 2 or rbi >= 1:
            off_parts.append(f"{loser_short}: {last_name(t['name'])} {h}-for-{t.get('ab',0)}")
    if off_parts:
        bullets.append("**Offense.** " + " · ".join(off_parts) + ".")

    # Turning point: biggest scoring inning for winner
    big_inn = None
    big_runs = 0
    for inn in linescore:
        n = inn.get("inning")
        if n in ("R", "H", "E"):
            continue
        r = int((inn.get("home_runs") if at_home else inn.get("away_runs")) or 0)
        if r > big_runs:
            big_runs = r
            big_inn = int(n)
    if big_inn and big_runs >= 2:
        bullets.append(f"**Turning point.** {winner_short} plated **{big_runs} in the {ord_abbr(big_inn)}** to break the game open.")
    elif big_inn:
        bullets.append(f"**Turning point.** {winner_short}'s decisive run came in the {ord_abbr(big_inn)}.")

    takeaways_block = "\n".join(f"- {b}" for b in bullets) if bullets else "_—_"

    # --- Top Performers (dual-column, below AT A GLANCE / LINE SCORE) ---
    def _top_performers(team_short: str) -> list[str]:
        """Pick 2 top batters (by hits/RBI/HR) + the team's pitcher of record."""
        lines: list[str] = []
        # Pitcher of record from this team (WP, LP, or SV).
        decision_pitcher = None
        for p in pitching:
            if p.get("team") != team_short:
                continue
            dec = p.get("decision") or ""
            if dec in ("W", "L", "S"):
                decision_pitcher = p
                break
        if decision_pitcher:
            nm = last_name(decision_pitcher.get("name") or "")
            pid = decision_pitcher.get("player_id") or ""
            nm_linked = f'[**{nm}**](#pit-{pid})' if pid else f"**{nm}**"
            dec = decision_pitcher.get("decision") or ""
            ip = fmt_ip(decision_pitcher.get("ip"))
            h = decision_pitcher.get("h", 0)
            er = decision_pitcher.get("er", 0)
            k = decision_pitcher.get("k", 0)
            bb = decision_pitcher.get("bb", 0)
            lines.append(
                f"- {nm_linked} {sb_tag_html(dec)} — "
                f"{ip} {stat_label('IP')}, "
                f"{h} {stat_label('H')}, "
                f"{er} {stat_label('ER')}, "
                f"{bb} {stat_label('BB')}, "
                f"{k} {stat_label('K')}"
            )
        # Top 2 batters, but only include those with meaningful contributions.
        hitters = _top_batters(batting, team_short, 3)
        shown = 0
        for b in hitters:
            if shown >= 2:
                break
            h = int(b.get("h") or 0)
            rbi = int(b.get("rbi") or 0)
            hr = int(b.get("hr") or 0)
            if h == 0 and rbi == 0:
                continue
            nm = last_name(b.get("name") or "")
            pid = b.get("player_id") or ""
            nm_linked = f'[**{nm}**](#bat-{pid})' if pid else f"**{nm}**"
            pos = b.get("position") or ""
            ab = int(b.get("ab") or 0)
            bits = [f"{h}-for-{ab}"]
            if hr: bits.append(f"{hr} {stat_label('HR')}")
            if rbi: bits.append(f"{rbi} {stat_label('RBI')}")
            line_ = f"- {nm_linked} {pos_tag(pos)} — " + ", ".join(bits)
            lines.append(line_)
            shown += 1
        return lines

    away_perf = _top_performers(away_short)
    home_perf = _top_performers(home_short)
    performers_block = (
        '<div class="twocol perf">\n\n'
        f'<section class="team-{away_abbr}">\n\n'
        f"**{away_pill} Top performers**\n\n"
        + ("\n".join(away_perf) if away_perf else "_—_")
        + "\n\n</section>\n\n"
        f'<section class="team-{home_abbr}">\n\n'
        f"**{home_pill} Top performers**\n\n"
        + ("\n".join(home_perf) if home_perf else "_—_")
        + "\n\n</section>\n\n"
        "</div>"
    )

    # --- Scoring (compact table; each inning cell links to the PBP entry below) ---
    scoring_rows: list[str] = []
    for pl in plays:
        if not pl.get("is_scoring_play"):
            continue
        inn = int(pl["inning"])
        pill_for_row = away_pill if pl["half"] == "top" else home_pill
        desc = first_sentence((pl.get("description") or "").strip()).replace("\n", " ").rstrip(". ")
        a = int(pl.get("away_score_after") or 0)
        h = int(pl.get("home_score_after") or 0)
        glyph = "⚾" if pl.get("event") == "Home Run" else "·"
        glyph_span = f'<span class="sc-glyph">{glyph}</span>'
        # Anchor link: each PBP list item gets id="play-N"; clicking the
        # inning cell jumps to the matching play below. Team pill replaces
        # the plain three-letter abbreviation.
        play_idx = pl.get("idx")
        label = f"{glyph_span} {pill_for_row} {ord_abbr(inn)}"
        inning_cell = f'<a href="#play-{play_idx}">{label}</a>' if play_idx is not None else label
        scoring_rows.append(f"| {inning_cell} | {desc}. | {a}\u2013{h} |")
    if scoring_rows:
        hdr = f"| Inn | Play | Score ({away_short}\u2013{home_short}) |"
        sep = "|-----|------|-------------|"
        scoring_block = "\n".join([hdr, sep] + scoring_rows)
    else:
        scoring_block = "_No scoring plays._"

    # --- Win Probability chart + Top 5 Plays (wWPA/wWE from feed) ---
    wp_plays = [pl for pl in plays if str(pl.get("wp_home") or "") != ""]
    if wp_plays:
        # SVG step chart. X = plays in order (uniform spacing); Y = home WP (0-100).
        # Red region (home trailing in odds) sits above the line; gray below.
        # Axis labels match the Baseball-Reference convention: away team on top.
        W, H = 760, 180
        pad_l, pad_r, pad_t, pad_b = 36, 10, 14, 22
        n = len(wp_plays)
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b
        step = plot_w / max(n, 1)
        # Build polyline points (step chart)
        points: list[tuple[float, float]] = []
        for i, pl in enumerate(wp_plays):
            try:
                wph = float(pl.get("wp_home") or 0.0)
            except ValueError:
                wph = 0.0
            x0 = pad_l + i * step
            x1 = pad_l + (i + 1) * step
            # y increases downward. Top of plot = away (SDP) at 100%;
            # bottom = home (LAA) at 100%. So y = home_wp share.
            y = pad_t + (wph / 100.0) * plot_h
            points.append((x0, y))
            points.append((x1, y))
        # Build fill path (area under line for home team)
        area = f"M{pad_l},{pad_t + plot_h} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in points) + f" L{pad_l + n*step:.1f},{pad_t + plot_h} Z"
        line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
        # Inning tick marks along x-axis
        ticks: list[str] = []
        seen_half: set[tuple[int, str]] = set()
        for i, pl in enumerate(wp_plays):
            key = (int(pl["inning"]), pl["half"])
            if key in seen_half:
                continue
            seen_half.add(key)
            xt = pad_l + i * step
            letter = "t" if pl["half"] == "top" else "b"
            lbl = f"{letter}{pl['inning']}"
            ticks.append(
                f'<line x1="{xt:.1f}" y1="{pad_t}" x2="{xt:.1f}" y2="{pad_t + plot_h}" class="wp-tick"/>'
                f'<text x="{xt:.1f}" y="{H - 6}" class="wp-tick-lbl">{lbl}</text>'
            )
        # Y labels (team names at top/bottom, 50% middle)
        y50 = pad_t + plot_h / 2
        wp_chart_svg = (
            f'<svg class="wp-chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
            f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="Win probability by play">'
            f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" class="wp-bg"/>'
            f'<path d="{area}" class="wp-area"/>'
            f'<line x1="{pad_l}" y1="{y50}" x2="{pad_l + plot_w}" y2="{y50}" class="wp-mid"/>'
            f'<path d="{line}" class="wp-line"/>'
            f'<text x="{pad_l - 6}" y="{pad_t + 10}" class="wp-axis">{away_short}</text>'
            f'<text x="{pad_l - 6}" y="{y50 + 4}" class="wp-axis">50%</text>'
            f'<text x="{pad_l - 6}" y="{pad_t + plot_h}" class="wp-axis">{home_short}</text>'
            + "".join(ticks)
            + "</svg>"
        )
        # Top 5 plays by |wpa_home|
        def _wpa(pl: dict) -> float:
            try:
                return abs(float(pl.get("wpa_home") or 0.0))
            except ValueError:
                return 0.0
        ranked = sorted(wp_plays, key=_wpa, reverse=True)[:5]
        top_rows: list[str] = []
        for pl in ranked:
            inn = int(pl["inning"])
            inn_code = f"{'t' if pl['half']=='top' else 'b'}{inn}"
            bat_abbr = away_abbr if pl["half"] == "top" else home_abbr
            bat_pill = away_pill if pl["half"] == "top" else home_pill
            inn_pill = (
                f'<span class="team-badge team-{bat_abbr}">{inn_code}</span>'
                if bat_abbr else inn_code
            )
            batter = last_name(pl.get("batter") or "")
            pitcher = last_name(pl.get("pitcher") or "")
            desc = first_sentence((pl.get("description") or "").strip()).rstrip(". ")
            try:
                wpa = float(pl.get("wpa_home") or 0.0)
            except ValueError:
                wpa = 0.0
            try:
                wwe = float(pl.get("wp_home") or 0.0)
            except ValueError:
                wwe = 0.0
            a = int(pl.get("away_score_after") or 0)
            h = int(pl.get("home_score_after") or 0)
            idx_anchor = pl.get("idx")
            inn_cell = f'<a href="#play-{idx_anchor}">{inn_pill}</a>' if idx_anchor is not None else inn_pill
            top_rows.append(
                f"| {inn_cell} | {a} – {h} | {batter} | {pitcher} | {abs(wpa):.0f}% | {wwe:.0f}% | {desc}. |"
            )
        top5_hdr = "| Inn | Score | Batter | Pitcher | wWPA | wWE | Play |"
        top5_sep = "|-----|-------|--------|---------|------|-----|------|"
        top5_table = "\n".join([top5_hdr, top5_sep] + top_rows)
        wp_block = (
            f'<div class="wp-chart-wrap">{wp_chart_svg}</div>\n\n'
            f'*Height of bar indicates home-team win probability; red band = away team leading odds.*\n\n'
            f'### Top 5 Plays by Win Probability Added\n\n{top5_table}'
        )
    else:
        wp_block = ""

    # --- Line score ---
    inning_rows = [i for i in linescore if i.get("inning") not in ("R", "H", "E")]
    max_inn = max(9, len(inning_rows))
    header = "| Team | " + " | ".join(str(i) for i in range(1, max_inn + 1)) + " | R | H | E |"
    sep = "|------|" + "|".join(["---"] * max_inn) + "|---|---|---|"

    def _totals(side: str) -> tuple[int, int, int]:
        r = next((row for row in linescore if row.get("inning") == "R"), {})
        h = next((row for row in linescore if row.get("inning") == "H"), {})
        e = next((row for row in linescore if row.get("inning") == "E"), {})
        key = f"{side}_runs"
        return (int(r.get(key) or 0), int(h.get(key) or 0), int(e.get(key) or 0))

    def _line_row(team_name: str, side: str) -> str:
        cells = []
        for i in range(1, max_inn + 1):
            inn = next((x for x in inning_rows if int(x["inning"]) == i), None)
            if inn is None:
                cells.append("–")
            else:
                v = inn.get(f"{side}_runs")
                cells.append(str(v) if v not in (None, "") else "–")
        r, h, e = _totals(side)
        return f"| {team_name} | " + " | ".join(cells) + f" | **{r}** | {h} | {e} |"

    line_table = "\n".join([header, sep, _line_row(away_name, "away"), _line_row(home_name, "home")])

    # --- Decisions block (sits under the line score to use the leftover column space) ---
    decision_lines: list[str] = []
    label_for = {"W": "Win", "L": "Loss", "S": "Save"}
    for dec in ("W", "L", "S"):
        p = next((x for x in pitching if (x.get("decision") or "") == dec), None)
        if not p:
            continue
        nm = last_name(p.get("name") or "")
        pid = p.get("player_id") or ""
        nm_html = f'<a href="#pit-{pid}"><strong>{nm}</strong></a>' if pid else f"<strong>{nm}</strong>"
        team = p.get("team") or ""
        team_abbr = away_abbr if team == away_short else (home_abbr if team == home_short else "")
        team_pill_html = (
            f'<span class="team-badge team-{team_abbr}">{team_abbr}</span>' if team_abbr else team
        )
        ip = fmt_ip(p.get("ip"))
        h = p.get("h", 0); er = p.get("er", 0); bb = p.get("bb", 0); k = p.get("k", 0)
        era = p.get("era_season") or ""
        era_bit = f", {era} ERA" if era not in ("", "-.--") else ""
        decision_lines.append(
            f'<li><span class="dec-tag sb-tag sb-{dec.lower()}">{label_for[dec]}</span> '
            f"{team_pill_html} {nm_html} — "
            f"{ip} IP, {h} H, {er} ER, {bb} BB, {k} K{era_bit}</li>"
        )
    decisions_block = (
        f'<ul class="decisions">{"".join(decision_lines)}</ul>' if decision_lines else ""
    )

    # --- Team records line (bottom-filler under the decisions block) ---
    away_rec = (g.get("away_record") or "").strip()
    home_rec = (g.get("home_record") or "").strip()
    if away_rec or home_rec:
        records_line = (
            f'<p class="records">{away_pill} {away_name} <strong>{away_rec}</strong> · '
            f'{home_pill} {home_name} <strong>{home_rec}</strong></p>'
        )
    else:
        records_line = ""

    # --- Play-by-play game log ---
    inning_order: list[tuple[int, str]] = []
    by_half: dict[tuple[int, str], list[dict]] = {}
    for pl in plays:
        inn = int(pl["inning"])
        half = pl["half"]
        key = (inn, half)
        if key not in by_half:
            by_half[key] = []
            inning_order.append(key)
        if (pl.get("description") or "").strip():
            by_half[key].append(pl)

    log_sections: list[str] = []
    for (inn, half) in inning_order:
        half_plays = by_half.get((inn, half), [])
        if not half_plays:
            continue
        bat_team = away_short if half == "top" else home_short
        pit_team = home_short if half == "top" else away_short
        inning_glyph = "▲" if half == "top" else "▼"
        header_line = f"### {inning_glyph} {half.capitalize()} {ord_abbr(inn)} — {bat_team} batting · {pit_team} pitching"
        items: list[str] = []
        last_pitcher: str | None = None
        for i, pl in enumerate(half_plays, 1):
            batter = last_name(pl["batter"])
            pitcher = last_name(pl["pitcher"])
            event = (pl.get("event") or "").strip() or "Play"
            desc = (pl.get("description") or "").strip().replace("\n", " ")
            pitch_count = int(pl.get("pitch_count") or 0)
            b = int(pl.get("balls") or 0)
            s = int(pl.get("strikes") or 0)
            meta_bits = [f"count {b}-{s}"]
            if pitch_count:
                meta_bits.append(f"{pitch_count} pitch{'es' if pitch_count != 1 else ''}")
            scored = bool(pl.get("is_scoring_play"))
            a = int(pl.get("away_score_after") or 0)
            h = int(pl.get("home_score_after") or 0)

            pitcher_changed = bool(last_pitcher) and pitcher != last_pitcher
            if pitcher_changed:
                items.append(
                    f'    <span class="pitching-change">🔄 <strong>Pitching change:</strong> '
                    f'{last_pitcher} → {pitcher}</span>'
                )
            first_pa_for_pitcher = (last_pitcher is None) or pitcher_changed
            last_pitcher = pitcher

            event_marker = "⚾ " if event == "Home Run" else ""
            tag = pl.get("event_tag") or ""
            tag_prefix = (sb_tag_html(tag) + " ") if tag else ""
            # Compact single-line format: count "B-S, NP" (one pitch shows as "1P").
            count_bit = f"{b}-{s}, {pitch_count}P" if pitch_count else f"{b}-{s}"
            # Anchor for SCORING-table deep links (#play-N).
            play_idx = pl.get("idx")
            anchor = f'<a id="play-{play_idx}"></a>' if play_idx is not None else ""
            # Only show "vs. <pitcher>" on the first batter against a pitcher
            # within the half-inning (or after a pitching change); subsequent
            # PAs against the same pitcher drop the redundant annotation.
            matchup = f" vs. {pitcher}" if first_pa_for_pitcher else ""
            row = f"{i}. {anchor}{tag_prefix}{event_marker}**{batter}**{matchup} — {desc}"
            if scored:
                row += f" **[{away_short} {a}, {home_short} {h}]**"
            row += f" · {count_bit}"
            sc = statcast_line_compact(int(pl["idx"]), pitches)
            if sc:
                row += f" · {sc}"
            items.append(row)
        log_sections.append(header_line + "\n\n" + "\n".join(items))
    game_log = "\n\n".join(log_sections) if log_sections else "_No play-by-play data available._"

    # --- Box score tables ---
    # Box-score cell emphasis helpers:
    # - `_em(x)`: bold when > 0, dim "·" when 0 (quickly eye-scan for production)
    # - `_emz(x)`: bold when > 0, normal "0" when 0 (used for columns where
    #              zero is meaningful and we don't want to hide it, e.g. AB)
    def _em(x) -> str:
        v = int(x or 0)
        return f"**{v}**" if v else '<span class="dim">·</span>'

    def _emz(x) -> str:
        v = int(x or 0)
        return f"**{v}**" if v else "0"

    def bat_table(team_short: str) -> str:
        rows = ["| Player | Pos | AB | R | H | RBI | BB | K | AVG |",
                "|--------|-----|----|----|----|-----|----|----|-----|"]
        totals = dict(ab=0, r=0, h=0, rbi=0, bb=0, k=0)
        team_rows = sorted([b for b in batting if b.get("team") == team_short],
                           key=lambda b: int(b.get("order") or 999))
        for b in team_rows:
            if b.get("ab") in (None, ""):
                continue
            # Anchor on the player name cell so top-performers links resolve.
            pid = b.get("player_id") or ""
            name_anchor = f'<a id="bat-{pid}"></a>' if pid else ""
            name = f"{name_anchor}{b['name']}"
            pos = b.get("position") or ""
            avg = b.get("avg_season") if b.get("avg_season") not in (None, "") else "—"
            rows.append(
                f"| {name} | {pos} | {b.get('ab',0)} | {_em(b.get('r'))} | {_em(b.get('h'))}"
                f" | {_em(b.get('rbi'))} | {_em(b.get('bb'))} | {_em(b.get('k'))} | {avg} |"
            )
            totals["ab"] += int(b.get("ab") or 0)
            totals["r"] += int(b.get("r") or 0)
            totals["h"] += int(b.get("h") or 0)
            totals["rbi"] += int(b.get("rbi") or 0)
            totals["bb"] += int(b.get("bb") or 0)
            totals["k"] += int(b.get("k") or 0)
        rows.append(f"| **Totals** | | **{totals['ab']}** | **{totals['r']}** | **{totals['h']}** | **{totals['rbi']}** | **{totals['bb']}** | **{totals['k']}** | |")
        return "\n".join(rows)

    def pit_table(team_short: str) -> str:
        rows = ["| Pitcher | IP | H | R | ER | BB | K | HR | ERA |",
                "|---------|----|----|----|----|----|----|----|-----|"]
        team_rows = sorted([p for p in pitching if p.get("team") == team_short],
                           key=lambda p: int(p.get("order") or 999))
        for p in team_rows:
            if p.get("ip") in (None, ""):
                continue
            pid = p.get("player_id") or ""
            name_anchor = f'<a id="pit-{pid}"></a>' if pid else ""
            name = p["name"]
            tag = p.get("decision") or ""
            # Decision tag (W/L/S) as a colored pill — green/red/gold.
            tag_pill = (" " + sb_tag_html(tag)) if tag else ""
            label = f"{name_anchor}{name}{tag_pill}"
            era = p.get("era_season") if p.get("era_season") not in (None, "") else "—"
            rows.append(
                f"| {label} | {fmt_ip(p.get('ip'))} | {_em(p.get('h'))} | {_em(p.get('r'))}"
                f" | {_em(p.get('er'))} | {_em(p.get('bb'))} | {_em(p.get('k'))} | {_em(p.get('hr'))} | {era} |"
            )
        return "\n".join(rows)

    # --- At a glance ---
    att = g.get("attendance") or "—"
    dur = g.get("duration") or "—"
    wx = g.get("weather") or "—"
    wind = g.get("wind") or ""
    first_pitch = g.get("first_pitch") or "—"
    umps = g.get("umpires") or "—"

    # External references — deep links to premium baseball research sites.
    # Each uses its own standard URL pattern; the subscription user's session
    # handles auth, we just compose the URL.
    _game_date = g["date"]
    br_url = br_box_url(home_abbr or home_short, _game_date) if home_abbr else ""
    fg_url = fangraphs_box_url(_game_date, away_short)
    ohtani_url = f"https://mlb.theohtani.com/game/{g.get('gamePk','')}" if g.get("gamePk") else ""
    ref_parts = [f'<a href="{br_url}">Baseball-Reference</a>'] if br_url else []
    ref_parts.append(f'<a href="{fg_url}">FanGraphs</a>')
    if ohtani_url:
        ref_parts.append(f'<a href="{ohtani_url}">mlb.theohtani.com</a>')
    # Sibling companions: the 1930s broadcast and the raw Markdown source.
    ds_name = Path(dataset.get("_dir", "")).name if dataset.get("_dir") else ""
    if ds_name:
        library_dir = Path(dataset["_dir"]).parent
        broadcast_html = library_dir / f"{ds_name}-broadcast.html"
        broadcast_md = library_dir / f"{ds_name}-broadcast.md"
        if broadcast_html.exists():
            ref_parts.append(f'<a href="{ds_name}-broadcast.html" title="1930s radio broadcast">🎙 1930s radio call</a>')
        elif broadcast_md.exists():
            ref_parts.append(f'<a href="{ds_name}-broadcast.md" title="1930s radio broadcast (Markdown)">🎙 1930s radio call</a>')
        ref_parts.append(f'<a href="{ds_name}.md" title="Markdown source">📄 .md</a>')
    external_refs = '<p class="external-refs">Also at: ' + " · ".join(ref_parts) + '</p>'

    # Compact symbol-driven AT A GLANCE banner (replaces the former 2-col table).
    # Every line leads with a recognizable glyph; team badges anchor the score;
    # baseball-native abbreviations (W, L, SV, HP, 1B, 2B, 3B) carry the labels.
    # Each pitcher of record gets a team pill so you can tell whose WP/LP/SV
    # it is without cross-referencing the score.
    # AT A GLANCE is now the game-context card: venue, crowd, time, weather,
    # umpires. The W/L/SV decisions and team records live in the LINE SCORE
    # column (decisions_block + records_line) so we don't duplicate them.
    wind_piece = f" · 💨 {wind_arrow(wind)}" if wind else ""
    # Compute pace (minutes per half-inning) as a small novelty stat —
    # surfaces something the line score can't already show at a glance.
    pace_line = ""
    try:
        dur_s = (dur or "").strip()
        if dur_s and ":" in dur_s:
            h_, m_ = dur_s.split(":")[:2]
            total_min = int(h_) * 60 + int(m_)
            half_innings = max(1, len([i for i in linescore if i.get("inning") not in ("R","H","E")])) * 2
            pace_line = f"⏲ {total_min // half_innings}m per half-inning"
    except (ValueError, TypeError):
        pass
    glance_lines = [
        f'<p class="score">{away_pill} {away_r} — {home_r} {home_pill}</p>',
        f"🏟 {venue} · 👥 {att} · ⏱ {dur} · 🕕 {first_pitch}",
        f"☀ {wx}{wind_piece}" if wx and wx != "—" else "",
        f"⚖ {tag_umpires(umps)}" if umps and umps != "—" else "",
        pace_line,
    ]
    at_glance = '<div class="glance">\n\n' + "\n\n".join(s for s in glance_lines if s) + "\n\n</div>"

    # --- Atmosphere ---
    atmosphere_rows: list[str] = []
    lat = g.get("lat")
    lon = g.get("lon")
    tz_id = g.get("tz")
    day_night = g.get("dayNight") or ""
    game_date_iso = g["date"]

    sunrise_dt = sunset_dt = civil_end_dt = None
    if lat not in (None, "") and lon not in (None, "") and tz_id:
        tz = ZoneInfo(tz_id)
        y, m, d = map(int, game_date_iso.split("-"))
        gd = _date(y, m, d)
        sunrise_dt = sun_event(float(lat), float(lon), gd, tz, sunrise=True)
        sunset_dt = sun_event(float(lat), float(lon), gd, tz, sunrise=False)
        civil_end_dt = sun_event(float(lat), float(lon), gd, tz, zenith=96.0, sunrise=False)
        fp_dt = parse_first_pitch(first_pitch, game_date_iso, tz) if first_pitch and first_pitch != "—" else None
        sunlight_phrase = ""
        if sunset_dt and fp_dt:
            delta_min = (sunset_dt - fp_dt).total_seconds() / 60
            if delta_min > 60:
                sunlight_phrase = f"Day game — full sunlight through the early innings (sunset {fmt_local_time(sunset_dt)})"
            elif delta_min > 10:
                sunlight_phrase = f"Late-afternoon start — sun still up at first pitch, setting at {fmt_local_time(sunset_dt)}; middle innings slide into dusk"
            elif delta_min > -30:
                sunlight_phrase = f"Twilight start — first pitch right around sunset ({fmt_local_time(sunset_dt)})"
            else:
                sunlight_phrase = f"Night game — sun already down at first pitch (sunset was {fmt_local_time(sunset_dt)})"
            if civil_end_dt:
                sunlight_phrase += f". Full dark around {fmt_local_time(civil_end_dt)}"
            sunlight_phrase += "."
        elif sunset_dt:
            sunlight_phrase = f"Sunset {fmt_local_time(sunset_dt)} local."
        atmosphere_rows.append(f"- **Time of day.** {sunlight_phrase or day_night.title()}")
        if sunrise_dt:
            atmosphere_rows.append(
                f"- **Sun.** Rise {fmt_local_time(sunrise_dt)} · Set {fmt_local_time(sunset_dt) if sunset_dt else '—'}"
                + (f" · Civil twilight ends {fmt_local_time(civil_end_dt)}" if civil_end_dt else "")
            )
    elif day_night:
        atmosphere_rows.append(f"- **Time of day.** {day_night.title()} game.")

    if wx and wx != "—":
        wx_glyph = weather_glyph(str(wx))
        wind_clause = f" · \U0001f4a8 Wind {wind_arrow(wind)}" if wind else ""
        atmosphere_rows.append(f"- **Weather.** {wx_glyph} {wx}{wind_clause}")
    if g.get("city") and g.get("state"):
        atmosphere_rows.append(
            f"- **Where.** {venue}, {g['city']}, {g['state']} · elevation {g.get('elevation','—') or '—'} ft · field azimuth {g.get('azimuth','—') or '—'}°"
        )
    crowd_bits = []
    if att and att != "—":
        crowd_bits.append(f"attendance {att}")
    if first_pitch and first_pitch != "—":
        crowd_bits.append(f"first pitch {first_pitch}")
    if dur and dur != "—":
        crowd_bits.append(f"game time {dur}")
    if crowd_bits:
        atmosphere_rows.append(f"- **Crowd.** " + " · ".join(crowd_bits))

    # Standouts from pitches.csv + plays
    longest_hr = None
    hardest_hit = None
    fastest_pitch = None
    hrs_list: list[tuple[str, str, int]] = []
    play_by_idx = {int(p["idx"]): p for p in plays}
    for pl in plays:
        if pl.get("event") == "Home Run":
            batter = last_name(pl["batter"])
            inn = int(pl["inning"])
            team_s = away_short if pl["half"] == "top" else home_short
            hrs_list.append((batter, team_s, inn))
    for pi in pitches:
        play = play_by_idx.get(int(pi.get("play_idx") or -1))
        if not play:
            continue
        batter = last_name(play["batter"])
        pitcher = last_name(play["pitcher"])
        evt = play.get("event") or ""
        inn = int(play["inning"])
        half = play["half"]
        dist = pi.get("distance_ft")
        ev = pi.get("ev_mph")
        sp = pi.get("speed_mph")
        ptype = pi.get("type_desc") or ""
        if dist not in (None, "") and evt == "Home Run":
            d_ = float(dist)
            if not longest_hr or d_ > longest_hr[0]:
                longest_hr = (d_, batter, inn, half)
        if ev not in (None, ""):
            e_ = float(ev)
            if not hardest_hit or e_ > hardest_hit[0]:
                hardest_hit = (e_, batter, evt or "Ball in play")
        if sp not in (None, ""):
            s_ = float(sp)
            if not fastest_pitch or s_ > fastest_pitch[0]:
                fastest_pitch = (s_, pitcher, ptype, batter)

    standouts: list[str] = []
    if longest_hr:
        d_, batter, inn, half = longest_hr
        team_s = away_short if half == "top" else home_short
        standouts.append(f"Longest HR: **{batter}** ({team_s}), {int(d_)} ft in the {ord_abbr(inn)}")
    if hardest_hit:
        e_, batter, evt = hardest_hit
        standouts.append(f"Hardest-hit ball: **{batter}**, {e_:.1f} mph ({evt})")
    if fastest_pitch:
        sp, pitcher, ptype, _ = fastest_pitch
        ptype_s = f" {ptype}" if ptype else ""
        standouts.append(f"Fastest pitch: **{pitcher}**, {sp:.1f} mph{ptype_s}")
    if not standouts and hrs_list:
        standouts.append("HRs: " + "; ".join(f"**{b}** ({t}), {ord_abbr(i)}" for b, t, i in hrs_list))
    if standouts:
        label = "Statcast standouts" if (longest_hr or hardest_hit or fastest_pitch) else "Notables"
        atmosphere_rows.append(f"- **{label}.** " + " · ".join(standouts) + ".")

    atmosphere_block = "\n".join(atmosphere_rows)

    # --- Moment of the game ---
    moment_idx = g.get("captivating_play_idx")
    moment_block = "_—_"
    if moment_idx not in (None, ""):
        pl = play_by_idx.get(int(moment_idx))
        if pl:
            ci = int(pl.get("captivating_index") or 0)
            inn = int(pl["inning"])
            half = pl["half"]
            half_word = "top" if half == "top" else "bottom"
            batter = last_name(pl["batter"])
            pitcher = last_name(pl["pitcher"])
            desc = (pl.get("description") or "").strip()
            b = int(pl.get("balls") or 0)
            s_ = int(pl.get("strikes") or 0)
            sc = None
            play_pitches = [p for p in pitches if int(p.get("play_idx") or -1) == int(pl["idx"])]
            for pi in play_pitches:
                if pi.get("ev_mph") not in (None, ""):
                    parts = [f"{float(pi['ev_mph']):.1f} mph exit velocity"]
                    if pi.get("la_deg") not in (None, ""):
                        parts.append(f"{float(pi['la_deg']):.0f}° launch angle")
                    if pi.get("distance_ft") not in (None, ""):
                        parts.append(f"{int(float(pi['distance_ft']))} ft")
                    sc = ", ".join(parts)
            # Link the inning/count citation to the matching PBP entry below.
            pl_idx = pl.get("idx")
            citation = f"**{batter}** vs. **{pitcher}**, {half_word} of the {ord_abbr(inn)}, {b}-{s_} count"
            if pl_idx is not None:
                citation = f"[{citation}](#play-{pl_idx})"
            moment_block = (
                f"> _\u201c{desc}\u201d_\n>\n"
                f"> — {citation}."
            )
            if sc:
                moment_block += f" {sc}."
            moment_block += f" *(MLB captivating index: {ci}/100.)*"

    # --- Attended banner + personal notes ---
    attended = bool(g.get("attended"))
    seat_line = ""
    companion_line = ""
    if attended:
        seat_parts = []
        if g.get("seat_section"):
            seat_parts.append(f"Section **{g['seat_section']}**")
        if g.get("seat_row"):
            seat_parts.append(f"Row **{g['seat_row']}**")
        if g.get("seat_number"):
            seat_val = str(g["seat_number"])
            # Pluralize when the value names more than one seat (e.g. "1 & 2", "1,2", "1-3")
            plural = any(c in seat_val for c in "&,+") or bool(re.search(r"\d+\s*-\s*\d+", seat_val))
            label = "Seats" if plural else "Seat"
            seat_parts.append(f"{label} **{seat_val}**")
        if seat_parts:
            seat_line = " · ".join(seat_parts)
        if g.get("companions"):
            companion_line = "With: " + str(g["companions"])

    attended_banner = ""
    if attended:
        bits = ["🎟 *Attended.*"]
        if seat_line:
            bits.append(seat_line)
        if companion_line:
            bits.append(companion_line)
        attended_banner = "\n\n> " + " · ".join(bits) + "\n"

    personal_notes_block = ""
    if attended:
        personal_notes_block = (
            "## PERSONAL NOTES\n\n"
            "<!-- Add your memories, impressions, and anything the box score can't capture. -->\n\n"
            "_\u2014_\n\n"
        )

    # --- Notable events blob ---
    notable: dict = {}
    notable_json = g.get("notable_events_json")
    if notable_json:
        try:
            notable = json.loads(notable_json) if isinstance(notable_json, str) else notable_json
        except (ValueError, TypeError):
            notable = {}

    # --- Media (photos + videos; auto-discovered from dataset dir) ---
    # The dataset directory can hold `photos/` and `videos/` subdirs — when
    # media files are present, a MEDIA section is emitted with links.
    # Anthony's wife takes photos/videos at the games; we link them here as
    # she processes them. Add files manually; we auto-index on render.
    PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif"}
    VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".webm", ".mkv"}
    media_block = ""
    ds_dir = dataset.get("_dir")
    if ds_dir and Path(ds_dir).is_dir():
        ds_dir = Path(ds_dir)
        slug_name = ds_dir.name
        photos_dir = ds_dir / "photos"
        videos_dir = ds_dir / "videos"
        photos = sorted([p for p in photos_dir.iterdir() if p.suffix.lower() in PHOTO_EXTS]) if photos_dir.is_dir() else []
        videos = sorted([v for v in videos_dir.iterdir() if v.suffix.lower() in VIDEO_EXTS]) if videos_dir.is_dir() else []
        if photos or videos:
            parts: list[str] = []
            if photos:
                parts.append("**📷 Photos** (" + str(len(photos)) + ")")
                parts.append(
                    "\n".join(
                        f'- <a href="{slug_name}/photos/{p.name}">{p.stem}</a>'
                        for p in photos
                    )
                )
            if videos:
                parts.append("**🎥 Videos** (" + str(len(videos)) + ")")
                parts.append(
                    "\n".join(
                        f'- <a href="{slug_name}/videos/{v.name}">{v.stem}</a>'
                        for v in videos
                    )
                )
            media_block = "\n\n".join(parts)

    # --- Notes section (three columns: away lineup · home lineup · game context) ---
    # Symbols chosen for scannability (baseball-page conventions where possible):
    # ⚖ umpires · 👥 attendance · ⏱ duration · 🕕 first pitch · 💨 wind · 📋 records
    # ⚠ balks / wild pitches · 🎯 pickoffs · 🚪 ejections · ⏰ pitch-timer violations
    NOTE_GLYPH = {
        "Umpires": "⚖",
        "First pitch": "🕕",
        "Attendance": "👥",
        "Duration": "⏱",
        "Weather": "☀",
        "Wind": "💨",
        "Records": "📋",
        "WP": "⚡",
        "PB": "✋",
        "Balk": "⚠",
        "HBP": "💥",
        "IBB": "⊕",
        "SB": "⚡",
        "CS": "⊗",
        "E": "✕",
        "DP": "⇉",
        "Pickoffs": "🎯",
        "Ejections": "🚪",
        "Disengagement violations": "⏰",
        "Pitch timer violations": "⏰",
    }

    def _note_line(label: str, value: str) -> str:
        glyph = NOTE_GLYPH.get(label, "·")
        return f"- {glyph} **{label}:** {value}"

    # Column 3: game-context notes (umpires, attendance, weather, violations, etc.)
    game_notes_lines: list[str] = []
    for k in NOTES_SECTION_KEYS:
        if notable.get(k):
            game_notes_lines.append(_note_line(k, str(notable[k])))
    if g.get("umpires") and g["umpires"] != "—":
        game_notes_lines.append(_note_line("Umpires", tag_umpires(g["umpires"])))
    if g.get("first_pitch") and g["first_pitch"] != "—":
        game_notes_lines.append(_note_line("First pitch", g["first_pitch"]))
    if g.get("attendance") and g["attendance"] != "—":
        game_notes_lines.append(_note_line("Attendance", g["attendance"]))
    if g.get("duration") and g["duration"] != "—":
        game_notes_lines.append(_note_line("Duration", g["duration"]))
    if g.get("weather") and g["weather"] != "—":
        game_notes_lines.append(_note_line("Weather", g["weather"]))
    if g.get("wind"):
        game_notes_lines.append(_note_line("Wind", wind_arrow(g["wind"])))
    if g.get("away_record") or g.get("home_record"):
        game_notes_lines.append(_note_line(
            "Records",
            f"{away_pill} {g.get('away_record','')} · {home_pill} {g.get('home_record','')}"
        ))

    # Columns 1–2: starting lineups (1–9 with position; DH gets line 10 if listed).
    def _lineup_lines(team_short: str) -> list[str]:
        roster = [b for b in batting if b.get("team") == team_short]
        starters = [b for b in roster if b.get("is_starter") in (True, "True", "true", 1, "1")]
        # Sort by batting order (already the insertion order, but be explicit).
        starters.sort(key=lambda b: int(b.get("order") or 99))
        lines: list[str] = []
        for b in starters:
            pos = b.get("position") or ""
            name = last_name(b.get("name") or "")
            order = b.get("order")
            if order in (None, "", 0):
                continue
            lines.append(f"{order}. {pos_tag(pos)} {name}")
        return lines

    away_lineup = _lineup_lines(away_short)
    home_lineup = _lineup_lines(home_short)

    notes_block = (
        '<div class="threecol notes-card">\n\n'
        f'<section class="team-{away_abbr}">\n\n'
        f"**{away_pill} Lineup**\n\n"
        + ("\n".join(away_lineup) if away_lineup else "_—_")
        + "\n\n</section>\n\n"
        f'<section class="team-{home_abbr}">\n\n'
        f"**{home_pill} Lineup**\n\n"
        + ("\n".join(home_lineup) if home_lineup else "_—_")
        + "\n\n</section>\n\n"
        "<section>\n\n"
        "**📋 Game notes**\n\n"
        + ("\n".join(game_notes_lines) if game_notes_lines else "_No notable events logged._")
        + "\n\n</section>\n\n"
        "</div>"
    )

    # --- Batting notes (per team, below each batting table) ---
    def _batting_notes(team_short: str) -> str:
        opp_short = home_short if team_short == away_short else away_short
        team_half = "top" if team_short == away_short else "bottom"
        # Filter plays by the batting team (half inning).
        tplays = [p for p in plays if p.get("half") == team_half]

        def _names_for(event_name: str) -> list[str]:
            return [last_name(p["batter"]) for p in tplays if p.get("event") == event_name]

        def _names_for_any(events: list[str]) -> list[str]:
            return [last_name(p["batter"]) for p in tplays if p.get("event") in events]

        doubles = _names_for("Double")
        triples = _names_for("Triple")
        # HR: also cite the opposing pitcher, with season-HR in parens.
        hr_entries: list[str] = []
        for p in tplays:
            if p.get("event") != "Home Run":
                continue
            batter = last_name(p["batter"])
            pitcher = last_name(p["pitcher"])
            bid = p.get("batter_id")
            season_hr = batter_season_hr.get(int(bid)) if bid not in (None, "") else None
            season_tag = f" ({season_hr}" if season_hr not in (None, "") else " ("
            # "(3, off Waldron)" — if no season HR, drop the leading number.
            inner = (f"{season_hr}, off {pitcher}" if season_hr not in (None, "") else f"off {pitcher}")
            hr_entries.append(f"{batter} ({inner})")

        # HBP is on the batting team's row (batter is the hit batter).
        hbp_entries: list[str] = []
        for p in tplays:
            if p.get("event") == "Hit By Pitch":
                hbp_entries.append(f"{last_name(p['batter'])} (by {last_name(p['pitcher'])})")

        gidp = _names_for("Grounded Into DP") + _names_for("GIDP")
        sh = _names_for_any(["Sac Bunt", "Sacrifice Bunt DP"])
        sf = _names_for_any(["Sac Fly", "Sac Fly Double Play"])

        # SB/CS come from team-aggregate stats tucked in notable_events_json.
        # NOTE: LOB is deliberately omitted. MLB's teamStats.batting.leftOnBase
        # is a cumulative counter (sum across innings), not the scorebook's
        # "runners stranded" figure. Rendering the raw API value gives wrong
        # numbers (e.g. 19 instead of ~7), and the skill's hard rule is
        # "facts are gospel" — a wrong LOB poisons the keepsake. Reinstate
        # when we reconstruct proper LOB from the per-play runner states.
        tbs = (notable.get("_team_bat_stats") or {}).get(team_short, {}) if isinstance(notable, dict) else {}
        sb_ct = tbs.get("sb", "")
        cs_ct = tbs.get("cs", "")

        def _fmt(lst: list[str]) -> str:
            return "; ".join(lst) if lst else "\u2014"

        bat_line = " · ".join([
            f"{sb_tag_html('2B')} {_fmt(doubles)}",
            f"{sb_tag_html('3B')} {_fmt(triples)}",
            f"{sb_tag_html('HR')} {_fmt(hr_entries)}",
            f"{sb_tag_html('SB')} {sb_ct if sb_ct not in (None,'','0',0) else '\u2014'}",
            f"{sb_tag_html('CS')} {cs_ct if cs_ct not in (None,'','0',0) else '\u2014'}",
            f"{sb_tag_html('SH')} {_fmt(sh)}",
            f"{sb_tag_html('SF')} {_fmt(sf)}",
            f"{sb_tag_html('HBP')} {_fmt(hbp_entries)}",
            f"{sb_tag_html('GIDP')} {_fmt(gidp)}",
        ])
        return f"**Batting notes** — {bat_line}"

    # --- Pitching notes (per team, below each pitching table) ---
    def _pitching_notes(team_short: str) -> str:
        # Pitchers on this team threw while the OTHER team batted, so filter plays accordingly.
        opp_half = "bottom" if team_short == away_short else "top"
        tplays = [p for p in plays if p.get("half") == opp_half]

        # WP / PB / Balk / IBB / Pickoffs come from the notable blob (strings keyed by pitcher/catcher name).
        def _nb(key: str) -> str:
            v = notable.get(key) if isinstance(notable, dict) else None
            return v if v else "\u2014"

        # Pitches-strikes / Batters faced / Inherited runners-scored are whole-game strings
        # in the notable blob — filter to pitchers on this team.
        # MLB feed uses last-name-first form "Morgan, D 38-22" so entries are ONLY
        # split on semicolons (the commas inside names would break a naive split).
        team_pitcher_lasts = {last_name(p["name"]) for p in pitching if p.get("team") == team_short}

        def _filter_pitcher_list(raw: str) -> str:
            if not raw:
                return "\u2014"
            entries = [e.strip() for e in str(raw).split(";") if e.strip()]
            kept = [e for e in entries
                    if any(e.startswith(last) for last in team_pitcher_lasts)]
            return "; ".join(kept) if kept else "\u2014"

        pitches_strikes = _filter_pitcher_list(notable.get("Pitches-strikes", "") if isinstance(notable, dict) else "")
        inh = _filter_pitcher_list(notable.get("Inherited runners-scored", "") if isinstance(notable, dict) else "")
        bf = _filter_pitcher_list(notable.get("Batters faced", "") if isinstance(notable, dict) else "")

        # WP / PB / Balk / IBB / Pickoffs — show only names matching this team's pitchers (catchers for PB).
        def _names_matching(raw: str, name_pool: list[str]) -> str:
            if not raw:
                return "\u2014"
            entries = [e.strip() for e in str(raw).split(";") if e.strip()]
            kept = [e for e in entries if any(e.startswith(n) for n in name_pool)]
            return "; ".join(kept) if kept else "\u2014"

        pool = list(team_pitcher_lasts)
        wp_line = _names_matching(notable.get("WP", "") if isinstance(notable, dict) else "", pool)
        balk_line = _names_matching(notable.get("Balk", "") if isinstance(notable, dict) else "", pool)
        ibb_line = _names_matching(notable.get("IBB", "") if isinstance(notable, dict) else "", pool)
        pko_line = _names_matching(notable.get("Pickoffs", "") if isinstance(notable, dict) else "", pool)
        # PB is attributed to the catcher — we don't have per-team catcher lists handy; show raw if present.
        pb_line = _nb("PB") if notable.get("PB") else "\u2014"

        head = "**Pitching notes** — " + " · ".join([
            f"{sb_tag_html('WP')} {wp_line}",
            f"{sb_tag_html('PB')} {pb_line}",
            f"{sb_tag_html('Balk')} {balk_line}",
            f"{sb_tag_html('IBB')} {ibb_line}",
            f"{sb_tag_html('Pickoffs')} {pko_line}",
        ])
        tail_bits = []
        if pitches_strikes != "\u2014":
            tail_bits.append(f"**Pitches-strikes** {pitches_strikes}")
        if inh != "\u2014":
            tail_bits.append(f"**Inherited runners–scored** {inh}")
        if bf != "\u2014":
            tail_bits.append(f"**Batters faced** {bf}")
        if tail_bits:
            head += " · " + " · ".join(tail_bits)
        return head

    # --- Frontmatter ---
    frontmatter = (
        "---\n"
        f"date: {game_date_iso}\n"
        f"gamePk: {g.get('gamePk','')}\n"
        f"venue: {venue}\n"
        f"away_team: {away_name}\n"
        f"home_team: {home_name}\n"
        f"away_team_short: {away_short}\n"
        f"home_team_short: {home_short}\n"
        f"away_team_abbr: {away_abbr}\n"
        f"home_team_abbr: {home_abbr}\n"
        f"final_away: {away_r}\n"
        f"final_home: {home_r}\n"
        f"attended: {'true' if attended else 'false'}\n"
        + (f"seat: {seat_line.replace('**','')}\n" if seat_line else "")
        + "---\n"
    )

    # Optional BR link on the headline (title click-through to the canonical
    # boxscore on Baseball-Reference — they don't mind inbound links).
    headline_md = f"[{headline}]({br_url})" if br_url else headline

    md = f"""{frontmatter}
# {headline_md}
{subhead}
{external_refs}
{attended_banner}
{lede_para}

---

<div class="twocol">

<section>

## AT A GLANCE

{at_glance}

</section>

<section>

## LINE SCORE

{line_table}

{decisions_block}

{records_line}

</section>

</div>

## ✨ TOP PERFORMERS

{performers_block}

## BOX SCORE — BATTING

<div class="twocol boxes">

<section class="team-{away_abbr}">

{away_pill} **{away_name}**

{bat_table(away_short)}

{_batting_notes(away_short)}

</section>

<section class="team-{home_abbr}">

{home_pill} **{home_name}**

{bat_table(home_short)}

{_batting_notes(home_short)}

</section>

</div>

## BOX SCORE — PITCHING

<div class="twocol boxes">

<section class="team-{away_abbr}">

{away_pill} **{away_name}**

{pit_table(away_short)}

{_pitching_notes(away_short)}

</section>

<section class="team-{home_abbr}">

{home_pill} **{home_name}**

{pit_table(home_short)}

{_pitching_notes(home_short)}

</section>

</div>

<div class="twocol">

<section>

## ATMOSPHERE

{atmosphere_block}

</section>

<section>

## ★ MOMENT OF THE GAME

{moment_block}

</section>

</div>

## SCORING

{scoring_block}

{('## WIN PROBABILITY' + chr(10) + chr(10) + wp_block + chr(10) + chr(10)) if wp_block else ''}## NOTES

{notes_block}

## PLAY-BY-PLAY

{game_log}

{('## MEDIA' + chr(10) + chr(10) + media_block + chr(10) + chr(10)) if media_block else ''}{personal_notes_block}---

*Source: MLB Stats API, gamePk {g.get('gamePk','')}.*
"""
    out_meta = {
        "date": game_date_iso,
        "away_slug": slugify(away_short),
        "home_slug": slugify(home_short),
    }
    return md, out_meta


# --- HTML + INDEX (shared between report + render) -------------------------

def render_html(md_path: Path, body_class: str = ""):
    import shutil
    import subprocess
    import sys
    if not shutil.which("pandoc"):
        print("pandoc not found; skipping HTML render. `brew install pandoc` to enable.", file=sys.stderr)
        return None
    html_path = md_path.with_suffix(".html")
    css = CSS_FILE.read_text() if CSS_FILE.exists() else ""
    cmd = [
        "pandoc",
        "--from=gfm+yaml_metadata_block+smart",
        "--to=html5",
        "--standalone",
        "--wrap=preserve",
        "--variable=document-css=false",
        "--metadata", "title= ",
        "--metadata", "lang=en",
        str(md_path),
        "-o", str(html_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"pandoc failed: {e.stderr}", file=sys.stderr)
        return None
    html = html_path.read_text()
    html = re.sub(
        r"<style>\s*/\*\s*Default styles provided by pandoc\..*?</style>",
        "", html, count=1, flags=re.DOTALL,
    )
    html = re.sub(r'<h1 class="title">[^<]*</h1>\s*', "", html, count=1)
    # Strip pandoc's auto-rendered `<p class="date">…</p>` (produced from the
    # YAML `date:` frontmatter we write for INDEX scanning). The date is
    # already present in the dateline + AT A GLANCE block.
    html = re.sub(r'<p class="date">[^<]*</p>\s*', "", html, count=1)
    # And any stray empty title-block header wrapper pandoc leaves behind.
    html = re.sub(r'<header id="title-block-header">\s*</header>\s*', "", html, count=1)
    style_block = f"<style>\n{css}\n</style>"
    # Site nav is injected by /nav.js on the hosted site (see mlb-games/nav.js).
    # The file is served from the site root, so an absolute /nav.js path works
    # when deployed; when viewing the HTML locally the script 404s harmlessly
    # and the page renders without the nav bar.
    nav_script = '<script src="/nav.js" defer></script>'
    html = html.replace("</head>", f"{style_block}\n{nav_script}\n</head>", 1)
    if body_class:
        html = html.replace("<body>", f'<body class="{body_class}">', 1)
    html_path.write_text(html)
    return html_path


def rebuild_index(library: Path) -> None:
    entries: list[dict] = []
    for p in sorted(library.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        # Variant .md files (1930s radio-call transcripts etc.) are not games;
        # they're surfaced as a separate column on the game's row.
        if p.stem.endswith("-broadcast"):
            continue
        txt = p.read_text().splitlines()
        if not txt or txt[0].strip() != "---":
            continue
        meta: dict[str, str] = {}
        for ln in txt[1:]:
            if ln.strip() == "---":
                break
            if ":" in ln:
                k, v = ln.split(":", 1)
                meta[k.strip()] = v.strip()
        # Require a date so we don't accept stray non-game .md files.
        if not meta.get("date"):
            continue
        meta["_file"] = p.name
        entries.append(meta)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    lines = ["# Games Attended", "",
             f"*{len(entries)} game{'s' if len(entries) != 1 else ''} in the log.*", "",
             "| Date | Matchup | Final | Venue | Log | Broadcast |",
             "|------|---------|-------|-------|-----|-----------|"]
    for e in entries:
        aw_s = e.get('away_team_short','?'); hm_s = e.get('home_team_short','?')
        aw_a = e.get('away_team_abbr') or aw_s
        hm_a = e.get('home_team_abbr') or hm_s
        # Team pills mirror the series-recap landing style.
        aw_pill = f'<span class="team-badge team-{aw_a}">{aw_a}</span>' if aw_a else aw_s
        hm_pill = f'<span class="team-badge team-{hm_a}">{hm_a}</span>' if hm_a else hm_s
        matchup = f"{aw_pill} @ {hm_pill}"
        try:
            fa = int(e.get('final_away','0') or 0); fh = int(e.get('final_home','0') or 0)
        except ValueError:
            fa = fh = 0
        if fh > fa:
            final = f"{hm_s} {fh}, {aw_s} {fa}"
        else:
            final = f"{aw_s} {fa}, {hm_s} {fh}"
        stem = Path(e["_file"]).stem
        html_sibling = (library / e["_file"]).with_suffix(".html")
        log_link = f"[log]({html_sibling.name})" if html_sibling.exists() else f"[log]({e['_file']})"
        bcast_html = library / f"{stem}-broadcast.html"
        bcast_md = library / f"{stem}-broadcast.md"
        if bcast_html.exists():
            bcast_cell = f"[radio call]({bcast_html.name})"
        elif bcast_md.exists():
            bcast_cell = f"[radio call]({bcast_md.name})"
        else:
            bcast_cell = "—"
        lines.append(f"| {e.get('date','')} | {matchup} | {final} | {e.get('venue','')} | {log_link} | {bcast_cell} |")
    idx_md = library / "INDEX.md"
    idx_md.write_text("\n".join(lines) + "\n")
    if any((library / e["_file"]).with_suffix(".html").exists() for e in entries):
        render_html(idx_md, body_class="index")
