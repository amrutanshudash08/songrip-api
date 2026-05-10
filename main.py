import os
import re
from urllib.parse import urlparse, urlunparse
from flask import Flask, request, jsonify
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

app = Flask(__name__)

ALLOWED = [
    "smule.com",
    "starmakerstudios.com",
    "starmaker.us",
    "yokee.com",
    "singsnap.com",
]

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    path = path.replace('/sing-recording/', '/recording/')
    return urlunparse(p._replace(path=path, query='', fragment=''))

@app.route('/')
def index():
    return jsonify(status='SongRip API is running')

@app.route('/rip', methods=['POST', 'OPTIONS'])
def rip():
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response, 200

    url = clean_url(request.json.get('url', ''))
    if not url:
        return jsonify(error='No URL found.'), 400
    if not any(d in url for d in ALLOWED):
        return jsonify(error='Unsupported platform.'), 400

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'skip_download': True,
            # Auto-pick any available impersonation target (uses curl-cffi)
            'impersonate': ImpersonateTarget(),
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        audio_url = info.get('url')
        ext = info.get('ext', 'm4a')

        if not audio_url and info.get('formats'):
            best = max(
                (f for f in info['formats'] if f.get('acodec') != 'none'),
                key=lambda f: f.get('abr') or 0
            )
            audio_url = best.get('url')
            ext = best.get('ext', 'm4a')

        if not audio_url:
            return jsonify(error='Could not extract audio.'), 422

        title = re.sub(r'[^\w\s\-()]', '', info.get('title', 'recording')).strip()[:80] or 'recording'

        response = jsonify(url=audio_url, filename=f'{title}.{ext}', title=title, ext=ext)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200

    except Exception as e:
        response = jsonify(error=str(e)[:300])
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 422

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
