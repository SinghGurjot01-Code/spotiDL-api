# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import ytmusicapi
import yt_dlp
import asyncio
import aiohttp
import os
from typing import List, Optional
import json

app = FastAPI(title="Aureum Music API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Path to Render secret file
COOKIES_PATH = "/etc/secrets/cookies.txt"

if not os.path.exists(COOKIES_PATH):
    raise RuntimeError(f"Cookies file missing at {COOKIES_PATH}. Upload cookies.txt in Render Secrets.")

# Initialize ytmusicapi with cookies
ytmusic = ytmusicapi.YTMusic(COOKIES_PATH)

@app.get("/")
async def root():
    return {"message": "Aureum Music API - Premium Streaming Service"}

@app.get("/search")
async def search_music(q: str, limit: int = 20):
    try:
        if not q.strip():
            raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

        search_results = ytmusic.search(q, filter="songs", limit=limit)

        formatted_results = []
        for r in search_results:
            duration_sec = 0
            if r.get("duration"):
                parts = r["duration"].split(":")
                if len(parts) == 2:
                    duration_sec = int(parts[0])*60 + int(parts[1])
                elif len(parts) == 3:
                    duration_sec = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])

            artists = [a["name"] for a in r.get("artists", [])]

            formatted_results.append({
                "videoId": r.get("videoId", ""),
                "title": r.get("title", "Unknown Title"),
                "artists": ", ".join(artists) if artists else "Unknown Artist",
                "thumbnail": r.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": r.get("duration", "0:00"),
                "duration_seconds": duration_sec
            })

        return JSONResponse(content=formatted_results)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/stream")
async def get_stream_url(videoId: str):
    try:
        if not videoId:
            raise HTTPException(status_code=400, detail="videoId is required")

        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extractaudio": True,
            "audioformat": "mp3",
            "noplaylist": True,
            "cookiefile": COOKIES_PATH,  # IMPORTANT
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={videoId}",
                download=False
            )

            # best audio URL
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
                    audio_url = audio_formats[0].get("url")

            if not audio_url:
                raise HTTPException(status_code=404, detail="No audio stream found")

            return {
                "stream_url": audio_url,
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "videoId": videoId
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stream URL extraction failed: {str(e)}")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
