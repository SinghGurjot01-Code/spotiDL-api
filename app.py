# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ytmusicapi import YTMusic
import yt_dlp
import os
import shutil
import logging
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("AureumMusicAPI")

app = FastAPI(title="Aureum Music API")

# CORS – open for your web app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Paths for cookies on Render ----
COOKIES_SOURCE = "/etc/secrets/cookies.txt"  # Render secret (read-only)
COOKIES_DEST = "/tmp/cookies.txt"            # Local copy (readable/writable)


def ensure_cookiefile() -> Optional[str]:
    """
    Ensure we have a readable cookiefile in /tmp.
    Returns the path or None if cookies are not available.
    """
    if not os.path.exists(COOKIES_SOURCE):
        log.warning("No cookies file at %s", COOKIES_SOURCE)
        return None

    try:
        if not os.path.exists(COOKIES_DEST):
            shutil.copy(COOKIES_SOURCE, COOKIES_DEST)
            log.info("Copied cookies.txt -> %s", COOKIES_DEST)
        return COOKIES_DEST
    except Exception as e:
        log.error("Failed to copy cookies: %s", e)
        return None


# ---- YTMusic (search only; works fine unauthenticated) ----
try:
    ytmusic = YTMusic()
    log.info("YTMusic initialized (unauthenticated)")
except Exception as e:
    log.error("YTMusic initialization failed: %s", e)
    ytmusic = None


@app.get("/")
def root():
    return {
        "service": "Aureum Music API",
        "status": "online",
        "ytmusic": "ready" if ytmusic else "unavailable",
        "cookies_available": os.path.exists(COOKIES_SOURCE),
    }


@app.get("/health")
def health():
    return {
        "status": "healthy" if ytmusic else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "cookies_available": os.path.exists(COOKIES_SOURCE),
    }


# ---------------- SEARCH ----------------
@app.get("/search")
async def search(q: str, limit: int = 20):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    if not ytmusic:
        raise HTTPException(status_code=503, detail="YTMusic not available")

    try:
        results = ytmusic.search(q, filter="songs", limit=limit)
        formatted = []

        for r in results:
            if "videoId" not in r:
                continue

            # duration -> seconds
            dur_str = r.get("duration", "0:00")
            dur_sec = 0
            if dur_str and ":" in dur_str:
                parts = dur_str.split(":")
                try:
                    if len(parts) == 2:
                        dur_sec = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        dur_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except ValueError:
                    dur_sec = 0

            artists = [a.get("name", "") for a in r.get("artists", []) if a.get("name")]

            # pick largest thumbnail
            thumb_url = ""
            thumbs = r.get("thumbnails") or []
            if thumbs:
                thumbs_sorted = sorted(thumbs, key=lambda x: x.get("width", 0), reverse=True)
                thumb_url = thumbs_sorted[0].get("url", "")

            formatted.append({
                "videoId": r["videoId"],
                "title": r.get("title", ""),
                "artists": ", ".join(artists) if artists else "",
                "thumbnail": thumb_url,
                "duration": dur_str,
                "duration_seconds": dur_sec,
            })

        return JSONResponse(content=formatted)

    except Exception as e:
        log.exception("Search failed")
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


# ------------- STREAM (progressive MP4/WEBM, browser-playable) -------------
@app.get("/stream")
async def stream(videoId: str):
    if not videoId:
        raise HTTPException(status_code=400, detail="videoId is required")

    url = f"https://www.youtube.com/watch?v={videoId}"
    cookiefile = ensure_cookiefile()

    # We deliberately request **progressive** formats with both audio+video,
    # because browsers handle them reliably.
    FORMAT_CHAIN = (
        "best[ext=mp4][vcodec!=none][acodec!=none]/"
        "best[ext=webm][vcodec!=none][acodec!=none]/"
        "best"
    )

    ydl_opts = {
        "format": FORMAT_CHAIN,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "cookiefile": cookiefile,
        "http_headers": {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com/",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android"],
                "skip": ["dash", "hls"],  # avoid segmented streams
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(status_code=404, detail="Video not found")

        stream_url = info.get("url")

        # extra fallback: search formats list
        if not stream_url and "formats" in info:
            fmts = [f for f in info["formats"] if f.get("url")]
            # prefer mp4 with both audio+video
            preferred = [
                f for f in fmts
                if f.get("ext") == "mp4"
                and f.get("vcodec") != "none"
                and f.get("acodec") != "none"
            ]
            if preferred:
                preferred.sort(key=lambda x: x.get("height", 0), reverse=True)
                stream_url = preferred[0]["url"]
            else:
                # fallback: any format with audio
                with_audio = [f for f in fmts if f.get("acodec") != "none"]
                if with_audio:
                    with_audio.sort(key=lambda x: x.get("abr", 0) or 0, reverse=True)
                    stream_url = with_audio[0]["url"]

        if not stream_url:
            raise HTTPException(status_code=404, detail="No playable stream found")

        return {
            "videoId": videoId,
            "stream_url": stream_url,
            "title": info.get("title", ""),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        log.exception("Stream extraction failed")
        raise HTTPException(status_code=500, detail=f"Stream extraction failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)