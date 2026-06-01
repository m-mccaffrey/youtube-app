"""
refresh.py — fetch all videos from a YouTube playlist and cache to SQLite.
Run manually or via cron: python refresh.py
"""

import json
import os
import sqlite3
import sys

from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
PLAYLIST_ID = os.environ.get("YOUTUBE_PLAYLIST_ID", "")
DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id           TEXT PRIMARY KEY,
            title        TEXT,
            channel      TEXT,
            description  TEXT,
            duration     TEXT,
            thumbnail    TEXT,
            tags         TEXT,
            published_at TEXT,
            embeddable   INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Add column to existing DBs that predate this field
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN embeddable INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    conn.commit()


def fetch_playlist_video_ids(youtube, playlist_id):
    """Return all video IDs in the playlist via paginated calls."""
    video_ids = []
    page_token = None

    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in resp.get("items", []):
            vid = item["contentDetails"]["videoId"]
            video_ids.append(vid)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return video_ids


def fetch_video_details(youtube, video_ids):
    """Fetch snippet, contentDetails, and status for a list of video IDs (batched 50 at a time)."""
    details = {}

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(
            part="snippet,contentDetails,status",
            id=",".join(batch),
            maxResults=50,
        ).execute()

        for item in resp.get("items", []):
            vid_id = item["id"]
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            status  = item.get("status", {})

            thumbnails = snippet.get("thumbnails", {})
            thumbnail = (
                thumbnails.get("maxres", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
                or ""
            )

            details[vid_id] = {
                "id": vid_id,
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "duration": content.get("duration", ""),
                "thumbnail": thumbnail,
                "tags": json.dumps(snippet.get("tags") or []),
                "published_at": snippet.get("publishedAt", ""),
                "embeddable": 1 if status.get("embeddable", True) else 0,
            }

    return details


def upsert_videos(conn, videos):
    conn.executemany(
        """
        INSERT INTO videos (id, title, channel, description, duration, thumbnail, tags, published_at, embeddable)
        VALUES (:id, :title, :channel, :description, :duration, :thumbnail, :tags, :published_at, :embeddable)
        ON CONFLICT(id) DO UPDATE SET
            title        = excluded.title,
            channel      = excluded.channel,
            description  = excluded.description,
            duration     = excluded.duration,
            thumbnail    = excluded.thumbnail,
            tags         = excluded.tags,
            published_at = excluded.published_at,
            embeddable   = excluded.embeddable
        """,
        videos,
    )
    conn.execute("DELETE FROM videos WHERE embeddable = 0")
    conn.commit()


def main():
    api_key     = os.environ.get("YOUTUBE_API_KEY", "")
    playlist_id = os.environ.get("YOUTUBE_PLAYLIST_ID", "")

    if not api_key:
        print("Error: YOUTUBE_API_KEY environment variable not set.", file=sys.stderr)
        return False
    if not playlist_id:
        print("Error: YOUTUBE_PLAYLIST_ID environment variable not set.", file=sys.stderr)
        return False

    youtube = build("youtube", "v3", developerKey=api_key)

    print(f"Fetching playlist {playlist_id} ...")
    video_ids = fetch_playlist_video_ids(youtube, playlist_id)
    print(f"  Found {len(video_ids)} videos in playlist.")

    print("Fetching video details ...")
    details = fetch_video_details(youtube, video_ids)
    print(f"  Got details for {len(details)} videos.")

    conn = get_db()
    init_db(conn)
    upsert_videos(conn, list(details.values()))
    conn.close()

    embeddable = sum(1 for v in details.values() if v["embeddable"])
    skipped    = len(details) - embeddable
    print(f"Done. {embeddable} embeddable videos in {DB_PATH}"
          + (f" ({skipped} removed — embedding disabled)" if skipped else ""))
    return True


if __name__ == "__main__":
    if not main():
        sys.exit(1)
