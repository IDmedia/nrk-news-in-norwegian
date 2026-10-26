#!/usr/bin/env python3
"""Build Alexa Flash Briefing feeds with the latest NRK local news.

NRK stopped publishing the regional "Distriktsnyheter" bulletins as separate
episodes on 31 Jan 2025. The bulletins still air inside each region's radio
show, where NRK marks them with chapter markers ("indexPoints") such as
"Lokale nyheter" or "Nyheter fra Buskerud". This script finds the newest
finished news chapter for each region, cuts it out of NRK's HLS stream into an
MP3 that meets Amazon's audio requirements, and writes a Flash Briefing JSON
feed per region:

    <out>/<region>/feed.json
    <out>/<region>/<program>-<start>.mp3

Each MP3 starts with a short spoken intro saying when the bulletin aired
("NRK Sørlandet, mandag klokka sju førtifire."), made with edge-tts.

Serve <out> over HTTPS at --base-url and run this every 15 minutes.
Needs Python 3.9+, ffmpeg and, for the intro, edge-tts (pip install edge-tts).
Without edge-tts the bulletins are published without the intro.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

API = "https://psapi.nrk.no"
OSLO = ZoneInfo("Europe/Oslo")

REGIONS = {
    "sorlandet": ("NRK Sørlandet", "distriktsprogram-soerlandet"),
    "buskerud": ("NRK Buskerud", "distriktsprogram-buskerud"),
    "finnmark": ("NRK Finnmark", "distriktsprogram-finnmark"),
    "hordaland": ("NRK Vestland, Hordaland", "distriktsprogram-hordaland"),
    "innlandet": ("NRK Innlandet", "nrk-innlandet"),
    "more": ("NRK Møre og Romsdal", "distriktsprogram-moere-og-romsdal-radio"),
    "nordland": ("NRK Nordland", "distriktsprogram-nordland"),
    "oslo": ("NRK Stor-Oslo", "distriktsprogram-oestlandssendingen"),
    "rogaland": ("NRK Rogaland", "distriktsprogram-rogaland"),
    "sogn": ("NRK Vestland, Sogn og Fjordane", "distriktsprogram-sogn-og-fjordane"),
    "telemark": ("NRK Telemark", "distriktsprogram-telemark"),
    "troms": ("NRK Troms", "distriktsprogram-troms"),
    "trondelag": ("NRK Trøndelag", "distriktsprogram-troendelag"),
    "vestfold": ("NRK Vestfold", "distriktsprogram-vestfold"),
    "ostfold": ("NRK Østfold", "distriktsprogram-oestfold"),
}
# Hourly national bulletin ("NRK Nyheter"), about 3 minutes, published as its own episodes.
NATIONAL = ("NRK Nyheter", "nrk-nyheter-radio")

# Chapter titles that count as a local news bulletin. Each region names them its own
# way: "Lokale nyheter", "Nyheter fra Buskerud", "Nyheiter, ver og trafikk",
# "Nyhetsoversikt", "Finnmarksnytt", "Nytt fra Nordland", "Lokalnytt 07:03"...
# ("nytt" alone is too broad: "nytt album", "Musikknytt".)
NEWS_TITLE = re.compile(r"nyhe(i)?t|^nytt fra|(lokal|finnmarks)nytt", re.I)
NOT_NEWS_TITLE = re.compile(r"quiz", re.I)

EPISODES_TO_CHECK = 6
MIN_LENGTH_SEC = 60
DEFAULT_LENGTH_SEC = 300

DEFAULT_VOICE = "nb-NO-FinnNeural"
INTRO_PAUSE_SEC = 0.3  # Extra silence between the intro and the bulletin.
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
NUMBERS = ["null", "en", "to", "tre", "fire", "fem", "seks", "sju", "åtte", "ni", "ti",
           "elleve", "tolv", "tretten", "fjorten", "femten", "seksten", "sytten", "atten", "nitten"]
TENS = ["", "", "tjue", "tretti", "førti", "femti"]

log = logging.getLogger("nrk_flash")


# ---------- NRK API ----------

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "nrk-flash-briefing/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def get_json(path):
    return json.loads(get(path if path.startswith("http") else API + path))


def iso_duration(s):
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$", s or "")
    if not m:
        return 0.0
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)


def latest_episodes(series, count):
    data = get_json(f"/radio/catalog/series/{series}/episodes?pageSize={count}&sort=desc")
    return data.get("_embedded", {}).get("episodes", [])


def playable_url(program_id):
    """HLS master playlist URL, or None if NRK doesn't allow playback yet."""
    manifest = get_json(f"/playback/manifest/program/{program_id}")
    if manifest.get("playability") != "playable" or not manifest.get("playable"):
        return None
    for asset in manifest["playable"]["assets"]:
        if asset.get("format") == "HLS":
            return asset["url"]
    return None


def share_url(ep, series):
    href = ep.get("_links", {}).get("share", {}).get("href")
    return href or f"https://radio.nrk.no/serie/{series}/{ep['episodeId']}"


def find_local_news(series, max_length, now):
    """Newest finished local news chapter in a region's radio show, or None."""
    for ep in latest_episodes(series, EPISODES_TO_CHECK):
        start = datetime.fromisoformat(ep["date"])
        if start > now:
            continue
        program = get_json(f"/radio/catalog/programs/{ep['episodeId']}")
        points = [(p["title"], iso_duration(p["startPoint"])) for p in program.get("indexPoints") or []]
        program_length = (program.get("duration") or {}).get("seconds") or ep.get("durationInSeconds") or 0
        elapsed = (now - start).total_seconds()

        for i in range(len(points) - 1, -1, -1):
            title, ch_start = points[i]
            if not NEWS_TITLE.search(title) or NOT_NEWS_TITLE.search(title):
                continue
            nxt = points[i + 1][1] if i + 1 < len(points) else (program_length or ch_start + DEFAULT_LENGTH_SEC)
            length = min(max_length, max(MIN_LENGTH_SEC, nxt - ch_start))
            if ch_start + length > elapsed:
                continue  # Still airing.
            master = playable_url(ep["episodeId"])
            if not master:
                continue
            return {
                "program_id": ep["episodeId"],
                "chapter": title,
                "aired": start + timedelta(seconds=ch_start),
                "master": master,
                "start": ch_start,
                "end": ch_start + length,
                "link": share_url(ep, series),
            }
    return None


def find_national_news(now):
    series = NATIONAL[1]
    for ep in latest_episodes(series, 4):
        start = datetime.fromisoformat(ep["date"])
        if start > now:
            continue
        master = playable_url(ep["episodeId"])
        if not master:
            continue
        return {
            "program_id": ep["episodeId"],
            "chapter": ep["titles"]["title"],
            "aired": start,
            "master": master,
            "start": 0.0,
            "end": None,
            "link": share_url(ep, series),
        }
    return None


# ---------- intro ----------

def number_words(n):
    if n < 20:
        return NUMBERS[n]
    return TENS[n // 10] + (NUMBERS[n % 10] if n % 10 else "")


def intro_text(name, aired):
    """For example "NRK Sørlandet, mandag klokka sju førtifire."

    The time is written out in words so the voice can't read 07.44 as a date or a decimal.
    """
    t = aired.astimezone(OSLO)
    hour = "ett" if t.hour == 1 else number_words(t.hour)
    if t.minute == 0:
        clock = hour
    elif t.minute < 10:
        clock = f"{hour} null {number_words(t.minute)}"
    else:
        clock = f"{hour} {number_words(t.minute)}"
    return f"{name}, {WEEKDAYS[t.weekday()]} klokka {clock}."


def make_intro(text, voice, dest):
    """Speaks the text with edge-tts (Microsoft's online voices) into an MP3."""
    import edge_tts  # Optional; only needed for the intro.

    asyncio.run(edge_tts.Communicate(text, voice).save(dest))


# ---------- audio ----------

def media_playlist(master_url):
    """Picks the highest-bandwidth variant and returns (media_url, [(duration, segment_url)])."""
    text = get(master_url).decode()
    lines = text.splitlines()
    best = None
    for i, line in enumerate(lines):
        m = re.match(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)", line)
        if m and i + 1 < len(lines) and not lines[i + 1].startswith("#"):
            if not best or int(m.group(1)) > best[0]:
                best = (int(m.group(1)), lines[i + 1].strip())
    media_url = urljoin(master_url, best[1]) if best else master_url
    if best:
        lines = get(media_url).decode().splitlines()
    segments = []
    for i, line in enumerate(lines):
        m = re.match(r"#EXTINF:([\d.]+)", line)
        if m:
            segments.append((float(m.group(1)), urljoin(media_url, lines[i + 1].strip())))
    return media_url, segments


def make_mp3(clip, dest, intro=None, voice=DEFAULT_VOICE):
    """Downloads the HLS segments covering the clip and encodes an Alexa-ready MP3,
    starting with the spoken intro text if one is given."""
    _, segments = media_playlist(clip["master"])
    t = 0.0
    first_start = None
    with tempfile.TemporaryDirectory() as tmp:
        ts_path = os.path.join(tmp, "clip.ts")
        with open(ts_path, "wb") as f:
            for dur, url in segments:
                seg_start, t = t, t + dur
                if t <= clip["start"]:
                    continue
                if clip["end"] is not None and seg_start >= clip["end"]:
                    break
                if first_start is None:
                    first_start = seg_start
                f.write(get(url))
        if first_start is None:
            raise RuntimeError("No audio segments in range")

        intro_path = None
        if intro:
            intro_path = os.path.join(tmp, "intro.mp3")
            try:
                make_intro(intro, voice, intro_path)
            except Exception as e:  # Better a bulletin without the intro than none.
                log.warning("intro failed, encoding without it: %s", e)
                intro_path = None

        offset = clip["start"] - first_start
        length = (clip["end"] - clip["start"]) if clip["end"] is not None else None
        # Amazon: 256 kbps MP3, about -14 LUFS, true peak at most -2 dBFS. Short fades
        # so the cut doesn't click. The intro is normalized on its own so the voice
        # comes out as loud as the bulletin.
        norm = "loudnorm=I=-14:TP=-2:LRA=11,aresample=44100,aformat=channel_layouts=stereo"
        news = ["afade=t=in:d=0.5"]
        if length:
            news.append(f"afade=t=out:st={max(0, length - 1.5):.2f}:d=1.5")
        news.append(norm)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.3f}"]
        if length:
            cmd += ["-t", f"{length:.3f}"]
        cmd += ["-i", ts_path]
        if intro_path:
            cmd += ["-i", intro_path, "-filter_complex",
                    f"[1:a]{norm},apad=pad_dur={INTRO_PAUSE_SEC}[intro];"
                    f"[0:a]{','.join(news)}[news];"
                    "[intro][news]concat=n=2:v=0:a=1[out]",
                    "-map", "[out]"]
        else:
            cmd += ["-af", ",".join(news)]
        tmp_mp3 = dest + ".part"
        cmd += ["-ar", "44100", "-ac", "2",
                "-c:a", "libmp3lame", "-b:a", "256k", "-f", "mp3", tmp_mp3]
        subprocess.run(cmd, check=True)
        os.replace(tmp_mp3, dest)


# ---------- feed ----------

def write_json(path, data):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_region(key, name, clip, out_dir, base_url, voice=DEFAULT_VOICE):
    region_dir = os.path.join(out_dir, key)
    os.makedirs(region_dir, exist_ok=True)
    filename = f"{clip['program_id']}-{int(clip['start'])}.mp3"
    mp3_path = os.path.join(region_dir, filename)

    if os.path.exists(mp3_path):
        log.info("%s: %s already done", key, filename)
    else:
        log.info("%s: encoding %s (%s, %s)", key, filename, clip["chapter"], clip["aired"].astimezone(OSLO))
        intro = intro_text(name, clip["aired"]) if voice else None
        make_mp3(clip, mp3_path, intro, voice)

    feed_path = os.path.join(region_dir, "feed.json")
    previous = None
    if os.path.exists(feed_path):
        with open(feed_path, encoding="utf-8") as f:
            previous = os.path.basename(json.load(f).get("streamUrl", ""))

    aired_local = clip["aired"].astimezone(OSLO)
    item = {
        "uid": f"urn:nrk:{clip['program_id']}:{int(clip['start'])}",
        "updateDate": clip["aired"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0Z"),
        "titleText": f"{name} {aired_local:%d.%m %H:%M}",
        "mainText": "",
        "streamUrl": f"{base_url}/{key}/{filename}",
        "redirectionUrl": clip["link"],
    }
    write_json(feed_path, item)

    # Keep the current MP3 and the one the previous feed pointed to (Alexa may still
    # have that feed cached). Based on the feed rather than file times, because CI
    # checkouts give every file a fresh timestamp.
    keep = {filename, previous}
    for f in os.listdir(region_dir):
        if f.endswith(".mp3") and f not in keep:
            os.remove(os.path.join(region_dir, f))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="public", help="output directory (default: public)")
    ap.add_argument("--base-url", required=True, help="public HTTPS URL where --out is served")
    ap.add_argument("--regions", default="all",
                    help=f"comma-separated: {','.join(REGIONS)},national or 'all' (default)")
    ap.add_argument("--max-length", type=int, default=480,
                    help="longest clip in seconds; a chapter runs to the next marker (default 480)")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help=f"edge-tts voice for the intro (default {DEFAULT_VOICE}, or nb-NO-PernilleNeural)")
    ap.add_argument("--no-intro", action="store_true", help="don't add the spoken date/time intro")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    keys = list(REGIONS) + ["national"] if args.regions == "all" else [k.strip() for k in args.regions.split(",")]
    unknown = [k for k in keys if k not in REGIONS and k != "national"]
    if unknown:
        ap.error(f"unknown region(s): {', '.join(unknown)}")

    base_url = args.base_url.rstrip("/")
    now = datetime.now(timezone.utc)
    failed = 0
    for key in keys:
        try:
            if key == "national":
                name, clip = NATIONAL[0], find_national_news(now)
            else:
                name, series = REGIONS[key]
                clip = find_local_news(series, args.max_length, now)
            if clip:
                build_region(key, name, clip, args.out, base_url, None if args.no_intro else args.voice)
            else:
                # Keep the previous feed; Alexa ignores items older than 7 days.
                log.warning("%s: no local news chapter found, feed unchanged", key)
        except Exception as e:  # One region failing shouldn't stop the others.
            failed += 1
            log.error("%s: %s", key, e)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
