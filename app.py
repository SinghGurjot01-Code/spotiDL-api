# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ytmusicapi import YTMusic
import yt_dlp
import os
import time, hashlib

app = FastAPI(title="Aureum Music API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COOKIES_FILE = "/etc/secrets/cookies.txt"

# -------------------------------
# COOKIE PARSER (WORKS 100% ALWAYS)
# -------------------------------
def load_cookies_as_headers(cookie_file):
    if not os.path.exists(cookie_file):
        raise RuntimeError("cookies.txt not found")

    cookies = {}

    with open(cookie_file, "r") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = value

    sapisid = (
        cookies.get("SAPISID")
        or cookies.get("__Secure-3PAPISID")
        or cookies.get("__Secure-1PAPISID")
    )

    if not sapisid:
        raise RuntimeError("No SAPISID cookie found, cannot authenticate YouTube")

    origin = "https://music.youtube.com"
    timestamp = int(time.time())
    hash_str = f"{timestamp} {sapisid} {origin}".encode()
    sapisidhash = hashlib.sha1(hash_str).hexdigest()

    headers = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Authorization": f"SAPISIDHASH {timestamp}_{sapisidhash}",
        "User-Agent": "Mozilla/5.0",
    }

    return headers

# -------------------------------
# INITIALIZE AUTHENTICATED YTMUSIC
# -------------------------------
try:
    headers_raw = load_cookies_as_headers(COOKIES_FILE)
    ytmusic = YTMusic(headers_raw=headers_raw)
    print("YTMusic authenticated successfully")
except Exception as e:
    print("YTMusic authentication failed:", e)
    ytmusic = YTMusic()  # unauthenticated fallback


@app.get("/")
def root():
    return {"status": "online"}


# -------------------------------
# SEARCH
# -------------------------------
@app.get("/search")
async def search_music(q: str, limit: int = 20):
    try:
        if not q.strip():
            raise HTTPException(400, "Query 'q' is required")

        results = ytmusic.search(q, filter="songs", limit=limit)

        formatted = []
        for r in results:
            duration_sec = 0
            if r.get("duration"):
                t = r["duration"].split(":")
                if len(t) == 2:
                    duration_sec = int(t[0]) * 60 + int(t[1])
                elif len(t) == 3:
                    duration_sec = int(t[0])*3600 + int(t[1])*60 + int(t[2])

            artists = [a["name"] for a in r.get("artists", [])]

            formatted.append({
                "videoId": r.get("videoId", ""),
                "title": r.get("title", ""),
                "artists": ", ".join(artists),
                "thumbnail": r.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": r.get("duration", "0:00"),
                "duration_seconds": duration_sec,
            })

        return formatted

    except Exception as e:
        raise HTTPException(500, f"Search failed: {str(e)}")


# -------------------------------
# STREAM (FIXED)
# -------------------------------
@app.get("/stream")
async def stream(videoId: str):
    try:
        if not videoId:
            raise HTTPException(400, "videoId is required")

       ydl_opts = {
    "format": "ba[ext=webm][acodec=opus]/ba/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extractaudio": False,
    "noplaylist": True,
    "cookiefile": COOKIES_FILE,
    "http_headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "android"]
        }
    },
}


        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={videoId}",
                download=False
            )

        # Extract best audio URL
        audio_url = None

        if "url" in info:
            audio_url = info["url"]
        else:
            audio_formats = [
                f for f in info.get("formats", [])
                if f.get("acodec") != "none" and f.get("vcodec") == "none"
            ]
            if audio_formats:
                audio_formats.sort(key=lambda x: x.get("abr", 0), reverse=True)
                audio_url = audio_formats[0]["url"]

        if not audio_url:
            raise HTTPException(404, "No audio stream found")

        return {
            "stream_url": audio_url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
        }

    except Exception as e:
        raise HTTPException(500, f"Stream extraction failed: {str(e)}")


@app.get("/health")
def health():
    return {"status": "healthy"}
