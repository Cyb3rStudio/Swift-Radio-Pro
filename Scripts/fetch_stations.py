#!/usr/bin/env python3
"""
Fetch the most popular radio stations into Swift Radio's bundled catalog: a
community-vote top hundred for each requested region (North America, Europe,
and Canada by default), plus every station carried over from the previously
bundled catalog (Scripts/legacy_stations.json).

Sources the community-maintained Radio Browser directory (radio-browser.info),
pools each region's stations into one community-vote ranking, validates that
each stream actually serves audio, and writes the template's `station` JSON
envelope to SwiftRadio/Data/stations.json.

Two curated entries are pinned so the project's UI tests stay meaningful:
  - "Absolute Country Hits" stays first (live stream + bundled artwork asset).
  - "MP3 file sample" stays last (offline file playback / seek fixture).

Stdlib only. Python 3.9+.

Usage:
    python3 Scripts/fetch_stations.py
    python3 Scripts/fetch_stations.py --per-region 100 --timeout 3
    python3 Scripts/fetch_stations.py --regions na,eu       # subset of regions
    python3 Scripts/fetch_stations.py --no-check            # skip stream validation
    python3 Scripts/fetch_stations.py --no-merge            # skip legacy additions
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
# Stations of the previously bundled catalog, merged into every run so a
# regeneration adds to the list instead of replacing it.
LEGACY_DEFAULT = REPO_ROOT / "Scripts" / "legacy_stations.json"

# Radio Browser community mirrors, tried in order until one answers.
MIRRORS = [
    "https://de1.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
]

# The API asks clients to identify themselves.
UA = "SwiftRadioCatalogFetcher/1.0 (+https://github.com/Cyb3rStudio/Swift-Radio-Pro)"

# Geographic regions as ISO 3166-1 alpha-2 codes. Each region's stations are
# pooled into one community-vote ranking, and its top `--per-region` healthy
# stations land in the catalog. In practice the rankings fill almost entirely
# from US/CA/MX and the big Western European countries; the remaining codes
# are listed so the regions stay geographic rather than cherrypicked.

# North America: the three majors, Central America, and the Caribbean.
NORTH_AMERICA = [
    # majors
    "US", "CA", "MX",
    # Central America
    "GT", "BZ", "SV", "HN", "NI", "CR", "PA",
    # Caribbean & Atlantic
    "CU", "HT", "DO", "JM", "PR", "TT", "BS", "BB", "DM", "GD",
    "LC", "VC", "AG", "KN", "AI", "MS", "KY", "BM", "TC", "VG", "GL",
]

# Geographic Europe: EU27 + UK + the other continental and microstates.
EUROPE = [
    # Western Europe
    "GB", "IE", "FR", "DE", "ES", "IT", "NL", "BE", "LU",
    # Nordics & alpine
    "CH", "AT", "SE", "NO", "DK", "FI", "IS",
    # Central & Eastern
    "PL", "CZ", "SK", "HU", "RO", "BG", "GR",
    # Southern & Baltic
    "PT", "HR", "SI", "LT", "LV", "EE", "CY", "MT",
    # Balkans & the East
    "RS", "BA", "AL", "MK", "ME", "UA", "MD", "XK", "BY", "RU",
    # Microstates & territories
    "AD", "MC", "SM", "VA", "LI", "GI", "FO",
]

# Canada rides along inside North America's ranking (a few Canadian stations
# make its global top) and gets a dedicated region below for its own top list;
# stream-URL deduplication keeps a station from landing in both twice.
CANADA = ["CA"]

REGIONS = {
    "na": ("North America", NORTH_AMERICA),
    "eu": ("Europe", EUROPE),
    "ca": ("Canada", CANADA),
}

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
# Stream validation walks a region's pooled vote ranking in batches of this size.
CHECK_WORKERS = 6
# Country searches run concurrently; each sleeps briefly after its request to
# roughly keep the directory's under-a-request-a-second etiquette.
COUNTRY_WORKERS = 8
seen_streams: set = set()
# Station identities already chosen, as (name, country): one mount or bitrate
# of a station wins a slot, its siblings are doubles. Radio Browser lists each
# mount as its own entry, so vote-ranked pools are full of them.
seen_stations: set = set()
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


def fetch_country_candidates(base: str, code: str) -> list:
    """One country's usable candidates, in community-vote order."""
    raw = []
    try:
        raw = api_get(base, "/json/stations/search", {
            "countrycode": code,
            "order": "votes",
            "reverse": "true",
            # Generous per-country cap: a single big country must not run dry
            # before the region finds its `--per-region` healthy stations.
            "limit": "200",
            "hidebroken": "true",
        })
    except Exception as error:  # noqa: BLE001
        log(f"  [{code}] search failed: {error}")
    time.sleep(0.3)  # the directory asks clients to stay under a request a second
    return [s for s in raw if usable(s)]


def station_key(station: dict) -> tuple:
    """A station's identity for deduplication: (name, country).

    Radio Browser lists every mount and bitrate of a station as its own entry,
    so one station fills several slots of a vote ranking. Stream URLs alone
    cannot tell siblings apart either: `France Inter` ships three mounts.
    Stations sharing a name across countries (two `ROCK FM`s) stay distinct.
    The country is normalized through COUNTRY_NAMES so fetched entries (raw
    directory names) and carried-over entries (mapped display names) hash alike.
    """
    country = clean(station.get("country"))
    code = clean(station.get("countrycode")).upper()
    if code in COUNTRY_NAMES:
        country = COUNTRY_NAMES[code]
    return (clean(station.get("name")).strip().lower(), country.strip().lower())


def select_region(base: str, label: str, codes: list, wanted: int,
                  timeout: float, do_check: bool) -> list:
    """The `wanted` most-voted healthy stations across a region's countries.

    Pools every country's vote-ranked candidates into one region-wide ranking,
    then walks it validating streams in batches (kept in ranking order) until
    `wanted` healthy ones are found. Deduplicates stream URLs globally so the
    regions never overlap and one relay is never listed twice.
    """
    pooled: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=COUNTRY_WORKERS) as pool:
        futures = {pool.submit(fetch_country_candidates, base, code): code
                   for code in codes}
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            candidates = future.result()
            pooled.extend(candidates)
            if candidates:
                log(f"  [{label}/{code}] {len(candidates)} usable candidates")
    pooled.sort(key=lambda s: int(s.get("votes") or 0), reverse=True)

    kept, position = [], 0
    while len(kept) < wanted and position < len(pooled):
        batch = pooled[position:position + CHECK_WORKERS]
        position += CHECK_WORKERS
        if do_check:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(CHECK_WORKERS, max(1, len(batch)))) as pool:
                results = list(pool.map(lambda s: (s, check_stream(stream_of(s), timeout)), batch))
        else:
            results = [(s, True) for s in batch]
        for station, healthy in results:
            if len(kept) >= wanted:
                break
            if not healthy:
                continue
            key = stream_of(station).strip().lower()
            identity = station_key(station)
            with seen_lock:
                if key in seen_streams or identity in seen_stations:
                    continue
                seen_streams.add(key)
                seen_stations.add(identity)
            kept.append(station)
    log(f"  [{label}] kept {len(kept)}/{wanted} healthy stations "
        f"from {len(pooled)} pooled candidates")
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


def load_legacy(path: Path) -> list:
    """A previous catalog's entries, keeping only ones the app can decode."""
    try:
        with path.open(encoding="utf-8") as handle:
            entries = json.load(handle)["station"]
    except Exception as error:  # noqa: BLE001 - a bad merge file must not kill a run
        log(f"Could not read previous catalog {path}: {error}")
        return []
    kept = []
    for station in entries:
        # The app's decoder requires name, streamURL, imageURL and desc; longDesc
        # and the location keys are optional.
        if not (clean(station.get("name")) and clean(station.get("streamURL"))
                and "imageURL" in station and clean(station.get("desc"))):
            log(f"  skipping malformed previous entry: {station.get('name')!r}")
            continue
        kept.append(station)
    return kept


def merge_legacy(path: Path, reserved: list, timeout: float, do_check: bool) -> list:
    """A previous catalog's stations that the freshly fetched ones don't cover.

    `reserved` are the entries already chosen (region blocks, pins, and the
    last pin). A previous station already among them by stream URL or by
    identity ((name, country) — one station's other mounts are doubles) is
    skipped; when validation is on, a previous stream that no longer serves
    audio is dropped with a log line so it never silently disappears.
    """
    streams = {clean(e.get("streamURL")).strip().lower() for e in reserved}
    identities = {station_key(e) for e in reserved}
    merged, dead, duplicates = [], [], 0
    for station in load_legacy(path):
        stream_key = clean(station.get("streamURL")).strip().lower()
        identity = station_key(station)
        if stream_key in streams or identity in identities:
            duplicates += 1
            continue
        if do_check and not check_stream(clean(station.get("streamURL")), timeout):
            dead.append(station)
            continue
        streams.add(stream_key)
        identities.add(identity)
        merged.append(station)
    log(f"  previous catalog: {len(merged)} added, {duplicates} already present, "
        f"{len(dead)} dead streams dropped")
    for station in dead:
        log(f"    dropped: {station.get('name')} -> {station.get('streamURL')}")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output path (default: {DEFAULT_OUT})")
    parser.add_argument("--per-region", type=int, default=100,
                        help="stations to keep per region (default: 100)")
    parser.add_argument("--regions", default="na,eu,ca",
                        help="comma-separated regions to fetch: na, eu, ca (default: na,eu,ca)")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="per-stream validation timeout in seconds (default: 3)")
    parser.add_argument("--no-check", action="store_true",
                        help="skip stream validation (faster, riskier catalog)")
    parser.add_argument("--merge", type=Path, default=LEGACY_DEFAULT,
                        help=f"previous catalog to carry over (default: {LEGACY_DEFAULT})")
    parser.add_argument("--no-merge", action="store_true",
                        help="do not carry over the previous catalog")
    args = parser.parse_args()

    keys: list = []
    for token in args.regions.split(","):
        key = token.strip().lower()
        if not key:
            continue
        if key not in REGIONS:
            raise SystemExit(f"Unknown region '{key}'. "
                             f"Choose from: {', '.join(sorted(REGIONS))}.")
        if key not in keys:
            keys.append(key)
    if not keys:
        raise SystemExit("No regions selected. Pass --regions na, eu and/or ca.")

    base = pick_mirror()
    validation = "off" if args.no_check else f"on, {args.timeout:.0f}s per stream"
    log(f"Fetching top {args.per_region} stations per region for "
        f"{len(keys)} region(s) (validation {validation})...")

    fetched: list = []
    for key in keys:
        label, codes = REGIONS[key]
        log(f"[{label}] searching {len(codes)} countries...")
        fetched.extend(select_region(base, label, codes, args.per_region,
                                     args.timeout, not args.no_check))

    # Pinned first entry must actually stream, or the app's own UI tests lie.
    if not args.no_check and not check_stream(PINNED_FIRST["streamURL"], args.timeout):
        raise SystemExit("Pinned 'Absolute Country Hits' stream is down; "
                          "update SwiftRadioUITests before shipping a dead pinned stream.")
    if not args.no_check and not check_stream(PINNED_LAST["streamURL"], args.timeout):
        raise SystemExit("Pinned 'MP3 file sample' file is unreachable; "
                          "update SwiftRadioUITests before shipping a dead pinned sample.")

    # Catalog order: pinned first entry, each region's top list in the order the
    # regions were requested (default North America, Europe, then Canada), the
    # carried-over previous catalog, then the pinned last entry.
    entries = [PINNED_FIRST]
    entries.extend(entry(s) for s in fetched)

    # Additions, never replacements: the previous catalog's stations join after
    # the region blocks, deduplicated against everything already chosen.
    if args.no_merge:
        log("Skipping previous-catalog additions (--no-merge).")
    elif not args.merge.exists():
        log(f"No previous catalog at {args.merge}; nothing to carry over.")
    else:
        entries.extend(merge_legacy(args.merge, entries + [PINNED_LAST],
                                     args.timeout, not args.no_check))

    entries.append(PINNED_LAST)

    countries = sorted({e["country"] for e in entries if e.get("country")})
    catalog = {"station": entries}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=4, ensure_ascii=False)
        handle.write("\n")

    log(f"\nWrote {len(entries)} stations across {len(countries)} countries to {args.out}")
    for key in keys:
        label, codes = REGIONS[key]
        count = sum(1 for e in entries if (e.get("countryCode") or "").upper() in codes)
        log(f"  {label}: {count}")
    for country in countries:
        count = sum(1 for e in entries if e.get("country") == country)
        log(f"  {country}: {count}")


if __name__ == "__main__":
    main()