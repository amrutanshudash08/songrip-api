import os
import re
import json
from urllib.parse import urlparse, urlunparse
from flask import Flask, request, jsonify
import cloudscraper

app = Flask(__name__)

SCRAPER = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
)

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    path = path.replace('/sing-recording/', '/recording/')
    return urlunparse(p._replace(path=path, query='', fragment=''))

def extract_smule(url):
    resp = SCRAPER.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Title from og:title
    title = 'recording'
    og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if og_title:
        title = og_title.group(1)

    audio_url = None

    # Strategy 1: __NEXT_DATA__ JSON blob
    next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
    if next_data:
        try:
            s = json.dumps(json.loads(next_data.group(1)))
            for pattern in [
                r'"media_url"\s*:\s*"([^"]+)"',
                r'"(https://[^"]+smule[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
                r'"(https://storage\.googleapis[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
                r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
            ]:
                m = re.search(pattern, s)
                if m:
                    audio_url = m.group(1)
                    break
        except Exception:
            pass

    # Strategy 2: og:audio meta tag
    if not audio_url:
        m = re.search(r'property="og:audio(?::url)?"\s+content="([^"]+)"', html)
        if m:
            audio_url = m.group(1)

    # Strategy 3: og:video
    if not audio_url:
        m = re.search(r'property="og:video(?::url)?"\s+content="([^"]+)"', html)
        if m and '/frame' not in m.group(1):
            audio_url = m.group(1)

    # Strategy 4: raw audio URL anywhere in page
    if not audio_url:
        m = re.search(r'https://[^\s"\'<>]+\.(?:m4a|mp4|aac|mp3)(?:\?[^\s"\'<>]*)?', html)
        if m:
            audio_url = m.group(0)

    # Strategy 5: hit Smule API directly using recording ID
    if not audio_url:
        ids = re.search(r'(\d+_\d+)', url)
        if ids:
            try:
                api = SCRAPER.get(
                    f'https://www.smule.com/api/v0/performances/{ids.group(1)}',
                    timeout=15
                )
                if api.ok:
                    data = api.json()
                    audio_url = (
                        data.get('media_url') or
                        data.get('data', {}).get('media_url') or
                        data.get('performance', {}).get('media_url')
                    )
                    if not audio_url:
                        s = json.dumps(data)
                        m = re.search(r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"', s)
                        if m:
                            audio_url = m.group(1)
            except Exception:
                pass

    if not audio_url:
        raise Exception('Could not find audio in this Smule page.')

    ext = re.search(r'\.(m4a|mp4|aac|mp3)', audio_url)
    ext = ext.group(1) if ext else 'm4a'
    return audio_url, title, ext

def extract_starmaker(url):
    p = urlparse(url)
    recording_id = dict(pair.split('=', 1) for pair in p.query.split('&') if '=' in pair).get('recordingId')
    if not recording_id:
        raise Exception('Could not find recording ID in StarMaker link.')

    resp = SCRAPER.get(
        f'https://www.starmakerstudios.com/api/social/recording/info?recordingId={recording_id}',
        timeout=15
    )
    if not resp.ok:
        raise Exception(f'StarMaker API error ({resp.status_code})')

    data = resp.json()
    s = json.dumps(data)

    title = (
        data.get('data', {}).get('recordingName') or
        data.get('data', {}).get('name') or
        'recording'
    )
    audio_url = (
        data.get('data', {}).get('recordingUrl') or
        data.get('data', {}).get('audioUrl') or
        data.get('data', {}).get('mediaUrl')
    )
    if not audio_url:
        m = re.search(r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"', s)
        if m:
            audio_url = m.group(1)

    if not audio_url:
        raise Exception('Could not extract audio from StarMaker.')

    ext = re.search(r'\.(m4a|mp4|aac|mp3)', audio_url)
    ext = ext.group(1) if ext else 'm4a'
    return audio_url, title, ext

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
        return jsonify(error='No URL found in the text you pasted.'), 400

    try:
        if 'smule.com' in url:
            audio_url, title, ext = extract_smule(url)
        elif 'starmaker' in url:
            audio_url, title, ext = extract_starmaker(url)
        else:
            return jsonify(error='Unsupported platform. Use Smule or StarMaker.'), 400

        safe_title = re.sub(r'[^\w\s\-()]', '', title).strip()[:80] or 'recording'
        response = jsonify(url=audio_url, filename=f'{safe_title}.{ext}', title=safe_title, ext=ext)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200

    except Exception as e:
        response = jsonify(error=str(e)[:300])
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 422

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
