# NRK News in Norwegian – NRK local news in your Alexa Flash Briefing

An open-source replacement for NRK's Alexa flash briefing, which now only says
*"Sorry, the news in Norwegian are unavailable"*. Say **"Alexa, flash me"** (or "what's my flash briefing")
and hear the latest local news from your NRK region, for example NRK Sørlandet.

Published with written permission from NRK. Not an official NRK product.

## Why the NRK skill broke

NRK used to publish each regional news bulletin ("Distriktsnyheter") as its own short episode.
The last one for Sørlandet came out on **31 January 2025**, and the others stopped around the same time,
so the old feed has had nothing to play since then.

The bulletins still air. They are now part of each region's 2–3 hour radio show, and NRK marks them with
chapter markers such as *"Lokale nyheter"*, *"Nyheter fra Buskerud"*, *"Finnmarksnytt"* or *"Lokalnytt 07:03"*.

## How it works

[`nrk_flash.py`](nrk_flash.py) runs every 15 minutes. For each region it:

1. Looks through the latest episodes of the region's show in NRK's radio API (`psapi.nrk.no`) and finds the
   newest news chapter that has finished airing.
2. Downloads just that part of NRK's stream and uses ffmpeg to make an MP3 that meets Amazon's requirements:
   256 kbps, about −14 LUFS, true peak at most −2 dBFS, with short fades in and out.
   The MP3 starts with a short spoken intro saying when the bulletin aired, for example
   *"NRK Sørlandet, fredag klokka sju førtifire"*, so you can tell whether it's current.
   The intro is read by Microsoft's `nb-NO-FinnNeural` voice through [edge-tts](https://github.com/rany2/edge-tts).
3. Writes a Flash Briefing feed to `<region>/feed.json` that points to the MP3.

A bulletin that is already encoded isn't processed again, so a run with nothing new takes under a second.
Each region keeps only the current MP3 and the previous one, in case Alexa still has the old feed cached.

| Feed (`--regions` key) | NRK channel | Local news found? |
|---|---|---|
| `sorlandet` | NRK Sørlandet | ✅ |
| `buskerud` | NRK Buskerud | ✅ |
| `finnmark` | NRK Finnmark | ✅ |
| `hordaland` | NRK Vestland (Hordaland) | ✅ |
| `innlandet` | NRK Innlandet | ✅ |
| `more` | NRK Møre og Romsdal | ✅ |
| `nordland` | NRK Nordland | ✅ |
| `oslo` | NRK Stor-Oslo | ✅ |
| `sogn` | NRK Vestland (Sogn og Fjordane) | ✅ |
| `troms` | NRK Troms | ✅ |
| `ostfold` | NRK Østfold | ✅ |
| `national` | NRK Nyheter (hourly national bulletin) | ✅ |
| `rogaland`, `telemark`, `trondelag`, `vestfold` | – | ❌ no chapter markers yet |

This was tested in October 2026. It depends on NRK's editors adding chapter markers, so coverage can change.
Leave out regions without markers when you set up the skill.

## 1. Run the feed generator

You need Python 3.9 or newer and ffmpeg. For the spoken intro you also need edge-tts:

```bash
pip install edge-tts
```

Without it, or if Microsoft's voice service can't be reached, the bulletin is published without the intro.

```bash
python3 nrk_flash.py --out public --base-url https://nrk-flash.example.no -v
```

| Option | Meaning |
|---|---|
| `--out` | Folder to write feeds and MP3s to (default `public`) |
| `--base-url` | Public HTTPS address where that folder is served. Amazon must be able to reach it. |
| `--regions` | For example `sorlandet,national`. Default: all regions. |
| `--max-length` | Longest clip in seconds (default 480). A chapter runs until the next marker, which can include a jingle or the start of a song. |
| `--voice` | edge-tts voice for the intro (default `nb-NO-FinnNeural`; `nb-NO-PernilleNeural` is the female voice) |
| `--no-intro` | Leave out the spoken intro |
| `-v` | Show what it does |

The script exits with code 1 if any region failed, but it still updates the others.

### How this repo publishes the feeds

The feeds are built on GitLab (`git.int.idmedia.no/idmedia/nrk-news-in-norwegian`) and served from GitHub Pages:

1. A scheduled GitLab pipeline ([`.gitlab-ci.yml`](.gitlab-ci.yml)) runs every 15 minutes.
2. [`deploy/publish-feeds.sh`](deploy/publish-feeds.sh) builds the feeds and force-pushes them to the `feeds` branch.
   The branch always holds a single commit, so old MP3s don't pile up in the history.
3. The GitLab push mirror copies `feeds` to [github.com/IDmedia/nrk-news-in-norwegian](https://github.com/IDmedia/nrk-news-in-norwegian).
4. GitHub Pages serves the branch at `https://idmedia.github.io/nrk-news-in-norwegian/`.

One-time setup:

1. **GitLab → Settings → Access tokens:** create a project access token with role **Developer** and scope
   **write_repository**.
2. **GitLab → Settings → CI/CD → Variables:** add it as `PUSH_TOKEN` (masked, not protected,
   or protected if your schedule runs on a protected branch).
3. **GitLab → Build → Pipeline schedules:** new schedule, interval `*/15 * * * *`, target branch `main`.
   Click the play button once to run it now.
4. **GitLab → Settings → Repository → Mirroring repositories:** make sure the `feeds` branch is mirrored.
   If *Mirror only protected branches* is on, protect `feeds` and allow force push for Maintainers.
   Leave *Keep divergent refs* off, so the force-pushed branch replaces the old one on GitHub.
5. **GitHub → Settings → Pages:** source **Deploy from a branch**, branch **`feeds`**, folder **`/ (root)`**.

To build the feeds somewhere else, change `BASE_URL` in `.gitlab-ci.yml`.

### Alternative – your own web server

You can also run the script with cron and serve the output folder with nginx.
See [`deploy/nrk-flash.cron`](deploy/nrk-flash.cron) and [`deploy/nginx.conf`](deploy/nginx.conf).
`.json` files must be served as `application/json`.

### Check it

Open <https://idmedia.github.io/nrk-news-in-norwegian/sorlandet/feed.json> in a browser. It should look like this:

```json
{
  "uid": "urn:nrk:DKSL01020526:6061",
  "updateDate": "2026-10-23T05:44:01.0Z",
  "titleText": "NRK Sørlandet 23.10 07:44",
  "mainText": "",
  "streamUrl": "https://idmedia.github.io/nrk-news-in-norwegian/sorlandet/DKSL01020526-6061.mp3",
  "redirectionUrl": "https://radio.nrk.no/serie/distriktsprogram-soerlandet/DKSL01020526"
}
```

Then open the `streamUrl` and check that the clip starts and ends in a sensible place.

## 2. Create the Flash Briefing skill

1. In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask), click **Create Skill**.
   Name: `Norway News Flash`. Locale: English (UK) and/or English (US), whichever your Echo uses.
2. Choose **Flash Briefing** as the model.
3. **Custom error message:** for example `Sorry, the local news from NRK is unavailable right now.`
4. Click **Add new feed** for each region you want, for example:

   | Field | Value |
   |---|---|
   | Preamble | `From NRK Sørlandet` (must start with "In" or "From", up to 70 characters) |
   | Name | `Local news from NRK Sørlandet` |
   | Content update frequency | Hourly |
   | Content type | Audio |
   | Content genre | Headline News |
   | Feed | `https://idmedia.github.io/nrk-news-in-norwegian/sorlandet/feed.json` |
   | Feed icon | [`icons/sorlandet.png`](icons/sorlandet.png) (512 × 512) |

   The first feed you add becomes the default, and the default is always on. Make Sørlandet the default if that's
   what most of your users want, or the `national` feed if you'd rather everyone get something.
5. Click **Save**. If you have two locales, add the same feeds for the other one too.

### Test it on your own Echo

1. On the **Test** tab, set *Skill testing is enabled in* to **Development**.
2. In the Alexa app, go to **More → Settings → News** (or **Flash Briefing**). Turn on your feeds and turn off the old NRK one.
3. Say **"Alexa, flash me"** or **"Alexa, what's my flash briefing"**.

## 3. Share and publish

- **Family on other Amazon accounts:** **Distribution → Availability → Beta test**. Add the email address of their
  Amazon account. They get a link to enable the skill and choose feeds in the Alexa app. A beta lasts 90 days and can be restarted.
- **Skills Store:** fill in **Distribution** (descriptions, [`icons/skill-108.png`](icons/skill-108.png) and
  [`icons/skill-512.png`](icons/skill-512.png), privacy questions), then submit for certification.
  Have NRK's written permission ready, since certification can ask for proof of rights to the content.

## Limitations

- **GitHub Pages limits.** GitHub Pages has a soft limit of 100 GB traffic per month. Each bulletin is
  about 10 MB at 256 kbps, so that's roughly 10,000 plays a month. If the skill gets more popular than that,
  move the output to object storage or a CDN by changing `BASE_URL` and where the files are copied.
- **Chapter length is a guess.** The clip runs to the next chapter marker, at most `--max-length` seconds.
- **Missing markers.** Some editors add news markers only on some days (NRK Sørlandet skips them for weeks).
  For regions listed in `SCHEDULE` in `nrk_flash.py`, an episode without news markers falls back to the fixed
  bulletin times (Sørlandet: 06:57 and 07:44, 6 minutes each).
- **Weekends.** Regional shows run on weekdays, so the feed keeps Friday's last bulletin over the weekend.
  Alexa ignores items older than 7 days.
- **Delay.** A bulletin is available once its chapter has finished and NRK has published the episode,
  plus up to 15 minutes for the cron job and 15 minutes for Amazon to fetch the feed.

## License

MIT. Not affiliated with or endorsed by NRK. NRK's content belongs to NRK and is used with permission.
