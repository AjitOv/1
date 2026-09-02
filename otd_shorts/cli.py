"""Command line entry point.

  python -m otd_shorts.cli plan    --date 2026-09-03 [--days 7]   write jobs + scripts
  python -m otd_shorts.cli preview --date 2026-09-03              print the scripts
  python -m otd_shorts.cli render  --date 2026-09-03              HeyGen renders (resumable)
  python -m otd_shorts.cli upload  --date 2026-09-03              schedule on YouTube
  python -m otd_shorts.cli run     --date tomorrow                plan + render + upload
  python -m otd_shorts.cli status  --date 2026-09-03
  python -m otd_shorts.cli auth    <channel_key>                  one-time YouTube OAuth
  python -m otd_shorts.cli export  --date 2026-09-03              scripts as CSV for manual production
"""
from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Settings, load_settings
from .planner import plan_range
from .scripts import full_text
from .state import Job, Store


def _date(s: str) -> date:
    if s in ("today", "tomorrow"):
        return date.today() + timedelta(days=1 if s == "tomorrow" else 0)
    return datetime.strptime(s, "%Y-%m-%d").date()


def _store(settings: Settings) -> Store:
    return Store(settings.data_dir / "jobs.db")


def cmd_plan(a, settings: Settings) -> int:
    store = _store(settings)
    jobs = plan_range(settings, store, _date(a.date), a.days)
    print(f"planned {len(jobs)} new jobs ({a.days} day(s) x {len(settings.channels)} channels)")
    return 0


def cmd_preview(a, settings: Settings) -> int:
    store = _store(settings)
    for j in store.for_date(_date(a.date).isoformat(), channel=a.channel):
        if not j.script:
            continue
        print(f"=== {j.channel} {j.slot} [{j.format}] {j.script['title']}  ({len(full_text(j.script).split())} words)")
        print(full_text(j.script))
        print()
    return 0


def cmd_export(a, settings: Settings) -> int:
    store = _store(settings)
    day = _date(a.date).isoformat()
    out = settings.data_dir / f"scripts_{day}.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "channel", "slot", "publish_at_utc", "format", "title", "script", "description"])
        for j in store.for_date(day):
            if j.script:
                w.writerow([j.id, j.channel, j.slot, j.publish_at, j.format, j.script["title"],
                            full_text(j.script), j.script["description"]])
    print(f"wrote {out}")
    return 0


def _render_one(settings: Settings, store: Store, job: Job) -> None:
    """Render one job with the backend named by OTD_RENDERER (heygen, default, or hedra)."""
    import os
    backend = (getattr(_render_one, "backend", None) or os.environ.get("OTD_RENDERER", "heygen")).lower()
    ch = settings.channel(job.channel)
    dest = settings.data_dir / "videos" / job.date / f"{job.id}.mp4"
    try:
        if backend == "local":
            from .local_render import LocalRenderer, LocalRenderError as RenderError
            store.update(job.id, status="rendering")
            LocalRenderer(settings).render(ch, job.script, job.id, dest)
        elif backend == "hedra":
            from .hedra import Hedra, HedraError as RenderError
            hd = Hedra()
            store.update(job.id, status="rendering")
            portrait = settings.secrets_dir / "portraits" / f"{ch.key}.jpg"
            url = hd.render(ch, portrait, full_text(job.script), job.id)
            hd.download(url, dest)
        else:
            from .heygen import HeyGen, HeyGenError as RenderError
            hg = HeyGen()
            vid = job.heygen_video_id
            if not vid:
                vid = hg.submit(ch, full_text(job.script), f"{job.id} {job.script['title']}")
                store.update(job.id, heygen_video_id=vid, status="rendering")
            hg.download(hg.wait(vid), dest)
        store.update(job.id, video_path=str(dest), status="rendered", error=None)
        print(f"rendered {job.id}")
    except RenderError as exc:
        store.update(job.id, status="failed", error=str(exc))
        print(f"FAILED {job.id}: {exc}", file=sys.stderr)


def cmd_render(a, settings: Settings) -> int:
    if a.renderer:
        _render_one.backend = a.renderer
    store = _store(settings)
    day = _date(a.date).isoformat()
    todo = [j for j in store.for_date(day, channel=a.channel)
            if j.script and j.status in ("scripted", "rendering", "failed" if a.retry else "scripted")]
    if a.limit:
        todo = todo[: a.limit]
    print(f"rendering {len(todo)} job(s) with {a.parallel} worker(s)")
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        list(ex.map(lambda j: _render_one(settings, store, j), todo))
    print(store.summary(day))
    return 0


def cmd_upload(a, settings: Settings) -> int:
    from .youtube import upload_short
    store = _store(settings)
    day = _date(a.date).isoformat()
    todo = [j for j in store.for_date(day, channel=a.channel, status="rendered")]
    if a.limit:
        todo = todo[: a.limit]
    print(f"uploading {len(todo)} video(s)")
    for j in todo:
        ch = settings.channel(j.channel)
        try:
            yt_id = upload_short(settings.secrets_dir / ch.token_file, Path(j.video_path),
                                 j.script["title"], j.script["description"], j.publish_at,
                                 settings.brand.get("hashtags", []))
            store.update(j.id, youtube_video_id=yt_id, status="uploaded", error=None)
            print(f"scheduled {j.id} -> https://youtube.com/shorts/{yt_id} at {j.publish_at}")
        except Exception as exc:  # noqa: BLE001 - keep going with the other channels
            store.update(j.id, error=f"upload: {exc}")
            print(f"UPLOAD FAILED {j.id}: {exc}", file=sys.stderr)
    print(store.summary(day))
    return 0


def cmd_run(a, settings: Settings) -> int:
    a.days = 1
    cmd_plan(a, settings)
    cmd_render(a, settings)
    return cmd_upload(a, settings)


def cmd_status(a, settings: Settings) -> int:
    store = _store(settings)
    day = _date(a.date).isoformat()
    for j in store.for_date(day, channel=a.channel):
        extra = j.youtube_video_id or j.heygen_video_id or ""
        print(f"{j.id:<40} {j.status:<10} {j.publish_at} {extra} {j.error or ''}")
    print(store.summary(day))
    return 0


def cmd_auth(a, settings: Settings) -> int:
    from .youtube import authorize
    ch = settings.channel(a.channel)
    cid = authorize(settings.secrets_dir / ch.oauth_client, settings.secrets_dir / ch.token_file)
    print(f"authorised channel {ch.key}: youtube_channel_id={cid}. Put that id in config/channels.yaml.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="otd_shorts")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--date", default="tomorrow")
        sp.add_argument("--channel", default=None)
        sp.add_argument("--limit", type=int, default=0)
        sp.add_argument("--parallel", type=int, default=4)
        sp.add_argument("--retry", action="store_true", help="also retry failed renders")
        sp.add_argument("--renderer", default=None, choices=["heygen", "hedra", "local"], help="override OTD_RENDERER")

    sp = sub.add_parser("plan"); common(sp); sp.add_argument("--days", type=int, default=1); sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("preview"); common(sp); sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("export"); common(sp); sp.set_defaults(fn=cmd_export)
    sp = sub.add_parser("render"); common(sp); sp.set_defaults(fn=cmd_render)
    sp = sub.add_parser("upload"); common(sp); sp.set_defaults(fn=cmd_upload)
    sp = sub.add_parser("run"); common(sp); sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("status"); common(sp); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("auth"); sp.add_argument("channel"); sp.set_defaults(fn=cmd_auth)

    a = p.parse_args(argv)
    return a.fn(a, load_settings())


if __name__ == "__main__":
    sys.exit(main())
