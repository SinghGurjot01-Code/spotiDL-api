# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ytmusicapi import YTMusic
import yt_dlp
import os, shutil, logging
from datetime import datetime

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

# --------------------------
# HANDLE COOKIES (Render)
# --------------------------
COOKIES_SOURCE = "/etc/secrets/cookies.txt"
COOKIES_DEST = "/tmp/cookies.txt"

def cookies_file():
    if os.path.exists(COOKIES_SOURCE):
        if not os.path.exists(COOKIES_DEST):
            shutil.copy(COOKIES_SOURCE, COOKIES_DEST)
            log.info("Copied cookies.txt to /tmp")
        return COOKIES_DEST
    return None


# --------------------------
# INIT YTMusic (for search)
# --------------------------
try:
    ytmusic = YTMusic()
    log.info("YTMusic OK")
except:
    ytmusic = None
    log.error("YTMusic unavailable")


@app.get("/")
def home():
    return {"status": "online", "ytmusic": ytmusic is not None}


# --------------------------
# SEARCH
# --------------------------
@app.get("/search")
async def search(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(400, "Missing ?q")

    if not ytmusic:
        raise HTTPException(503, "YTMusic unavailable")

    res = ytmusic.search(q, filter="songs", limit=limit)
    out = []

    for r in res:
        if "videoId" not in r:
            continue

        dur_str = r.get("duration", "0:00")
        sec = 0
        if ":" in dur_str:
            parts = list(map(int, dur_str.split(":")))
            if len(parts) == 2:
                sec = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                sec = parts[0] * 3600 + parts[1] * 60 + parts[2]

        thumbs = r.get("thumbnails") or []
        thumb = thumbs[-1]["url"] if thumbs else ""

        artists = ", ".join(a["name"] for a in r.get("artists", []))

        out.append({
            "videoId": r["videoId"],
            "title": r.get("title", ""),
            "artists": artists,
            "thumbnail": thumb,
            "duration": dur_str,
            "duration_seconds": sec
        })

    return out


# --------------------------
# STREAM — **UNBREAKABLE VERSION**
# --------------------------
@app.get("/stream")
async def stream(videoId: str):
    if not videoId:
        raise HTTPException(400, "Missing videoId")

    cookie = cookies_file()
    url = f"https://www.youtube.com/watch?v={videoId}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cookiefile": cookie,
        "ignore_no_formats_error": True,
        "ignoreerrors": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9"
        },
        "extractor_args": {"youtube": {"player_client": ["web"]}}
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(404, "Video not found")

        formats = info.get("formats", [])
        if not formats:
            raise HTTPException(404, "No formats found")

        # --------------------------
        # **SMART FORMAT PICKER**
        # --------------------------
        playable = []

        for f in formats:
            if not f.get("url"):
                continue

            # Browser-friendly only
            if f.get("acodec") == "none":
                continue

            if f.get("ext") not in ("mp4", "webm", "m4a"):
                continue

            playable.append(f)

        if not playable:
            raise HTTPException(404, "No playable format found")

        # Prefer:
        # 1. mp4 with both audio+video
        # 2. webm with audio+video
        # 3. audio-only fallback
        playable.sort(
            key=lambda x: (
                x.get("vcodec") != "none",
                x.get("abr", 0) or 0,
                x.get("height", 0) or 0
            ),
            reverse=True
        )

        best = playable[0]
        stream_url = best["url"]

        return {
            "stream_url": stream_url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "videoId": videoId
        }

    except Exception as e:
        log.error("STREAM ERROR: %s", e)
        raise HTTPException(500, f"Stream error: {e}")