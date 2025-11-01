import os
import json
import zipfile
import tempfile
import requests
import yt_dlp
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv
import threading
from collections import defaultdict
import base64

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Spotify client
sp = Spotify(client_credentials_manager=SpotifyClientCredentials(
    client_id=os.getenv('SPOTIPY_CLIENT_ID'),
    client_secret=os.getenv('SPOTIPY_CLIENT_SECRET')
))

# Store download progress
download_progress = defaultdict(dict)

# Cookie configuration for Render.com
def setup_cookies():
    """Setup cookies from Render environment variable"""
    # Get the cookie file path from environment variable
    cookie_file_path = os.getenv('COOKIE_FILE')
    
    if cookie_file_path and os.path.exists(cookie_file_path):
        print(f"✅ Cookies found at: {cookie_file_path}")
        return cookie_file_path
    
    # Fallback: Check if cookies are provided as base64 in environment
    cookies_base64 = os.getenv('YOUTUBE_COOKIES_BASE64')
    if cookies_base64:
        try:
            # Decode base64 cookies
            cookies_content = base64.b64decode(cookies_base64).decode('utf-8')
            
            # Create temporary cookies file
            temp_cookie_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            temp_cookie_file.write(cookies_content)
            temp_cookie_file.close()
            
            print(f"✅ Cookies loaded from base64 environment variable to: {temp_cookie_file.name}")
            return temp_cookie_file.name
            
        except Exception as e:
            print(f"❌ Error decoding cookies from base64: {e}")
    
    # Check common paths as last resort
    common_paths = [
        '/etc/secrets/cookies.txt',
        os.path.join(os.getcwd(), 'cookies.txt'),
        os.path.join(os.getcwd(), 'cookies', 'cookies.txt'),
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            print(f"✅ Cookies found at common path: {path}")
            return path
    
    print("❌ No cookies found. YouTube downloads may be limited.")
    return None

# Global cookie file path
COOKIE_FILE = setup_cookies()

def extract_spotify_id(url, type):
    """Extract Spotify ID from URL"""
    if 'spotify.com' in url:
        if type == 'track':
            parts = url.split('/track/')
            if len(parts) > 1:
                return parts[1].split('?')[0]
        elif type == 'album':
            parts = url.split('/album/')
            if len(parts) > 1:
                return parts[1].split('?')[0]
        elif type == 'playlist':
            parts = url.split('/playlist/')
            if len(parts) > 1:
                return parts[1].split('?')[0]
    else:
        return url
    return None

def detect_spotify_type(url):
    """Detect if URL is track, album, or playlist"""
    if 'track' in url:
        return 'track'
    elif 'album' in url:
        return 'album'
    elif 'playlist' in url:
        return 'playlist'
    return None

def format_duration(ms):
    """Convert milliseconds to minutes:seconds"""
    seconds = int((ms / 1000) % 60)
    minutes = int((ms / (1000 * 60)) % 60)
    return f"{minutes}:{seconds:02d}"

def search_youtube_query(track_name, artist_name):
    """Create search query for YouTube"""
    return f"{track_name} {artist_name} official audio"

def download_full_song(track_info, download_id):
    """Download full song from YouTube using yt-dlp"""
    try:
        query = search_youtube_query(track_info['name'], track_info['artists'][0]['name'])
        
        # Base yt-dlp options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(tempfile.gettempdir(), f'{download_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'progress_hooks': [lambda d: progress_hook(d, download_id)],
        }
        
        # Add cookies if available
        if COOKIE_FILE and os.path.exists(COOKIE_FILE):
            print(f"🍪 Using cookies from: {COOKIE_FILE}")
            ydl_opts['cookiefile'] = COOKIE_FILE
        else:
            print("⚠️ No cookie file found. Using fallback options...")
            # Fallback options when no cookies are available
            ydl_opts.update({
                'extract_flat': False,
                'ignoreerrors': True,
                'no_check_certificate': True,
                'prefer_ffmpeg': True,
                'geo_bypass': True,
            })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            download_progress[download_id]['status'] = 'searching'
            download_progress[download_id]['progress'] = 0
            
            try:
                # Search and download
                print(f"🔍 Searching YouTube for: {query}")
                ydl.download([f"ytsearch:{query}"])
                download_progress[download_id]['status'] = 'completed'
                download_progress[download_id]['progress'] = 100
                print(f"✅ Download completed for: {track_info['name']}")
                
            except Exception as download_error:
                print(f"❌ Download error: {download_error}")
                
                # If download fails, try alternative approach without cookies
                if 'cookiefile' in ydl_opts:
                    print("🔄 Retrying without cookies...")
                    try:
                        # Remove cookies and retry
                        retry_opts = ydl_opts.copy()
                        retry_opts.pop('cookiefile', None)
                        retry_opts.update({
                            'extract_flat': False,
                            'ignoreerrors': True,
                            'no_check_certificate': True,
                            'prefer_ffmpeg': True,
                            'geo_bypass': True,
                        })
                        
                        with yt_dlp.YoutubeDL(retry_opts) as ydl_retry:
                            ydl_retry.download([f"ytsearch:{query}"])
                            download_progress[download_id]['status'] = 'completed'
                            download_progress[download_id]['progress'] = 100
                            print(f"✅ Retry successful for: {track_info['name']}")
                    except Exception as retry_error:
                        print(f"❌ Retry also failed: {retry_error}")
                        download_progress[download_id]['status'] = 'error'
                        download_progress[download_id]['error'] = f"Download failed: {str(retry_error)}"
                else:
                    download_progress[download_id]['status'] = 'error'
                    download_progress[download_id]['error'] = f"Download failed: {str(download_error)}"
            
    except Exception as e:
        print(f"💥 Unexpected error in download_full_song: {e}")
        download_progress[download_id]['status'] = 'error'
        download_progress[download_id]['error'] = f"Unexpected error: {str(e)}"

def progress_hook(d, download_id):
    """Progress hook for yt-dlp"""
    if d['status'] == 'downloading':
        if '_percent_str' in d:
            percent = d['_percent_str'].strip().replace('%', '')
            try:
                download_progress[download_id]['progress'] = float(percent)
                download_progress[download_id]['status'] = 'downloading'
            except:
                download_progress[download_id]['progress'] = 0
    elif d['status'] == 'finished':
        download_progress[download_id]['filename'] = d['filename']
        download_progress[download_id]['status'] = 'processing'

@app.route('/')
def index():
    return jsonify({
        'message': 'SpotiDL API is running!',
        'endpoints': {
            'POST /api/fetch': 'Fetch Spotify metadata',
            'POST /api/download/full/track': 'Download full track from YouTube',
            'GET /api/download/progress/<download_id>': 'Check download progress',
            'GET /api/download/file/<download_id>': 'Download completed file'
        },
        'cookies_status': 'Available' if COOKIE_FILE and os.path.exists(COOKIE_FILE) else 'Not available',
        'cookie_file_path': COOKIE_FILE if COOKIE_FILE else 'Not set'
    })

@app.route('/api/fetch', methods=['POST'])
def fetch_spotify_data():
    try:
        data = request.get_json()
        url = data.get('url', '').strip()

        if not url:
            return jsonify({'error': 'No URL provided'}), 400

        spotify_type = detect_spotify_type(url)
        if not spotify_type:
            return jsonify({'error': 'Invalid Spotify URL. Must be track, album, or playlist'}), 400

        spotify_id = extract_spotify_id(url, spotify_type)
        if not spotify_id:
            return jsonify({'error': 'Could not extract Spotify ID from URL'}), 400

        result = {'type': spotify_type}

        if spotify_type == 'track':
            track = sp.track(spotify_id)
            album = sp.album(track['album']['id'])

            result.update({
                'title': track['name'],
                'artists': [artist['name'] for artist in track['artists']],
                'album': track['album']['name'],
                'duration': format_duration(track['duration_ms']),
                'duration_ms': track['duration_ms'],
                'release_date': track['album']['release_date'],
                'cover_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
                'preview_url': track['preview_url'],
                'external_url': track['external_urls']['spotify'],
                'composers': [artist['name'] for artist in track['artists']],
                'genres': album.get('genres', []),
                'spotify_id': track['id']
            })

        elif spotify_type == 'album':
            album = sp.album(spotify_id)
            tracks = sp.album_tracks(spotify_id)

            album_tracks = []
            for item in tracks['items']:
                track_data = {
                    'id': item['id'],
                    'title': item['name'],
                    'artists': [artist['name'] for artist in item['artists']],
                    'duration': format_duration(item['duration_ms']),
                    'duration_ms': item['duration_ms'],
                    'track_number': item['track_number'],
                    'preview_url': item['preview_url'],
                    'external_url': item['external_urls']['spotify'],
                    'spotify_id': item['id']
                }
                album_tracks.append(track_data)

            result.update({
                'title': album['name'],
                'artists': [artist['name'] for artist in album['artists']],
                'release_date': album['release_date'],
                'total_tracks': album['total_tracks'],
                'cover_art': album['images'][0]['url'] if album['images'] else None,
                'external_url': album['external_urls']['spotify'],
                'genres': album.get('genres', []),
                'tracks': album_tracks,
                'spotify_id': album['id']
            })

        elif spotify_type == 'playlist':
            playlist = sp.playlist(spotify_id)
            tracks_data = sp.playlist_tracks(spotify_id)

            playlist_tracks = []
            for item in tracks_data['items']:
                if item['track']:
                    track = item['track']
                    track_data = {
                        'id': track['id'],
                        'title': track['name'],
                        'artists': [artist['name'] for artist in track['artists']],
                        'duration': format_duration(track['duration_ms']),
                        'duration_ms': track['duration_ms'],
                        'album': track['album']['name'],
                        'preview_url': track['preview_url'],
                        'external_url': track['external_urls']['spotify'],
                        'spotify_id': track['id']
                    }
                    playlist_tracks.append(track_data)

            result.update({
                'title': playlist['name'],
                'description': playlist.get('description', ''),
                'owner': playlist['owner']['display_name'],
                'total_tracks': playlist['tracks']['total'],
                'cover_art': playlist['images'][0]['url'] if playlist['images'] else None,
                'external_url': playlist['external_urls']['spotify'],
                'tracks': playlist_tracks,
                'spotify_id': playlist['id']
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f'Failed to fetch data: {str(e)}'}), 500

@app.route('/api/download/full/track', methods=['POST'])
def download_full_track():
    try:
        data = request.get_json()
        spotify_id = data.get('spotify_id')
        
        if not spotify_id:
            return jsonify({'error': 'No track ID provided'}), 400
        
        # Get track info from Spotify
        track = sp.track(spotify_id)
        
        # Generate unique download ID
        download_id = f"full_{spotify_id}_{int(threading.current_thread().ident)}"
        
        # Start download in background thread
        thread = threading.Thread(
            target=download_full_song,
            args=(track, download_id)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'download_id': download_id,
            'track_name': track['name'],
            'artists': [artist['name'] for artist in track['artists']],
            'status': 'started',
            'cookies_available': COOKIE_FILE and os.path.exists(COOKIE_FILE)
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to start download: {str(e)}'}), 500

@app.route('/api/download/progress/<download_id>')
def get_download_progress(download_id):
    progress = download_progress.get(download_id, {})
    return jsonify(progress)

@app.route('/api/download/file/<download_id>')
def download_file(download_id):
    try:
        progress = download_progress.get(download_id, {})
        
        if progress.get('status') != 'completed':
            return jsonify({'error': 'Download not completed'}), 400
        
        filename = progress.get('filename', '').replace('.webm', '.mp3').replace('.m4a', '.mp3')
        
        if not os.path.exists(filename):
            return jsonify({'error': 'File not found'}), 404
        
        # Clean up the progress entry
        if download_id in download_progress:
            del download_progress[download_id]
        
        safe_name = f"{os.path.basename(filename)}"
        return send_file(filename, as_attachment=True, download_name=safe_name)
        
    except Exception as e:
        return jsonify({'error': f'Failed to download file: {str(e)}'}), 500

# Keep the existing preview download endpoints
@app.route('/api/download/<spotify_type>/<spotify_id>')
def download_previews(spotify_type, spotify_id):
    try:
        if spotify_type not in ['album', 'playlist']:
            return jsonify({'error': 'Invalid type for bulk download'}), 400

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f'spotiDL_{spotify_type}_{spotify_id}.zip')

        if spotify_type == 'album':
            tracks_data = sp.album_tracks(spotify_id)
            title = sp.album(spotify_id)['name']
        else:
            tracks_data = sp.playlist_tracks(spotify_id)
            title = sp.playlist(spotify_id)['name']

        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for item in tracks_data['items']:
                track = item['track'] if spotify_type == 'playlist' else item
                if track and track.get('preview_url'):
                    try:
                        audio_content = download_preview(track['preview_url'])
                        if audio_content:
                            safe_name = f"{track['name']} - {', '.join([a['name'] for a in track['artists']])}.mp3"
                            safe_name = "".join(c for c in safe_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            zipf.writestr(safe_name, audio_content)
                    except Exception as e:
                        print(f"Error downloading {track['name']}: {e}")
                        continue

        return send_file(zip_path, as_attachment=True, download_name=f'spotiDL_{title}_previews.zip')

    except Exception as e:
        return jsonify({'error': f'Failed to create download: {str(e)}'}), 500

@app.route('/api/download/track/<track_id>')
def download_track_preview(track_id):
    try:
        track = sp.track(track_id)
        if not track.get('preview_url'):
            return jsonify({'error': 'No preview available for this track'}), 404

        audio_content = download_preview(track['preview_url'])
        if audio_content:
            safe_name = f"{track['name']} - {', '.join([a['name'] for a in track['artists']])}.mp3"
            safe_name = "".join(c for c in safe_name if c.isalnum() or c in (' ', '-', '_')).rstrip()

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_file.write(audio_content)
            temp_file.close()

            return send_file(temp_file.name, as_attachment=True, download_name=safe_name)
        else:
            return jsonify({'error': 'Failed to download preview'}), 500

    except Exception as e:
        return jsonify({'error': f'Failed to download track: {str(e)}'}), 500

def download_preview(url):
    """Download preview MP3 file"""
    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"Error downloading preview: {e}")
    return None

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
