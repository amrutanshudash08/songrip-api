import os, re
from urllib.parse import urlparse, urlunparse
from flask import Flask, request, jsonify
import yt_dlp

app = Flask(__name__)

ALLOWED = ["smule.com", "starmakerstudios.com", "starmaker.us", "yokee.com", "singsnap.com"]

def clean_url(raw):
    # Extract URL from share message text
    m = re.search(r'https?://\S+', raw)
    if not m:
        return None
    url = m.group(0).rstrip("'\".,)")

    # Parse and strip junk
    p = urlparse(url)
    # Remove social suffixes and UTM params
    path = re.sub(r'/(twitter|facebook|instagram|whatsapp|copy|embed|frame|box)(/.*)?$', '', p.path)
    return urlunparse(p._replace(path=path, query='', fragment=''))

@app.route('/')
def index():
    return jsonify(status='SongRip API is running')

@app.route('/rip', methods=['POST', 'OPTIONS'])
def rip():
    if request.method == 'OPTIONS':
        return '', 200, {
            'Acces
