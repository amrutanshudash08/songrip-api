import os
import re
import json
import asyncio
import requests
import nodriver as uc
from urllib.parse import urlparse, urlunparse, parse_qs
from flask import Flask, request, jsonify

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

def find_smule_cdn(text):
    patterns = [
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>\\]+\.m4a(?:\?[^\s"\'<>\\]*)?',
        r'https://[a-z0-9\-]+\.cdn\.smule\.com/[^\s"\'<>\\]+\.mp4(?:\?[^\s"\'<>\\]*)?',
        r'https://feed\.smule\.com/[^\s"\'<>\\]+\.m4a(?:\?[^\s"\'<>\\]*)?',
        r'https://feed\.smule\.com/[^\s"\'<>\\]+\.mp4(?:\?[^\s"\'<>\\]*)?',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0).rstrip('\\')
    return None

async def extract_smule_async(url):
    audio_url = None
    title = 'recording'

    browser = await uc.start(
        headless=True,
        browser_executable_path='/usr/bin/google-chrome',
        browser_args=[
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--autoplay-policy=no-user-gesture-required',
        ]
    )

    try:
        tab = await browser.get(url)

        # Wait for page to fully load and JS to execute
        await asyncio.sleep(6)

        # Get page title
        title = await tab.evaluate('document.title') or 'recording'

        # Get full page HTML
        content = await tab.get_content()

        # Check for audio CDN URL in page HTML
        audio_url = find_smule_cdn(content)

        # Check __NEXT_DATA__
        if not audio_url:
            try:
                nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', content)
                if nd:
                    audio_url = find_smule_cdn(json.dumps(json.loads(nd.group(1))))
            except Exception:
                pass

        # Try clicking play button
        if not audio_url:
            for selector in [
                'button[aria-label*="play" i]',
                'button[aria-label*="Play" i]',
                '[class*="play-btn"]',
                '[class*="PlayBtn"]',
                '[class*="player"] button',
                'button[class*="play"]',
            ]:
                try:
                    el = await tab.find(selector, timeout=2)
                    if el:
                        await el.click()
                        await asyncio.sleep(4)
                        content = await tab.get_content()
                        audio_url = find_smule_cdn(content)
                        if audio_url:
                            break
                except Exception:
                    continue

        # Also check network requests via JS
        if not audio_url:
            try:
                result = await tab.evaluate('''
                    (() => {
                        const entries = performance.getEntriesByType("resource");
                        for (const e of entries) {
                            if ((e.name.includes("cdn.smule.com") || e.name.includes("feed.smule.com"))
                                && (e.name.includes(".m4a") || e.name.includes(".mp4"))) {
                                return e.name;
                            }
                        }
                        return null;
                    })()
                ''')
                if result:
                    audio_url = result
            except Exception:
                pass

    finally:
        browser.stop()

    return audio_url, title

def extract_smule(url):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        audio_url, title = loop.run_until_complete(extract_smule_async(url))
        loop.close()
    except Exception as e:
        raise Exception(f'Browser error: {str(e)[:200]}')

    if not audio_url:
        raise Exception('Could not extract audio from Smule. The recording may be private or unavailable.')

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
