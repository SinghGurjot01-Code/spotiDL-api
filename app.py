# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import os
import time
import hashlib
import shutil
import json
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("AureumAPI")

app = FastAPI(title="Aureum Music API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# PATHS
# ============================================================
READONLY_COOKIES = "/etc/secrets/cookies.txt"    # Render secret file
WRITABLE_COOKIES = "/tmp/cookies.txt"           # Writable location
AUTH_JSON = "/tmp/ytmusic_auth.json"            # For YTMusic(auth=..)


# ============================================================
# COPY COOKIES TO WRITABLE AREA
# ============================================================
def prepare_writable_cookies():
    if not os.path.exists(READONLY_COOKIES):
        raise RuntimeError("cookies.txt missing in /etc/secrets")

    if not os.path.exists(WRITABLE_COOKIES):
        shutil.copy(READONLY_COOKIES, WRITABLE_COOKIES)
        log.info(f"Copied cookies to {WRITABLE_COOKIES}")

    return WRITABLE_COOKIES


# ============================================================
# BUILD SAPISIDHASH → WRITE auth.json
# ============================================================
def create_auth_file(cookie_file):
    cookies = {}

    with open(cookie_file, "r") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]

    sapisid = (
        cookies.get("SAPISID")
        or cookies.get("__Secure-1PAPISID")
        or cookies.get("__Secure-3PAPISID")
    )
    if not sapisid:
        raise RuntimeError("SAPISID cookie missing")

    origin = "https://music.youtube.com"
    timestamp = int(time.time())
    sig = hashlib.sha1(f"{timestamp} {sapisid} {origin}".encode()).hexdigest()

    headers = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Authorization": f"SAPISIDHASH {timestamp}_{sig}",
        "User-Agent": "Mozilla/5.0"
    }

    # Write JSON for legacy ytmusicapi
    with open(AUTH_JSON, "w") as f:
        json.dump(headers, f)

    log.info("Created YTMusic auth JSON")

    return AUTH_JSON


# ============================================================
# INITIALIZE AUTHENTICATED YTMUSIC
# ============================================================
cookies_path = prepare_writable_cookies()
auth_path = create_auth_file(cookies_path)

ytmusic = YTMusic(auth=auth_path)
log.info("YTMusic authenticated successfully.")


@app.get("/")
def home():
    return {
        "status": "online",
        "cookies": cookies_path,
        "auth_json": auth_path
    }


# ============================================================
# SEARCH
# ============================================================
@app.get("/search")
async def search(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(400, "Missing ?q")

    try:
        results = ytmusic.search(q, filter="songs", limit=limit)
        output = []

        for r in results:
            sec = 0
            if r.get("duration"):
                t = r["duration"].split(":")
                if len(t) == 2:
                    sec = int(t[0]) * 60 + int(t[1])
                elif len(t) == 3:
                    sec = int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2])

            artists = [a["name"] for a in r.get("artists", [])]

            output.append({
                "videoId": r.get("videoId", ""),
                "title": r.get("title", "Unknown"),
                "artists": ", ".join(artists),
                "thumbnail": r.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": r.get("duration", "0:00"),
                "duration_seconds": sec
            })

        return output

    except Exception as e:
        raise HTTPException(500, f"Search failed: {e}")


# ============================================================
# STREAM
# ============================================================
@app.get("/stream")
async def stream(videoId: str):
    if not videoId:
        raise HTTPException(400, "Missing videoId")

    url = f"https://www.youtube.com/watch?v={videoId}"

    ydl_opts = {
        "quiet": True,
        "cookiefile": cookies_path,
        "no_warnings": True,
        "noplaylist": True,
        "format": (
            "ba[ext=webm][acodec=opus]/"
            "bestaudio/best/"
            "bestaudio[ext=m4a]/"
            "worstaudio/best"
        ),
        "extractor_args": {
            "youtube": {"player_client": ["web", "android"]}
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        audio_url = info.get("url")

        if not audio_url:
            for f in info.get("formats", []):
                if f.get("acodec") != "none" and f.get("url"):
                    audio_url = f["url"]
                    break

        if not audio_url:
            raise HTTPException(404, "No audio stream found")

        return {
            "videoId": videoId,
            "stream_url": audio_url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail")
        }

    except Exception as e:
        raise HTTPException(500, f"Stream failed: {e}")
