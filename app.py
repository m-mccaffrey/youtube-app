"""
app.py — Flask server for the YouTube shuffle TV channel.
Run: python app.py
"""

import json
import os
import sqlite3

from flask import Flask, jsonify, send_from_directory

DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite")
STATIC_DIR = os.path.dirname(__file__)

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/videos")
def videos():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, channel, duration, thumbnail, published_at FROM videos WHERE embeddable = 1 ORDER BY id"
    ).fetchall()
    conn.close()

    result = [
        {
            "id": r["id"],
            "title": r["title"],
            "channel": r["channel"],
            "duration": r["duration"],
            "thumbnail": r["thumbnail"],
            "published_at": r["published_at"],
        }
        for r in rows
    ]
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
