# OTD Shorts

Plans, scripts, renders and schedules **4 Shorts per day on 11 YouTube channels** (44 videos/day)
promoting the OTD System course at https://otdsystem.com.

```
config/          brand, 11 channels, 132-topic bank (12 per channel pillar)
otd_shorts/      planner -> scripts -> renderer (HeyGen or Hedra) -> YouTube scheduler
.github/         nightly workflow that produces tomorrow's 44 videos
```

## How it works

1. **Plan** (`plan`): for each channel and each posting slot (08:00, 12:30, 17:00, 21:00 IST by default)
   pick a topic + format pair the channel has never used. Each channel has its own pillar and persona
   (price action, risk, psychology, options, intraday, swing, investing, mistakes, history, routine,
   the system itself) so the 44 daily videos are not near-duplicates. 12 topics x 6 formats = 72
   unique videos per channel before anything repeats (18 days), and repeats get a different hook/CTA.
2. **Script**: 75-115 spoken words (30-45 s). Hook, one lesson, one example, the common mistake,
   a CTA to otdsystem.com. Every script is linted against `banned_phrases` (guaranteed, risk-free,
   get rich...). With `ANTHROPIC_API_KEY` set, Claude writes the script; otherwise the offline
   template engine does. Both produce the same JSON shape.
3. **Render** (`render`): 9:16 avatar video with burned-in captions.
   * `OTD_RENDERER=heygen` (default): your HeyGen "Ajit Reels" avatar group. Channel -> look mapping
     is in `config/channels.yaml` (two channels use the digital twin, the rest use photo looks).
   * `OTD_RENDERER=hedra`: TTS + `hedra-avatar` from a portrait photo per channel
     (`secrets/portraits/<channel_key>.jpg`).
   * `OTD_RENDERER=local` (**free, no keys**): Edge neural voice (a different one per channel) +
     word-by-word highlighted captions + animated per-channel gradient, assembled by ffmpeg into
     1080x1920. Optional: `PEXELS_API_KEY` swaps the gradient for darkened portrait stock b-roll;
     `OTD_MUSIC_DIR` mixes a music bed at 10%. `OTD_TTS=piper` + `OTD_PIPER_MODEL=<voice.onnx>`
     makes it fully offline (voices: github.com/rhasspy/piper-voices). No talking head in this mode.
4. **Upload** (`upload`): each video is uploaded **private with `publishAt`** set to its slot, so
   YouTube releases it on schedule. Title/description carry `#Shorts`, the course link and the
   risk disclaimer. Category: Education.

Everything is tracked in `data/jobs.db`; every stage is idempotent, so a crashed run can simply be
re-run (`--retry` re-attempts failed renders).

## Quick start

macOS: `git clone https://github.com/AjitOv/1.git otd-shorts && cd otd-shorts && bash scripts/setup_mac.sh`
(installs Homebrew if missing, Python 3, ffmpeg, a virtualenv and the requirements). Then
`source .venv/bin/activate` in every new terminal.

Other systems:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in keys, then `export $(cat .env | xargs)`
python -m otd_shorts.cli plan --date tomorrow          # writes 44 jobs + scripts
python -m otd_shorts.cli preview --date tomorrow       # read the scripts
python -m otd_shorts.cli export --date tomorrow        # CSV if you want to record manually
python -m otd_shorts.cli render --date tomorrow        # HeyGen/Hedra renders (parallel 4)
python -m otd_shorts.cli upload --date tomorrow        # schedule on YouTube
python -m otd_shorts.cli run --date tomorrow           # all three
python -m otd_shorts.cli status --date tomorrow
python -m pytest -q                                    # tests
```

## YouTube setup (once per channel)

1. Google Cloud Console -> create a project -> enable **YouTube Data API v3** -> OAuth consent screen
   (External, add your Google account as test user) -> Credentials -> **OAuth client ID, Desktop app**
   -> download as `secrets/client_secret.json`.
2. For each channel: `python -m otd_shorts.cli auth <channel_key>` and log in with the Google account
   that owns that channel (pick the brand channel if asked). This writes `secrets/yt_<key>.json` and
   prints the channel id; paste it into `youtube_channel_id` in `config/channels.yaml`.
**Already have a Google Cloud project with the channels authorised?** Copy its `client_secret.json`
into `secrets/`, then for each channel import the existing token instead of re-authorising:
`python -m otd_shorts.cli import-token price_action ~/path/to/that/token.json` (JSON or pickle
credentials with a refresh_token are accepted). `python -m otd_shorts.cli whoami --write` prints
which YouTube channel each token maps to and saves the ids into `channels.yaml`.

3. **Quota.** One `videos.insert` costs 1,600 units; a new project gets 10,000 units/day = **6
   uploads/day per project**. For 44/day you need one of:
   * a quota extension (Google's *YouTube API Services - Audit and Quota Extension* form), or
   * up to 8 projects, each with its own `client_secret_<n>.json`, mapped per channel via
     `oauth_client:` in `channels.yaml` (2 channels x 4 videos = 8 uploads < 10,000 units, so
     **6 projects covers all 11 channels**).
4. Unverified OAuth apps: uploads from an app that has not passed verification are set to
   **private** by YouTube regardless of `privacyStatus`. Complete OAuth verification (or the quota
   audit, which covers it) before relying on scheduled publishing.

## Nightly automation

`.github/workflows/daily.yml` runs at 20:00 UTC (01:30 IST) and produces the next day's 44 videos.
Secrets to add in the GitHub repo:

| secret | value |
|---|---|
| `HEYGEN_API_KEY` | HeyGen -> Settings -> API |
| `ANTHROPIC_API_KEY` | optional, better scripts |
| `YT_SECRETS_TAR_B64` | `tar -cz -C secrets . \| base64 -w0` (client secrets + yt tokens + portraits) |

## Cost per day (44 videos, ~40 s each) - approximate, from the providers' current price lists

| renderer | per video | per day | per month |
|---|---|---|---|
| **local (Edge TTS + ffmpeg, faceless)** | **$0** | **$0** | **$0** |
| Hedra avatar 540p + MiniMax TTS | ~$1.05 | ~$46 | ~$1,400 |
| Hedra avatar 720p + MiniMax TTS | ~$2.05 | ~$90 | ~$2,700 |
| HeyGen API | depends on API plan credits (roughly $1-3 per minute of avatar video) | | |
| Claude scripts (claude-opus-5, ~1.5k tokens each) | ~$0.02 | ~$1 | ~$30 |

Right now all three connected avatar accounts (HeyGen, Higgsfield, Hedra) are on free plans with
**zero balance**. The `local` renderer needs none of them: `OTD_RENDERER=local python -m otd_shorts.cli run`
produces all 44 videos for free (about 20-40 s of CPU each).

## Risks you should know before turning this on

* **YouTube spam policy.** Mass-produced, repetitive content across many channels is an explicit
  termination reason, and channels owned by the same account are usually terminated together.
  The per-channel pillars, different avatar looks, rotating hooks/CTAs and the Claude generator all
  exist to keep the 11 channels distinct. Still ramp up: start at 1-2 Shorts/day per channel for
  two weeks and watch for "reused content" or "spam" flags before going to 4.
* **Financial-content rules.** Scripts never give buy/sell calls on named securities and never
  promise returns; the description carries a risk disclaimer. Keep it that way, especially with
  a $5,999 course link, or expect "misleading claims" strikes.
* **Faceless + AI voice is the format YouTube scrutinises most.** The local renderer gives every
  channel its own voice, colours and b-roll query for that reason. Consider recording yourself for
  the flagship channel and using the free renderer for the other ten.
* **Avatar disclosure.** YouTube requires the "altered or synthetic content" flag for realistic AI
  avatars. Tick it in Studio (the API does not expose it yet) or expect the label to be applied for you.
* **Verify Hedra endpoints.** The Hedra client follows the v3 schema (`/v3/files`, `/v3/models/{id}`,
  `/v3/jobs/{id}`); confirm the base URL and auth header for your account via `HEDRA_API_BASE` /
  `HEDRA_AUTH_HEADER` on the first run.
