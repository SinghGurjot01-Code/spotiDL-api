# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import os
import time
import hashlib
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Aureum Music API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COOKIES_FILE = "/etc/secrets/cookies.txt"


# =====================================================
# COOKIE PARSER → YTMusic AUTH (SAPISIDHASH METHOD)
# =====================================================
def load_cookies_as_headers(cookie_file):
    """Load cookies from Netscape format file and create YTMusic auth headers"""
    if not os.path.exists(cookie_file):
        logger.warning(f"cookies.txt not found at {cookie_file}")
        return None

    cookies = {}

    try:
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
            logger.warning("No valid SAPISID cookie found")
            return None

        origin = "https://music.youtube.com"
        timestamp = int(time.time())
        hash_str = f"{timestamp} {sapisid} {origin}".encode()
        sapisidhash = hashlib.sha1(hash_str).hexdigest()

        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
            "Authorization": f"SAPISIDHASH {timestamp}_{sapisidhash}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        return headers
    except Exception as e:
        logger.error(f"Error loading cookies: {e}")
        return None


# =====================================================
# INITIALIZE YTMUSIC
# =====================================================
try:
    headers_raw = load_cookies_as_headers(COOKIES_FILE)
    if headers_raw:
        ytmusic = YTMusic(headers_raw=headers_raw)
        logger.info("YTMusic authenticated with cookies successfully")
    else:
        ytmusic = YTMusic()
        logger.info("YTMusic initialized without authentication")
except Exception as e:
    logger.warning(f"YTMusic initialization error: {e} - Using fallback")
    ytmusic = YTMusic()


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Aureum Music API",
        "endpoints": ["/search", "/stream", "/health"]
    }


@app.get("/health")
def health():
    return {"status": "healthy", "ytmusic": "initialized"}


# =====================================================
# SEARCH ENDPOINT
# =====================================================
@app.get("/search")
async def search_music(q: str, limit: int = 20):
    """Search for music using YTMusic API"""
    try:
        if not q.strip():
            raise HTTPException(400, "Query 'q' is required")

        logger.info(f"Searching for: {q}")
        results = ytmusic.search(q, filter="songs", limit=limit)

        formatted = []
        for r in results:
            # Parse duration to seconds
            duration_sec = 0
            if r.get("duration"):
                t = r["duration"].split(":")
                try:
                    if len(t) == 2:
                        duration_sec = int(t[0]) * 60 + int(t[1])
                    elif len(t) == 3:
                        duration_sec = int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2])
                except ValueError:
                    duration_sec = 0

            artists = [a["name"] for a in r.get("artists", [])]

            formatted.append({
                "videoId": r.get("videoId", ""),
                "title": r.get("title", "Unknown"),
                "artists": ", ".join(artists) if artists else "Unknown Artist",
                "thumbnail": r.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": r.get("duration", "0:00"),
                "duration_seconds": duration_sec,
            })

        logger.info(f"Found {len(formatted)} results")
        return formatted

    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(500, f"Search failed: {str(e)}")


# =====================================================
# STREAM ENDPOINT — 2025 UPDATED VERSION
# =====================================================
@app.get("/stream")
async def stream_music(videoId: str):
    """Extract streamable audio URL from YouTube video"""
    try:
        if not videoId:
            raise HTTPException(400, "videoId is required")

        logger.info(f"Extracting stream for videoId: {videoId}")

        # Enhanced yt-dlp options for 2025
        ydl_opts = {
            # Try multiple format fallbacks
            "format": (
                "bestaudio[ext=webm]/bestaudio[ext=m4a]/"
                "bestaudio/best"
            ),
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "extract_flat": False,
            
            # Add cookies if available
            "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            
            # User agent and headers
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            },
            
            # Multiple player clients for better compatibility
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web", "ios"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            
            # Disable age gate
            "age_limit": None,
        }

        url = f"https://www.youtube.com/watch?v={videoId}"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"yt-dlp extraction error: {e}")
                raise HTTPException(500, f"Failed to extract video info: {str(e)}")

        # Find the best audio stream URL
        audio_url = None

        # Method 1: Direct URL from info
        if "url" in info:
            audio_url = info["url"]
            logger.info("Found direct audio URL")

        # Method 2: Search through formats
        if not audio_url and "formats" in info:
            audio_formats = [
                f for f in info["formats"]
                if f.get("acodec") and f.get("acodec") != "none"
            ]

            if audio_formats:
                # Sort by audio bitrate (quality)
                audio_formats.sort(
                    key=lambda x: x.get("abr", 0) or x.get("tbr", 0), 
                    reverse=True
                )
                audio_url = audio_formats[0].get("url")
                logger.info(f"Found audio format: {audio_formats[0].get('format_note', 'unknown')}")

        # Method 3: Try requested formats
        if not audio_url and "requested_formats" in info:
            for fmt in info["requested_formats"]:
                if fmt.get("acodec") != "none":
                    audio_url = fmt.get("url")
                    logger.info("Found audio URL in requested_formats")
                    break

        if not audio_url:
            logger.error("No audio stream URL found in any format")
            raise HTTPException(404, "No audio stream found. Video may be restricted or unavailable.")

        response = {
            "stream_url": audio_url,
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "videoId": videoId,
            "format_note": info.get("format_note", "audio"),
        }

        logger.info(f"Stream extracted successfully: {info.get('title', 'Unknown')}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stream extraction failed: {str(e)}")
        raise HTTPException(500, f"Stream extraction failed: {str(e)}")


# Error handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global error: {exc}")
    return {
        "error": "Internal server error",
        "detail": str(exc)
    }
