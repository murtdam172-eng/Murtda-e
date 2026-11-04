
# -*- coding: utf-8 -*-
import os
import re
import glob
import shutil
import sqlite3
import tempfile
import ipaddress
import socket
from urllib.parse import urlparse
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template,
    send_file, Response, after_this_request
)
import yt_dlp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

SITE_NAME = "MORTADA EMPIRE"
DB_PATH = os.getenv("DB_PATH", "mortada_empire.db")
ADMIN_USER = os.getenv("ADMIN_USER", "mortada")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
MAX_SIZE = int(os.getenv("MAX_DOWNLOAD_MB", "48")) * 1024 * 1024

ALLOWED_DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com", "facebook.com",
    "fb.watch", "instagram.com", "x.com", "twitter.com",
    "pinterest.com", "reddit.com", "vimeo.com", "dailymotion.com"
)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        platform TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    return conn

db().close()

def valid_url(url):
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass
        try:
            for item in socket.getaddrinfo(host, None):
                addr = ipaddress.ip_address(item[4][0])
                if not addr.is_global:
                    return False
        except socket.gaierror:
            return False
        return True
    except Exception:
        return False

def platform_name(url):
    host = (urlparse(url).hostname or "").lower()
    return next((d for d in ALLOWED_DOMAINS if host == d or host.endswith("." + d)), "Unknown")

def extract_info(url):
    if not valid_url(url):
        raise ValueError("الرابط غير صالح أو المنصة غير مدعومة.")
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 20,
        "extractor_retries": 1, "retries": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if (not ADMIN_PASSWORD or not auth or
            auth.username != ADMIN_USER or auth.password != ADMIN_PASSWORD):
            return Response(
                "يلزم تسجيل الدخول إلى لوحة الإدارة.",
                401,
                {"WWW-Authenticate":  Basic realm="MORTADA EMPIRE ADMIN" }
            )
        return fn(*args, **kwargs)
    return wrapper

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

@app.get("/")
def home():
    return render_template("index.html")

@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    try:
        info = extract_info(url)
        return jsonify({
            "title": str(info.get("title") or "Video")[:180],
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration_string") or "غير معروف",
            "platform": platform_name(url)
        })
    except Exception as exc:
        return jsonify({"error": str(exc)[:220]}), 400

@app.post("/download")
def download():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    quality = data.get("quality", "best")
    formats = {
        "best": "best[ext=mp4]/best",
        "720": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "480": "best[height<=480][ext=mp4]/best[height<=480]/best",
        "audio": "bestaudio/best",
    }
    if quality not in formats:
        return jsonify({"error": "اختيار الجودة غير صحيح."}), 400
    if not valid_url(url):
        return jsonify({"error": "الرابط غير صالح أو المنصة غير مدعومة."}), 400

    temp_dir = tempfile.mkdtemp(prefix="mortada_")
    opts = {
        "format": formats[quality],
        "outtmpl": os.path.join(temp_dir, "%(title).70s.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "max_filesize": MAX_SIZE, "socket_timeout": 30,
        "retries": 1, "overwrites": True,
    }
    if quality == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = [p for p in glob.glob(os.path.join(temp_dir, "*")) if os.path.isfile(p)]
        if not files:
            raise RuntimeError("لم يتم إنشاء ملف التحميل.")
        path = max(files, key=os.path.getsize)
        if os.path.getsize(path) > MAX_SIZE:
            raise RuntimeError("حجم الملف يتجاوز الحد المسموح.")
        conn = db()
        conn.execute(
            "INSERT INTO downloads(title, platform) VALUES (?, ?)",
            (str(info.get("title") or "Video")[:180], platform_name(url))
        )
        conn.commit()
        conn.close()

        @after_this_request
        def cleanup(response):
            response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
            return response

        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": str(exc)[:220]}), 400

@app.get("/admin")
@admin_required
def admin():
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
    rows = conn.execute(
        "SELECT title, platform, created_at FROM downloads ORDER BY id DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return render_template("admin.html", total=total, rows=rows)

@app.get("/health")
def health():
    return jsonify({"status": "ok", "site": SITE_NAME})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)

