"""YouTube Data API v3 uploads, one OAuth token per channel.

Quota reality: videos.insert costs 1,600 units and a fresh Google Cloud project gets 10,000
units/day, i.e. 6 uploads/day. 44 uploads/day needs either a quota extension (YouTube API
Services audit form) or several projects; `oauth_client` in channels.yaml lets each channel
upload through a different project's client secret."""
from __future__ import annotations

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
CATEGORY_EDUCATION = "27"
UPLOAD_COST_UNITS = 1600


def _google():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    return Request, Credentials, InstalledAppFlow, build, MediaFileUpload


def authorize(client_secret: Path, token_file: Path) -> str:
    """Interactive one-time OAuth for a channel. Returns the channel id it authorised."""
    _, _, InstalledAppFlow, build, _ = _google()
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=False)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    me = yt.channels().list(part="id,snippet", mine=True).execute()
    item = me["items"][0]
    return item["id"]


def _service(token_file: Path):
    Request, Credentials, _, build, _ = _google()
    if not token_file.exists():
        raise FileNotFoundError(f"no OAuth token at {token_file}; run `cli auth <channel>` first")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_short(token_file: Path, video_path: Path, title: str, description: str,
                 publish_at: str, tags: list[str]) -> str:
    """Upload as private with a scheduled publishAt. Returns the YouTube video id.
    Vertical + under 60 s + #Shorts in title/description is what makes YouTube treat it as a Short."""
    _, _, _, _, MediaFileUpload = _google()
    yt = _service(token_file)
    if "#shorts" not in (title + description).lower():
        description += "\n#Shorts"
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": [t.lstrip("#") for t in tags][:30],
            "categoryId": CATEGORY_EDUCATION,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def import_token(src: Path, client_secret: Path | None, token_file: Path) -> None:
    """Convert an existing Google OAuth credential into our token file.
    Accepts: google-auth 'authorized_user' JSON, a token.json/credentials JSON with a refresh_token,
    or a token.pickle written by google-auth. Missing client_id/secret are taken from client_secret."""
    import json
    import pickle
    cid = csec = None
    if client_secret and client_secret.exists():
        cs = json.loads(client_secret.read_text())
        block = cs.get("installed") or cs.get("web") or {}
        cid, csec = block.get("client_id"), block.get("client_secret")
    data: dict
    if src.suffix in (".pickle", ".pkl", ".p"):
        with open(src, "rb") as f:
            creds = pickle.load(f)
        data = {"refresh_token": creds.refresh_token, "client_id": creds.client_id,
                "client_secret": creds.client_secret, "token": getattr(creds, "token", None),
                "scopes": list(creds.scopes or SCOPES)}
    else:
        raw = json.loads(src.read_text())
        data = raw.get("credentials", raw)
    refresh = data.get("refresh_token")
    if not refresh:
        raise ValueError(f"{src} has no refresh_token; it cannot be reused without re-authorising")
    out = {
        "type": "authorized_user",
        "client_id": data.get("client_id") or cid,
        "client_secret": data.get("client_secret") or csec,
        "refresh_token": refresh,
        "token": data.get("token") or data.get("access_token"),
        "scopes": data.get("scopes") or SCOPES,
        "token_uri": data.get("token_uri", "https://oauth2.googleapis.com/token"),
    }
    if not out["client_id"] or not out["client_secret"]:
        raise ValueError("client_id/client_secret missing: copy that project's client_secret.json into secrets/")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(out, indent=2))


def whoami(token_file: Path) -> dict:
    """Return {id, title} of the channel a token file is authorised for. Costs 1 quota unit."""
    yt = _service(token_file)
    resp = yt.channels().list(part="id,snippet", mine=True).execute()
    item = resp["items"][0]
    return {"id": item["id"], "title": item["snippet"]["title"]}
