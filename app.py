

# -*- coding: utf-8 -*-

import os
import hmac
import sqlite3
import tempfile
import shutil
from urllib.parse import urlparse

from flask import Flask, request, jsonify, Response, render_template, send_file
import yt_dlp

# ═══════════════════════════════════════
#          MORTADA EMPIRE
# ═══════════════════════════════════════

APP_NAME = "MORTADA EMPIRE"
DEVELOPER = "مــرتــضــى"
DEVELOPER_USERNAME = "@xa_qu"

ADMIN_USER = os.environ.get("ADMIN_USER", "mortada")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

MAX_SIZE = 48 * 1024 * 1024
DB_NAME = "/tmp/mortada_empire.db"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

ALLOWED_DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com",
    "facebook.com", "fb.watch", "instagram.com",
    "twitter.com", "x.com", "pinterest.com",
    "reddit.com", "dailymotion.com", "vimeo.com"
)

# ═══════════════════════════════════════
#                 DATABASE
# ═══════════════════════════════════════

def init_db():
    connection = sqlite3.connect(DB_NAME)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    connection.commit()
    connection.close()


init_db()

# ═══════════════════════════════════════
#               URL VALIDATION
# ═══════════════════════════════════════

def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False

    try:
        parsed = urlparse(url.strip())

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname or parsed.username or parsed.password:
            return False

        hostname = parsed.hostname.lower().rstrip(".")

        return any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in ALLOWED_DOMAINS
        )

    except Exception:
        return False


# ═══════════════════════════════════════
#                ADMIN LOGIN
# ═══════════════════════════════════════

def admin_required():
    auth = request.authorization

    if not ADMIN_PASSWORD or not auth:
        return False

    user_ok = hmac.compare_digest(
        auth.username or "",
        ADMIN_USER
    )

    password_ok = hmac.compare_digest(
        auth.password or "",
        ADMIN_PASSWORD
    )

    return user_ok and password_ok


def admin_login_required():
    if not admin_required():
        return Response(
            "Authentication required",
            401,
            headers={
                "WWW-Authenticate":  Basic realm="MORTADA EMPIRE ADMIN" 
            }
        )

    return None


# ═══════════════════════════════════════
#                YT-DLP OPTIONS
# ═══════════════════════════════════════

def get_ydl_options():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "max_filesize": MAX_SIZE,
        "restrictfilenames": True,
    }


# ═══════════════════════════════════════
#                    HOME
# ═══════════════════════════════════════

@app.route("/")
def home():
    return render_template("index.html")


# ═══════════════════════════════════════
#               VIDEO INFORMATION
# ═══════════════════════════════════════

@app.route("/api/info", methods=["POST"])
def video_info():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()

    if not validate_url(url):
        return jsonify({
            "error": "الرابط غير صحيح أو المنصة غير مدعومة"
        }), 400

    try:
        with yt_dlp.YoutubeDL(get_ydl_options()) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise Exception("لم يتم العثور على الفيديو")

        return jsonify({
            "title": info.get("title", "Video"),
            "platform": info.get("extractor_key", "Unknown"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration")
        })

    except Exception:
        app.logger.exception("Video information error")
        return jsonify({
            "error": "تعذر فحص الرابط، حاول مرة أخرى"
        }), 400


# ═══════════════════════════════════════
#                    DOWNLOAD
# ═══════════════════════════════════════

@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json(silent=True) or {}

    url = str(data.get("url", "")).strip()
    quality = str(data.get("quality", "best"))

    if not validate_url(url):
        return jsonify({
            "error": "الرابط غير صحيح أو المنصة غير مدعومة"
        }), 400

    if quality not in ("best", "720", "480", "audio"):
        return jsonify({
            "error": "الجودة غير صحيحة"
        }), 400

    temp_dir = tempfile.mkdtemp(prefix="mortada_")

    try:
        options = get_ydl_options()
        options["outtmpl"] = os.path.join(
            temp_dir,
            "%(title).70s_%(id)s.%(ext)s"
        )

        if quality == "audio":
            options["format"] = "bestaudio/best"
        elif quality == "720":
            options["format"] = "best[height<=720]/best"
        elif quality == "480":
            options["format"] = "best[height<=480]/best"
        else:
            options["format"] = "best"

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        files = [
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, name))
            and not name.endswith((".part", ".ytdl"))
        ]

        if not files:
            raise Exception("لم يتم إنشاء ملف التحميل")

        filepath = max(files, key=os.path.getsize)

        if os.path.getsize(filepath) > MAX_SIZE:
            raise Exception("حجم الملف أكبر من 48 ميجابايت")

        response = send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath)
        )

        response.call_on_close(
            lambda: shutil.rmtree(temp_dir, ignore_errors=True)
        )

        return response

    except Exception as error:
        app.logger.exception("Download error")
        shutil.rmtree(temp_dir, ignore_errors=True)

        return jsonify({
            "error": str(error)[:250]
        }), 400


# ═══════════════════════════════════════
#                 ADMIN PANEL
# ═══════════════════════════════════════

@app.route("/admin")
def admin():
    auth_error = admin_login_required()

    if auth_error:
        return auth_error

    return render_template("admin.html")


# ═══════════════════════════════════════
#                   HEALTH
# ═══════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "name": APP_NAME,
        "developer": DEVELOPER
    })


# ═══════════════════════════════════════
#                   ERRORS
# ═══════════════════════════════════════

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "الصفحة غير موجودة"
    }), 404


@app.errorhandler(413)
def too_large(error):
    return jsonify({
        "error": "حجم الطلب أكبر من المسموح"
    }), 413


@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Server error")
    return jsonify({
        "error": "حدث خطأ داخلي في الخادم"
    }), 500


# ═══════════════════════════════════════
#                  RUN SERVER
# ═══════════════════════════════════════

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        debug=False
    )
