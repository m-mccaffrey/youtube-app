"""
app.py — Flask server for the YouTube shuffle TV channel.
Run: python app.py
"""

import os
import re
import sqlite3

import refresh
from flask import Flask, jsonify, redirect, request, send_from_directory, url_for
from googleapiclient.discovery import build

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False

DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
CLIENT_SECRETS_PATH = os.path.join(os.path.dirname(__file__), "client_secrets.json")
STATIC_DIR = os.path.dirname(__file__)
SCOPES = ["https://www.googleapis.com/auth/youtube"]

app = Flask(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_duration_seconds(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def get_oauth_credentials():
    if not OAUTH_AVAILABLE or not os.path.exists(TOKEN_PATH):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        return creds
    return None


# ── pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/search")
def search_page():
    return send_from_directory(STATIC_DIR, "search.html")


# ── data endpoints ───────────────────────────────────────────────────────────

@app.route("/videos")
def videos():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, channel, duration, thumbnail, published_at"
        " FROM videos WHERE embeddable = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/search", methods=["POST"])
def api_search():
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return jsonify({"error": "YOUTUBE_API_KEY not set"}), 500

    data = request.get_json()
    terms = [t.strip() for t in (data.get("terms") or "").splitlines() if t.strip()]
    suffix = (data.get("suffix") or "").strip()
    if not terms:
        return jsonify({})

    youtube = build("youtube", "v3", developerKey=api_key)
    results = {}

    for term in terms:
        query = f"{term} {suffix}".strip() if suffix else term
        search_resp = youtube.search().list(
            part="id",
            q=query,
            type="video",
            maxResults=25,
            order="relevance",
        ).execute()

        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
        if not video_ids:
            results[term] = []
            continue

        videos_resp = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(video_ids),
        ).execute()

        candidates = []
        for item in videos_resp.get("items", []):
            secs = parse_duration_seconds(item["contentDetails"]["duration"])
            if secs == 0 or secs >= 600:
                continue
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            thumbs = snippet.get("thumbnails", {})
            thumbnail = (
                thumbs.get("high", {}).get("url")
                or thumbs.get("medium", {}).get("url")
                or thumbs.get("default", {}).get("url")
                or ""
            )
            candidates.append({
                "id": item["id"],
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "duration": item["contentDetails"]["duration"],
                "thumbnail": thumbnail,
                "likes": int(stats.get("likeCount", 0)),
                "views": int(stats.get("viewCount", 0)),
            })

        candidates.sort(key=lambda v: v["likes"], reverse=True)
        results[term] = candidates[:5]

    return jsonify(results)


@app.route("/api/add", methods=["POST"])
def api_add():
    playlist_id = os.environ.get("YOUTUBE_PLAYLIST_ID", "")
    if not playlist_id:
        return jsonify({"error": "YOUTUBE_PLAYLIST_ID not set"}), 500

    creds = get_oauth_credentials()
    if not creds:
        return jsonify({"error": "not_authorized"}), 401

    video_id = (request.get_json() or {}).get("video_id")
    if not video_id:
        return jsonify({"error": "missing video_id"}), 400

    youtube = build("youtube", "v3", credentials=creds)
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()

    return jsonify({"ok": True})


@app.route("/api/auth-status")
def api_auth_status():
    return jsonify({
        "authorized": get_oauth_credentials() is not None,
        "oauth_available": OAUTH_AVAILABLE,
        "client_secrets_exist": os.path.exists(CLIENT_SECRETS_PATH),
    })


# ── OAuth flow ───────────────────────────────────────────────────────────────

_oauth_flow = None  # kept in memory between /auth and /auth/callback


@app.route("/auth")
def auth():
    global _oauth_flow
    if not OAUTH_AVAILABLE:
        return "Run: pip install google-auth-oauthlib", 501
    if not os.path.exists(CLIENT_SECRETS_PATH):
        return f"Place client_secrets.json in {STATIC_DIR}", 501
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    _oauth_flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_PATH, scopes=SCOPES,
        redirect_uri=url_for("auth_callback", _external=True),
    )
    auth_url, _ = _oauth_flow.authorization_url(prompt="consent")
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    global _oauth_flow
    if not _oauth_flow:
        return redirect(url_for("auth"))
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    _oauth_flow.fetch_token(authorization_response=request.url.replace("https://", "http://"))
    with open(TOKEN_PATH, "w") as f:
        f.write(_oauth_flow.credentials.to_json())
    _oauth_flow = None
    return redirect(url_for("search_page"))


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("--- Syncing playlist ---")
    refresh.main()
    print("--- Starting server ---")
    app.run(host="0.0.0.0", port=5000, debug=False)
