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
            id          TEXT PRIMARY KEY,
            title       TEXT,
            channel     TEXT,
            description TEXT,
            duration    TEXT,
            thumbnail   TEXT,
            tags        TEXT,
            published_at TEXT
        )
    """)
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
    """Fetch snippet + contentDetails for a list of video IDs (batched 50 at a time)."""
    details = {}

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(
            part="snippet,contentDetails",
            id=",".join(batch),
            maxResults=50,
        ).execute()

        for item in resp.get("items", []):
            vid_id = item["id"]
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})

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
            }

    return details


def upsert_videos(conn, videos):
    conn.executemany(
        """
        INSERT INTO videos (id, title, channel, description, duration, thumbnail, tags, published_at)
        VALUES (:id, :title, :channel, :description, :duration, :thumbnail, :tags, :published_at)
        ON CONFLICT(id) DO UPDATE SET
            title        = excluded.title,
            channel      = excluded.channel,
            description  = excluded.description,
            duration     = excluded.duration,
            thumbnail    = excluded.thumbnail,
            tags         = excluded.tags,
            published_at = excluded.published_at
        """,
        videos,
    )
    conn.commit()


def main():
    if not API_KEY:
        print("Error: YOUTUBE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)
    if not PLAYLIST_ID:
        print("Error: YOUTUBE_PLAYLIST_ID environment variable not set.", file=sys.stderr)
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=API_KEY)

    print(f"Fetching playlist {PLAYLIST_ID} ...")
    video_ids = fetch_playlist_video_ids(youtube, PLAYLIST_ID)
    print(f"  Found {len(video_ids)} videos in playlist.")

    print("Fetching video details ...")
    details = fetch_video_details(youtube, video_ids)
    print(f"  Got details for {len(details)} videos.")

    conn = get_db()
    init_db(conn)
    upsert_videos(conn, list(details.values()))
    conn.close()

    print(f"Done. {len(details)} videos written to {DB_PATH}")


if __name__ == "__main__":
    main()
