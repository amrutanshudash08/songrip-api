import os
import re
import json
import requests
from urllib.parse import urlparse, urlunparse, parse_qs
from flask import Flask, request, jsonify

app = Flask(__name__)

SCRAPER_API_KEY = os.environ.get('SCRAPER_API_KEY', '')

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    path = path.replace('/sing-recording/', '/recording/')
    return urlunparse(p._replace(path=path, query='', fragment=''))

def find_smule_cdn(text):
    patterns = [
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>\\]+\.m4a(?:\?[^\s"\'<>\\]*)?',
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>\\]+\.mp4(?:\?[^\s"\'<>\\]*)?',
        r'https://feed\.smule\.com/[^\s"\'<>\\]+\.m4a(?:\?[^\s"\'<>\\]*)?',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0).rstrip('\\')
    return None

def scrape(url, render=False, ultra=False):
    params = {
        'api_key': SCRAPER_API_KEY,
        'url': url,
        'premium': 'true',
    }
    if render:
        params['render'] = 'true'
    if ultra:
        params['ultra_premium'] = 'true'
    resp = requests.get('http://api.scraperapi.com', params=params, timeout=60)
    return resp

def extract_smule(url):
    if not SCRAPER_API_KEY:
        raise Exception('SCRAPER_API_KEY not set. Add it in Railway Variables.')

    # Also try the short URL format which may be less protected
    key_match = re.search(r'(\d+_\d+)', url)
    short_url = f'https://www.smule.com/recording/p/{key_match.group(1)}' if key_match else None

    html = None
    title = 'recording'

    # Strategy 1: plain fetch (no JS render) — sometimes bypasses CF better
    for attempt_url in [url, short_url]:
        if not attempt_url:
            continue
        try:
            resp = scrape(attempt_url, render=False, ultra=False)
            if resp.ok and 'cdn.smule.com' in resp.text:
                html = resp.text
                break
            elif resp.ok and 'smule' in resp.text.lower() and 'cloudflare' not in resp.text.lower():
                html = resp.text
                break
        except Exception:
            continue

    # Strategy 2: ultra premium no render
    if not html:
        try:
            resp = scrape(url, render=False, ultra=True)
            if resp.ok and len(resp.text) > 5000:
                html = resp.text
        except Exception:
            pass

    # Strategy 3: render=true + ultra premium
    if not html:
        try:
            resp = scrape(url, render=True, ultra=True)
            if resp.ok and len(resp.text) > 5000:
                html = resp.text
        except Exception:
            pass

    if not html:
        raise Exception('ScraperAPI could not bypass Smule\'s Cloudflare protection. Try sownloader.com for now.')

    # Extract title
    og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if og_title:
        title = og_title.group(1)

    # Find audio URL
    audio_url = find_smule_cdn(html)

    if not audio_url:
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
        if nd:
            try:
                audio_url = find_smule_cdn(json.dumps(json.loads(nd.group(1))))
            except Exception:
                pass

    if not audio_url:
        # Check if we got the actual page or a challenge
        is_challenge = any(x in html for x in ['cf-browser-verification', 'checking your browser', 'enable javascript', 'cf_chl'])
        if is_challenge:
            raise Exception('Cloudflare blocked the request. Smule is heavily protected — try sownloader.com for Smule recordings.')
        raise Exception('Got the page but could not find audio URL. The recording may be private or deleted.')

    ext = 'm4a' if '.m4a' in audio_url else 'mp4'
    safe_title = re.sub(r'[^\w\s\-()]', '', title).strip()[:80] or 'recording'
    return audio_url, safe_title, ext

def extract_starmaker(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    recording_id = (params.get('recordingId') or [''])[0]
    if not recording_id:
        raise Exception('Could not find recording ID in StarMaker link.')

    resp = requests.get(
        f'https://www.starmakerstudios.com/api/social/recording/info?recordingId={recording_id}',
        headers={'User-Agent': 'Mozilla/5.0'}, timeout=15
    )
    if not resp.ok:
        raise Exception(f'StarMaker API error ({resp.status_code})')

    data = resp.json()
    s = json.dumps(data)
    title = data.get('data', {}).get('recordingName') or data.get('data', {}).get('name') or 'recording'
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
    return audio_url, re.sub(r'[^\w\s\-()]', '', title).strip()[:80] or 'recording', ext

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

        response = jsonify(url=audio_url, filename=f'{title}.{ext}', title=title, ext=ext)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200

    except Exception as e:
        response = jsonify(error=str(e)[:400])
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 422

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
