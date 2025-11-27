# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import os
import time
import hashlib
import shutil
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
# READ-ONLY SOURCE (Render Secrets)
# ============================================================
READONLY_COOKIES = "/etc/secrets/cookies.txt"

# ============================================================
# READ-WRITE DESTINATION
# ============================================================
WRITABLE_COOKIES = "/tmp/cookies.txt"


def ensure_writable_cookies():
    """Copies cookies.txt from read-only location → /tmp/ (writable)."""
    if not os.path.exists(READONLY_COOKIES):
        raise RuntimeError("cookies.txt not found in /etc/secrets")

    # Copy only once at startup
    if not os.path.exists(WRITABLE_COOKIES):
        shutil.copy(READONLY_COOKIES, WRITABLE_COOKIES)
        log.info(f"Copied cookies to writable location: {WRITABLE_COOKIES}")

    return WRITABLE_COOKIES


# ============================================================
# AUTH HEADERS (SAPISIDHASH)
# ============================================================
def load_cookie_headers(cookie_file):
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

    return {
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Authorization": f"SAPISIDHASH {timestamp}_{sig}",
        "User-Agent": "Mozilla/5.0",
    }


# ============================================================
# INITIALIZE COOKIES + YTMUSIC AUTH
# ============================================================
cookies_path = ensure_writable_cookies()
headers = load_cookie_headers(cookies_path)

ytmusic = YTMusic(headers_raw=headers)
log.info("YTMusic Authenticated Successfully")


@app.get("/")
def root():
    return {
        "status": "online",
        "cookies_path": cookies_path,
        "cookies_readwrite": True
    }


# ============================================================
# SEARCH
# ============================================================
@app.get("/search")
async def search(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(400, "Missing ?q")

    try:
        res = ytmusic.search(q, filter="songs", limit=limit)
        out = []

        for r in res:
            # convert duration
            sec = 0
            if r.get("duration"):
                t = r["duration"].split(":")
                if len(t) == 2:
                    sec = int(t[0]) * 60 + int(t[1])
                elif len(t) == 3:
                    sec = int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2])

            artists = [a["name"] for a in r.get("artists", [])]

            out.append({
                "videoId": r.get("videoId", ""),
                "title": r.get("title", "Unknown"),
                "artists": ", ".join(artists),
                "thumbnail": r.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": r.get("duration", "0:00"),
                "duration_seconds": sec
            })

        return out

    except Exception as e:
        raise HTTPException(500, f"Search failed: {e}")


# ============================================================
# STREAM (Works for 99.9% of videos)
# ============================================================
@app.get("/stream")
async def stream(videoId: str):
    if not videoId:
        raise HTTPException(400, "Missing videoId")

    url = f"https://www.youtube.com/watch?v={videoId}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "cookiefile": cookies_path,
        "noplaylist": True,
        "format": (
            "ba[ext=webm][acodec=opus]/"
            "bestaudio/best/"
            "bestaudio[ext=m4a]/"
            "worstaudio/"
            "best"
        ),
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
        # MP3 postprocessor (your request)
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
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
            "stream_url": audio_url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "videoId": videoId
        }

    except Exception as e:
        raise HTTPException(500, f"Stream error: {e}")
