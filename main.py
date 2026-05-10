import os
import re
import json
import requests
from urllib.parse import urlparse, urlunparse, parse_qs, quote
from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

def find_smule_cdn(text):
    """Find *.cdn.smule.com audio URLs in any text blob."""
    # Look for audio CDN URLs (updated pattern per community fix)
    patterns = [
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>]+\.m4a(?:\?[^\s"\'<>]*)?',
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>]+\.mp4(?:\?[^\s"\'<>]*)?',
        r'https://feed\.smule\.com/[^\s"\'<>]+\.m4a(?:\?[^\s"\'<>]*)?',
        r'https://feed\.smule\.com/[^\s"\'<>]+\.mp4(?:\?[^\s"\'<>]*)?',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0).rstrip('\\')
    return None

def try_sownloader_api(url):
    """Try the old sownloader.com API endpoint — still works for many recordings."""
    try:
        resp = requests.get(
            f'https://sownloader.com/index.php?url={quote(url, safe="")}',
            headers=HEADERS,
            timeout=20
        )
        if resp.ok and 'cdn.smule.com' in resp.text:
            audio = find_smule_cdn(resp.text)
            if audio:
                # Try to get title from the page
                title_match = re.search(r'<title>([^<]+)</title>', resp.text, re.I)
                title = title_match.group(1) if title_match else 'recording'
                title = re.sub(r'\s*[-|]\s*Sownloader.*$', '', title, flags=re.I).strip()
                return audio, title
    except Exception:
        pass
    return None, None

def try_playwright(url):
    """Fallback: use Playwright to render the page and intercept the CDN URL."""
    audio_url = None
    title = 'recording'

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        context = browser.new_context(
            user_agent=HEADERS['User-Agent'],
            viewport={'width': 1280, 'height': 720},
        )
        page = context.new_page()

        def on_response(response):
            nonlocal audio_url
            u = response.url
            if not audio_url and 'cdn.smule.com' in u and any(ext in u for ext in ['.m4a', '.mp4']):
                audio_url = u
            if not audio_url and 'feed.smule.com' in u and any(ext in u for ext in ['.m4a', '.mp4']):
                audio_url = u

        page.on('response', on_response)

        try:
            page.goto(url, wait_until='commit', timeout=20000)
            page.wait_for_timeout(5000)
            title = page.title() or 'recording'

            if not audio_url:
                content = page.content()
                audio_url = find_smule_cdn(content)

                # Also check __NEXT_DATA__
                if not audio_url:
                    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', content)
                    if nd:
                        try:
                            audio_url = find_smule_cdn(json.dumps(json.loads(nd.group(1))))
                        except Exception:
                            pass
        finally:
            browser.close()

    return audio_url, title

def extract_smule(url):
    # Strategy 1: Sownloader API (fast, no Playwright needed)
    audio_url, title = try_sownloader_api(url)

    # Strategy 2: Playwright fallback
    if not audio_url:
        audio_url, title = try_playwright(url)

    if not audio_url:
        raise Exception('Could not extract audio from Smule. The recording may be private.')

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
