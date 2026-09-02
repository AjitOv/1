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
