import os
import re
import json
import requests
from urllib.parse import urlparse, urlunparse
from flask import Flask, request, jsonify

app = Flask(__name__)

MOBILE_HEADERS = {
    'User-Agent': 'Smule/8.6.8 (iPhone; iOS 17.0; Scale/3.00)',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
}

WEB_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    path = path.replace('/sing-recording/', '/recording/')
    return urlunparse(p._replace(path=path, query='', fragment=''))

def find_audio_in_json(s):
    for pattern in [
        r'"media_url"\s*:\s*"([^"]+)"',
        r'"(https://[^"]+smule[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
        r'"(https://feed\.smule\.com[^"]+)"',
        r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
    ]:
        m = re.search(pattern, s)
        if m:
            return m.group(1)
    return None

def extract_smule(url):
    # Extract recording key e.g. 804005285_5007707421
    key_match = re.search(r'(\d+_\d+)', url)
    if not key_match:
        raise Exception('Could not find recording ID in URL.')
    key = key_match.group(1)

    title = 'recording'
    audio_url = None

    # Strategy 1: Smule mobile API (bypasses Cloudflare)
    try:
        resp = requests.get(
            f'https://www.smule.com/api/v0/performances/{key}',
            headers=MOBILE_HEADERS,
            timeout=15
        )
        if resp.ok:
            data = resp.json()
            s = json.dumps(data)
            title = (
                data.get('title') or
                data.get('data', {}).get('title') or
                data.get('performance', {}).get('title') or
                title
            )
            audio_url = find_audio_in_json(s)
    except Exception:
        pass

    # Strategy 2: OEmbed API (public, no auth)
    if not audio_url:
        try:
            resp = requests.get(
                f'https://www.smule.com/oembed?url={url}&format=json',
                headers=WEB_HEADERS,
                timeout=15
            )
            if resp.ok:
                data = resp.json()
                s = json.dumps(data)
                title = data.get('title', title)
                audio_url = find_audio_in_json(s)
        except Exception:
            pass

    # Strategy 3: Smule recording JSON endpoint
    if not audio_url:
        try:
            resp = requests.get(
                f'https://www.smule.com/recording/{key}.json',
                headers=WEB_HEADERS,
                timeout=15
            )
            if resp.ok:
                data = resp.json()
                s = json.dumps(data)
                title = data.get('title', title)
                audio_url = find_audio_in_json(s)
        except Exception:
            pass

    # Strategy 4: Twitter card meta tags (lighter page, may bypass CF)
    if not audio_url:
        try:
            resp = requests.get(
                f'{url}/twitter',
                headers=WEB_HEADERS,
                timeout=20
            )
            if resp.ok:
                html = resp.text
                og_audio = re.search(r'property="og:audio(?::url)?"\s+content="([^"]+)"', html)
                if og_audio:
                    audio_url = og_audio.group(1)
                og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                if og_title:
                    title = og_title.group(1)
                if not audio_url:
                    audio_url = find_audio_in_json(html)
        except Exception:
            pass

    # Strategy 5: feed.smule.com CDN guess from key
    if not audio_url:
        try:
            account_id, perf_id = key.split('_')
            cdn_url = f'https://feed.smule.com/s3-media4/renditions/{account_id}/{perf_id}/index.m3u8'
            resp = requests.head(cdn_url, headers=WEB_HEADERS, timeout=10)
            if resp.ok:
                audio_url = cdn_url
        except Exception:
            pass

    if not audio_url:
        raise Exception(
            'Smule is blocking automated access. '
            'Try sharing from the Smule app directly using the Copy Link option.'
        )

    ext = re.search(r'\.(m4a|mp4|aac|mp3|m3u8)', audio_url)
    ext = ext.group(1) if ext else 'm4a'
    return audio_url, title, ext

def extract_starmaker(url):
    p = urlparse(url)
    params = dict(pair.split('=', 1) for pair in p.query.split('&') if '=' in pair)
    recording_id = params.get('recordingId')
    if not recording_id:
        raise Exception('Could not find recording ID in StarMaker link.')

    resp = requests.get(
        f'https://www.starmakerstudios.com/api/social/recording/info?recordingId={recording_id}',
        headers=WEB_HEADERS,
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
        response = jsonify(error=str(e)[:400])
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 422

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
