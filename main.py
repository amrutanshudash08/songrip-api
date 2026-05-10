import re, json
from urllib.parse import urlparse, urlunparse
from flask import Flask, request, jsonify
import yt_dlp

app = Flask(__name__)

ALLOWED = ["smule.com", "starmakerstudios.com", "starmaker.us", "yokee.com", "singsnap.com"]

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m: return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame/box)/?$', '', p.path)
    return urlunparse(p._replace(path=path, query='', fragment=''))

@app.route('/rip', methods=['POST', 'OPTIONS'])
def rip():
    if request.method == 'OPTIONS':
        return '', 200, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type'}

    url = clean_url(request.json.get('url', ''))
    if not url: return jsonify(error='No URL found.'), 400
    if not any(d in url for d in ALLOWED): return jsonify(error='Unsupported platform.'), 400

    try:
        with yt_dlp.YoutubeDL({'format': 'bestaudio/best', 'quiet': True, 'noplaylist': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        audio_url = info.get('url')
        if not audio_url and info.get('formats'):
            best = max((f for f in info['formats'] if f.get('acodec') != 'none'), key=lambda f: f.get('abr') or 0)
            audio_url = best.get('url')
            info['ext'] = best.get('ext', 'm4a')

        if not audio_url: return jsonify(error='Could not extract audio.'), 422

        title = re.sub(r'[^\w\s\-()]', '', info.get('title', 'recording')).strip()[:80] or 'recording'
        ext = info.get('ext', 'm4a')
        return jsonify(url=audio_url, filename=f'{title}.{ext}', title=title, ext=ext), 200, {'Access-Control-Allow-Origin': '*'}

    except Exception as e:
        return jsonify(error=str(e)[:300]), 422, {'Access-Control-Allow-Origin': '*'}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
