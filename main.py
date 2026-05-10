import os
import re
import json
import requests
from urllib.parse import urlparse, urlunparse
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

def extract_smule(url):
    audio_url = None
    title = 'recording'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        # Intercept audio CDN responses
        def on_response(response):
            nonlocal audio_url
            u = response.url
            if any(ext in u for ext in ['.m4a', '.mp4', '.aac', '.mp3']) and not audio_url:
                audio_url = u

        page.on('response', on_response)

        try:
            page.goto(url, wait_until='networkidle', timeout=30000)
            title = page.title() or 'recording'

            # Also dig through page JSON for media_url
            if not audio_url:
                content = page.content()
                next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', content)
                if next_data:
                    try:
                        s = json.dumps(json.loads(next_data.group(1)))
                        for pattern in [
                            r'"media_url"\s*:\s*"([^"]+)"',
                            r'"(https://[^"]+\.(?:m4a|mp4|aac|mp3)(?:\?[^"]*)?)"',
                        ]:
                            m = re.search(pattern, s)
                            if m:
                                audio_url = m.group(1)
                                break
                    except Exception:
                        pass
        finally:
            browser.close()

    if not audio_url:
        raise Exception('Could not find audio. The recording may be private or unavailable.')

    ext = re.search(r'\.(m4a|mp4|aac|mp3)', audio_url)
    ext = ext.group(1) if ext else 'm4a'
    safe_title = re.sub(r'[^\w\s\-()]', '', title).strip()[:80] or 'recording'
    return audio_url, safe_title, ext

def extract_starmaker(url):
    # Use URLSearchParams-style parsing to handle complex query strings
    parsed = urlparse(url)
    # Handle both ? and & separated params
    from urllib.parse import parse_qs
    params = parse_qs(parsed.query)
    recording_id = (params.get('recordingId') or [''])[0]
    
    if not recording_id:
        raise Exception('Could not find recording ID in StarMaker link.')

    data = resp.json()
    s = json.dumps(data)
    title = data.get('data', {}).get('recordingName') or data.get('data', {}).get('name') or 'recording'
    audio_url = data.get('data', {}).get('recordingUrl') or data.get('data', {}).get('audioUrl')

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
