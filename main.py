import os
import re
import json
import requests
from urllib.parse import urlparse, urlunparse, parse_qs
from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

def clean_url(raw):
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")
    p = urlparse(url)
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    path = path.replace('/sing-recording/', '/recording/')
    return urlunparse(p._replace(path=path, query='', fragment=''))

def find_audio_in_text(text):
    """Search for audio CDN URLs in any text/JSON blob."""
    patterns = [
        r'"media_url"\s*:\s*"([^"]+)"',
        r'"(https://[^"]+smule[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
        r'"(https://feed\.smule\.com[^"]+)"',
        r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            url = m.group(1)
            # Skip if it looks like a thumbnail or image
            if any(x in url for x in ['thumb', 'cover', 'pic', 'avatar', 'image']):
                continue
            return url
    return None

def extract_smule(url):
    audio_url = None
    title = 'recording'

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
        )
        page = context.new_page()

        # Intercept audio CDN responses in the background
        def on_response(response):
            nonlocal audio_url
            u = response.url
            if not audio_url and any(ext in u for ext in ['.m4a', '.mp4', '.aac', '.mp3']):
                if not any(x in u for x in ['thumb', 'cover', 'pic', 'avatar']):
                    audio_url = u

        page.on('response', on_response)

        try:
            # Use 'commit' — returns as soon as first byte received, very fast
            page.goto(url, wait_until='commit', timeout=20000)

            # Wait a few seconds for SSR HTML + any audio requests
            page.wait_for_timeout(4000)

            content = page.content()
            title = page.title() or 'recording'

            # Parse __NEXT_DATA__ — Smule uses Next.js so audio URL is SSR'd into the page
            if not audio_url:
                next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', content)
                if next_data:
                    try:
                        data = json.loads(next_data.group(1))
                        s = json.dumps(data)
                        audio_url = find_audio_in_text(s)

                        # Also try to get a better title from the JSON
                        title_match = re.search(r'"title"\s*:\s*"([^"]{5,100})"', s)
                        if title_match:
                            title = title_match.group(1)
                    except Exception:
                        pass

            # Fallback: search raw HTML
            if not audio_url:
                audio_url = find_audio_in_text(content)

            # Fallback: og:audio meta tag
            if not audio_url:
                og = re.search(r'property="og:audio(?::url)?"\s+content="([^"]+)"', content)
                if og:
                    audio_url = og.group(1)

        finally:
            browser.close()

    if not audio_url:
        raise Exception('Could not find audio. The recording may be private or Smule is blocking access.')

    ext = re.search(r'\.(m4a|mp4|aac|mp3)', audio_url)
    ext = ext.group(1) if ext else 'm4a'
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
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=15
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
    safe_title = re.sub(r'[^\w\s\-()]', '', title).strip()[:80] or 'recording'
    return audio_url, safe_title, ext

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
