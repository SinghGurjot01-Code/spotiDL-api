# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ytmusicapi import YTMusic
import yt_dlp
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

# Initialize ytmusicapi with cookies file
def initialize_ytmusic():
    cookies_file = "/etc/secrets/cookies.txt"
    
    if os.path.exists(cookies_file):
        try:
            print("Loading cookies from file...")
            return YTMusic(auth=cookies_file)
        except Exception as e:
            print(f"Failed to load cookies from file: {e}")
            print("Falling back to default YTMusic initialization...")
    
    # Fallback to default initialization
    return YTMusic()

# Initialize ytmusic
ytmusic = initialize_ytmusic()

@app.get("/")
async def root():
    return {"message": "Aureum Music API - Premium Streaming Service"}

@app.get("/search")
async def search_music(q: str, limit: int = 20):
    """
    Search for music using YouTube Music API
    """
    try:
        if not q or len(q.strip()) == 0:
            raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
        
        search_results = ytmusic.search(q, filter="songs", limit=limit)
        
        formatted_results = []
        for result in search_results:
            # Extract duration if available
            duration_seconds = 0
            if 'duration' in result and result['duration']:
                time_parts = result['duration'].split(':')
                if len(time_parts) == 2:
                    duration_seconds = int(time_parts[0]) * 60 + int(time_parts[1])
                elif len(time_parts) == 3:
                    duration_seconds = int(time_parts[0]) * 3600 + int(time_parts[1]) * 60 + int(time_parts[2])
            
            # Get artist names
            artists = []
            if 'artists' in result:
                artists = [artist['name'] for artist in result['artists']]
            
            formatted_result = {
                "videoId": result.get('videoId', ''),
                "title": result.get('title', 'Unknown Title'),
                "artists": ", ".join(artists) if artists else "Unknown Artist",
                "thumbnail": result.get('thumbnails', [{}])[-1].get('url', '') if result.get('thumbnails') else '',
                "duration": result.get('duration', '0:00'),
                "duration_seconds": duration_seconds
            }
            formatted_results.append(formatted_result)
        
        return JSONResponse(content=formatted_results)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/stream")
async def get_stream_url(videoId: str):
    """
    Get streamable audio URL for a YouTube video ID
    """
    try:
        if not videoId:
            raise HTTPException(status_code=400, detail="videoId parameter is required")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'noplaylist': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get video info without downloading
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={videoId}",
                download=False
            )
            
            # Find the best audio URL
            audio_url = None
            if 'url' in info:
                audio_url = info['url']
            elif 'formats' in info:
                # Look for audio formats and pick the best one
                audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                if audio_formats:
                    # Sort by bitrate and get the highest quality
                    audio_formats.sort(key=lambda x: x.get('abr', 0) or 0, reverse=True)
                    audio_url = audio_formats[0]['url']
            
            if not audio_url:
                raise HTTPException(status_code=404, detail="No audio stream found")
            
            # Get additional track info
            duration = info.get('duration', 0)
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', '')
            
            return {
                "stream_url": audio_url,
                "title": title,
                "duration": duration,
                "thumbnail": thumbnail,
                "videoId": videoId
            }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stream URL extraction failed: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Aureum Music API"}

@app.get("/cookies-status")
async def cookies_status():
    """
    Check if cookies are properly loaded
    """
    cookies_file = "/etc/secrets/cookies.txt"
    cookies_exists = os.path.exists(cookies_file)
    
    try:
        # Test if YTMusic is working with current auth
        test_search = ytmusic.search("test", limit=1)
        cookies_working = True
    except Exception as e:
        cookies_working = False
        test_error = str(e)
    
    return {
        "cookies_file_exists": cookies_exists,
        "cookies_file_path": cookies_file,
        "cookies_working": cookies_working,
        "error": test_error if not cookies_working else None
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
