#!/usr/bin/env python3
"""
Fetch the world's most popular radio stations into Swift Radio's bundled catalog.

Sources the community-maintained Radio Browser directory (radio-browser.info),
ranked by community votes per country, validates that each stream actually
serves audio, and writes the template's `station` JSON envelope to
SwiftRadio/Data/stations.json.

Two curated entries are pinned so the project's UI tests stay meaningful:
  - "Absolute Country Hits" stays first (live stream + bundled artwork asset).
  - "MP3 file sample" stays last (offline file playback / seek fixture).

Stdlib only. Python 3.9+.

Usage:
    python3 Scripts/fetch_stations.py
    python3 Scripts/fetch_stations.py --per-country 3 --timeout 3
    python3 Scripts/fetch_stations.py --no-check          # skip stream validation
    python3 Scripts/fetch_stations.py --out /tmp/stations.json
"""

import argparse
import concurrent.futures
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "SwiftRadio" / "Data" / "stations.json"

# Radio Browser community mirrors, tried in order until one answers.
MIRRORS = [
    "https://de1.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
]

# The API asks clients to identify themselves.
UA = "SwiftRadioCatalogFetcher/1.0 (+https://github.com/Cyb3rStudio/Swift-Radio-Pro)"

# ~50 countries spanning every inhabited region; top `--per-country` stations each.
COUNTRIES = [
    # Americas
    "US", "CA", "MX", "BR", "AR", "CL", "CO",
    # Europe
    "GB", "IE", "FR", "DE", "ES", "IT", "NL", "BE", "CH", "AT",
    "SE", "NO", "DK", "FI", "IS", "PL", "CZ", "SK", "HU", "RO", "BG", "GR",
    "TR", "RU", "UA", "RS",
    # Middle East
    "IL", "SA", "AE", "QA",
    # Africa
    "EG", "MA", "NG", "ZA", "KE", "TZ",
    # Asia
    "IN", "PK", "BD", "LK", "CN", "JP", "KR", "TW",
    # Southeast Asia
    "ID", "TH", "VN", "PH", "MY", "SG",
    # Oceania
    "AU", "NZ",
]

# Codecs AVFoundation (and therefore FRadioPlayer) can actually play.
PLAYABLE_CODECS = {"MP3", "AAC", "AAC+"}
# Resolved URLs ending in these are playlists, not streams.
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8", ".pls", ".asx", ".ram"}

# Radio Browser reports official long names ("The United States Of America");
# the catalog shows short display names in rows and the country picker.
COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "KR": "South Korea",
    "RU": "Russia", "TZ": "Tanzania", "TW": "Taiwan", "AE": "United Arab Emirates",
    "NL": "Netherlands", "PH": "Philippines", "VN": "Vietnam", "CD": "DR Congo",
    "CG": "Congo", "CI": "Côte d'Ivoire", "MK": "North Macedonia",
    "MD": "Moldova", "BA": "Bosnia & Herzegovina", "SZ": "Eswatini",
}

PINNED_FIRST = {
    "name": "Absolute Country Hits",
    "website": "https://www.1.fm/station/acountry",
    "streamURL": "http://strm112.1.fm/acountry_mobile_mp3",
    "imageURL": "station-absolutecountry",
    "desc": "The Music Starts Here",
    "longDesc": ("All your favorite country hits and artists, from Johnny Cash to "
                 "Taylor Swift, on 1.FM's Absolute Country, playing non-stop crooners "
                 "and banjos, dance-tunes and fiddles, ballads and harmonicas. Absolute "
                 "Country focuses on 5th, 6th and 7th generation Country (from the 90s on) "
                 "but often delves into classic, older tunes as well."),
}

PINNED_LAST = {
    "name": "MP3 file sample",
    "website": "https://samplelib.com",
    "streamURL": "https://freetestdata.com/wp-content/uploads/2021/09/Free_Test_Data_1OMB_MP3.mp3",
    "imageURL": "",
    "desc": "From: https://samplelib.com",
    "longDesc": "https://samplelib.com",
}

API_TIMEOUT = 20
CHECK_WORKERS_PER_COUNTRY = 6
COUNTRY_WORKERS = 8
seen_streams: set = set()
seen_lock = threading.Lock()


def ssl_context() -> ssl.SSLContext:
    """certifi's roots when installed; otherwise an unverified context, since the
    app plays these very streams over TLS without such verification anyway."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        log("certifi is not installed; falling back to an unverified TLS context.")
        return ssl._create_unverified_context()


TLS = ssl_context()


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def pick_mirror() -> str:
    for mirror in MIRRORS:
        try:
            request = urllib.request.Request(mirror + "/json/stats", headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=API_TIMEOUT, context=TLS) as response:
                if response.status == 200:
                    log(f"Using Radio Browser mirror: {mirror}")
                    return mirror
        except Exception as error:  # noqa: BLE001 - try the next mirror
            log(f"Mirror unreachable, skipping: {mirror} ({error})")
    raise SystemExit("No Radio Browser mirror is reachable.")


def api_get(base: str, path: str, params: dict) -> list:
    url = base + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=API_TIMEOUT, context=TLS) as response:
        return json.loads(response.read().decode("utf-8"))


def clean(value) -> str:
    return " ".join(str(value or "").split())


def title_tag(tag: str) -> str:
    return " ".join(word.capitalize() for word in tag.split())


def stream_of(station: dict) -> str:
    return clean(station.get("url_resolved")) or clean(station.get("url"))


def usable(station: dict) -> bool:
    """Catalog-level rules: checked, playable codec, not HLS, plausible stream URL."""
    if not station.get("lastcheckok"):
        return False
    if station.get("hls"):
        return False
    codec = clean(station.get("codec")).upper()
    if codec not in PLAYABLE_CODECS:
        return False
    stream = stream_of(station)
    if not stream.lower().startswith(("http://", "https://")):
        return False
    path = urllib.parse.urlparse(stream).path.lower()
    if Path(path).suffix in PLAYLIST_EXTENSIONS:
        return False
    return bool(clean(station.get("name")))


def check_stream(url: str, timeout: float) -> bool:
    """Opens the stream and reads a few bytes; a live server sends audio or errors loudly."""
    def attempt(context) -> bool:
        request = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status in (200, 206) and len(response.read(64)) > 0

    try:
        return attempt(TLS)
    except urllib.error.HTTPError:
        return False  # server answered: the stream is deliberately not servable
    except ssl.SSLError:
        try:  # self-signed stream hosts are common; the app's player does not verify either
            return attempt(ssl._create_unverified_context())
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001 - timeout, reset, DNS: treated as unreachable
        return False


def select_for_country(base: str, code: str, per_country: int,
                       timeout: float, do_check: bool) -> list:
    """Top `per_country` checked streams for one country, in community-vote order."""
    raw = []
    try:
        raw = api_get(base, "/json/stations/search", {
            "countrycode": code,
            "order": "votes",
            "reverse": "true",
            "limit": "40",
            "hidebroken": "true",
        })
    except Exception as error:  # noqa: BLE001
        log(f"  [{code}] search failed: {error}")
        return []
    time.sleep(0.3)  # the directory asks clients to stay under a request a second

    candidates = [s for s in raw if usable(s)]
    kept, position = [], 0
    while len(kept) < per_country and position < len(candidates):
        batch = candidates[position:position + CHECK_WORKERS_PER_COUNTRY]
        position += CHECK_WORKERS_PER_COUNTRY
        if do_check:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(CHECK_WORKERS_PER_COUNTRY, max(1, len(batch)))) as pool:
                results = list(pool.map(lambda s: (s, check_stream(stream_of(s), timeout)), batch))
        else:
            results = [(s, True) for s in batch]
        for station, healthy in results:
            if len(kept) >= per_country:
                break
            if not healthy:
                continue
            key = stream_of(station).strip().lower()
            with seen_lock:
                if key in seen_streams:
                    continue
                seen_streams.add(key)
            kept.append(station)
    return kept


def entry(station: dict) -> dict:
    """Maps a Radio Browser station onto the app's `station` JSON shape."""
    name = clean(station.get("name"))
    stream = stream_of(station)
    homepage = clean(station.get("homepage"))
    favicon = clean(station.get("favicon"))
    city = clean(station.get("state"))
    country = clean(station.get("country"))
    country_code = clean(station.get("countrycode")).upper()
    if country_code in COUNTRY_NAMES:
        country = COUNTRY_NAMES[country_code]
    tags = [title_tag(t) for t in
            (clean(station.get("tags")).split(",") if station.get("tags") else []) if t][:3]
    codec = clean(station.get("codec")).upper()
    bitrate = int(station.get("bitrate") or 0)

    # `desc` is required by the app's decoder and shown under the name; the row
    # renders the location as a separate caption, so desc leads with the genre.
    desc = ", ".join(tags) if tags else (
        f"Radio from {country}" if country else "Live radio stream")

    long_parts = []
    if city and country:
        long_parts.append(f"{name} broadcasts from {city}, {country}.")
    elif country:
        long_parts.append(f"{name} broadcasts from {country}.")
    else:
        long_parts.append(f"{name} streams live online.")
    if tags:
        long_parts.append(f"Plays {', '.join(t.lower() for t in tags)}.")
    if codec:
        long_parts.append(f"{codec} stream" + (f" at {bitrate} kbps" if bitrate > 0 else "") + ".")
    long_parts.append("Community-ranked on Radio Browser (radio-browser.info).")
    long_desc = " ".join(long_parts)

    entry_dict = {"name": name}
    if homepage.lower().startswith(("http://", "https://")):
        entry_dict["website"] = homepage
    entry_dict["streamURL"] = stream
    entry_dict["imageURL"] = favicon if favicon.lower().startswith(("http://", "https://")) else ""
    entry_dict["desc"] = desc
    entry_dict["longDesc"] = long_desc
    if country:
        entry_dict["country"] = country
    if country_code:
        entry_dict["countryCode"] = country_code
    if city:
        entry_dict["city"] = city
    if tags:
        entry_dict["tags"] = ", ".join(tags)
    return entry_dict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output path (default: {DEFAULT_OUT})")
    parser.add_argument("--per-country", type=int, default=3,
                        help="stations to keep per country (default: 3)")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="per-stream validation timeout in seconds (default: 3)")
    parser.add_argument("--no-check", action="store_true",
                        help="skip stream validation (faster, riskier catalog)")
    args = parser.parse_args()

    base = pick_mirror()
    validation = "off" if args.no_check else f"on, {args.timeout:.0f}s per stream"
    log(f"Fetching top {args.per_country} stations for {len(COUNTRIES)} countries "
        f"(validation {validation})...")

    fetched: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=COUNTRY_WORKERS) as pool:
        futures = {pool.submit(select_for_country, base, code, args.per_country,
                                args.timeout, not args.no_check): code
                   for code in COUNTRIES}
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            stations = future.result()
            if stations:
                log(f"  [{code}] kept {len(stations)}/{args.per_country}")
            fetched.extend(stations)

    # Pinned first entry must actually stream, or the app's own UI tests lie.
    if not args.no_check and not check_stream(PINNED_FIRST["streamURL"], args.timeout):
        raise SystemExit("Pinned 'Absolute Country Hits' stream is down; "
                          "update SwiftRadioUITests before shipping a dead pinned stream.")
    if not args.no_check and not check_stream(PINNED_LAST["streamURL"], args.timeout):
        raise SystemExit("Pinned 'MP3 file sample' file is unreachable; "
                          "update SwiftRadioUITests before shipping a dead pinned sample.")

    # Global popularity order: the planet's most-voted stations first, so the
    # plain catalog reads as a worldwide top list before any filtering.
    entries = [PINNED_FIRST]
    entries.extend(entry(s) for s in
                   sorted(fetched, key=lambda s: int(s.get("votes") or 0), reverse=True))
    entries.append(PINNED_LAST)

    countries = sorted({e["country"] for e in entries if e.get("country")})
    catalog = {"station": entries}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=4, ensure_ascii=False)
        handle.write("\n")

    log(f"\nWrote {len(entries)} stations across {len(countries)} countries to {args.out}")
    for country in countries:
        count = sum(1 for e in entries if e.get("country") == country)
        log(f"  {country}: {count}")


if __name__ == "__main__":
    main()