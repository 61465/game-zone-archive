# main.py - نسخة مطورة مع أمان وأداء أفضل

import feedparser
import re
import socket
import sqlite3
import requests
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, session, make_response
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from user_agents import parse
import bleach
import logging
from logging.handlers import RotatingFileHandler

# --- إعدادات السيرفر المحسنة ---
socket.setdefaulttimeout(15)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-super-secret-key-change-in-production-2024'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# --- إعداد التخزين المؤقت ---
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300
})

# --- إعداد تحديد المعدل ---
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# --- إعداد التسجيل ---
if not app.debug:
    handler = RotatingFileHandler('gamezone.log', maxBytes=10000, backupCount=3)
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)

# --- إعدادات قاعدة البيانات ---
DB_PATH = 'radar.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            news_id TEXT PRIMARY KEY,
            swords INTEGER DEFAULT 0,
            shields INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            news_id TEXT,
            vote_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, news_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS image_cache (
            url TEXT PRIMARY KEY,
            image_url TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_votes_session ON user_votes(session_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_image_cache_fetched ON image_cache(fetched_at)')
    conn.commit()
    conn.close()

init_db()

# --- مصادر RSS ---
RSS_SOURCES = [
    {"name": "IGN",             "url": "https://feeds.feedburner.com/ign/all",                           "type": "official", "priority": 1},
    {"name": "GameSpot",        "url": "https://www.gamespot.com/feeds/game-news/",                      "type": "official", "priority": 1},
    {"name": "Eurogamer",       "url": "https://www.eurogamer.net/feed/news",                            "type": "official", "priority": 1},
    {"name": "Kotaku",          "url": "https://kotaku.com/rss",                                         "type": "official", "priority": 1},
    {"name": "Gematsu",         "url": "https://www.gematsu.com/feed",                                   "type": "leak",     "priority": 2},
    {"name": "VGC",             "url": "https://www.videogameschronicle.com/feed/",                      "type": "leak",     "priority": 2},
    {"name": "Insider Gaming",  "url": "https://insider-gaming.com/feed/",                               "type": "leak",     "priority": 2},
    {"name": "Reddit Leaks",    "url": "https://www.reddit.com/r/GamingLeaksAndRumors/new/.rss",         "type": "leak",     "priority": 2},
]

# --- دوال مساعدة ---

def generate_session_id():
    if 'user_id' not in session:
        session['user_id'] = hashlib.sha256(
            (str(time.time()) + str(request.remote_addr)).encode()
        ).hexdigest()[:16]
    return session['user_id']

def validate_input(text, max_length=200):
    if not text:
        return ""
    cleaned = bleach.clean(text, tags=[], strip=True)
    return cleaned[:max_length]

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

# --- جلب الصورة ---

def fetch_main_image(url):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT image_url FROM image_cache WHERE url = ? AND fetched_at > datetime('now', '-1 day')",
            (url,)
        )
        cached = c.fetchone()
        if cached:
            conn.close()
            return cached[0]

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')

        img_url = None
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get('content'):
            img_url = og_img['content']
        if not img_url:
            twitter_img = soup.find("meta", {"name": "twitter:image"})
            if twitter_img and twitter_img.get('content'):
                img_url = twitter_img['content']
        if not img_url:
            first_img = soup.find("article").find("img") if soup.find("article") else None
            if first_img and first_img.get('src'):
                img_url = first_img['src']
        if not img_url:
            img_url = "https://via.placeholder.com/500x280/0a0a0a/D4AF37?text=GAME+ZONE"

        c.execute(
            "INSERT OR REPLACE INTO image_cache (url, image_url) VALUES (?, ?)",
            (url, img_url)
        )
        conn.commit()
        conn.close()
        return img_url

    except Exception as e:
        app.logger.error(f"Image fetch error for {url}: {e}")
        return "https://via.placeholder.com/500x280/0a0a0a/D4AF37?text=GAME+ZONE"

# --- جلب الأخبار ---

@cache.memoize(timeout=300)
def get_gaming_news(category=None):
    all_articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    sources = sorted(RSS_SOURCES, key=lambda x: x['priority'])

    for source in sources:
        try:
            feed = feedparser.parse(source["url"], request_headers=headers)
            if not hasattr(feed, 'entries') or not feed.entries:
                continue

            for entry in feed.entries[:10]:
                news_id = hashlib.md5(entry.link.encode()).hexdigest()
                title = validate_input(entry.title, 150)

                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT swords, shields FROM reactions WHERE news_id=?", (news_id,))
                res = c.fetchone()
                swords, shields = res if res else (0, 0)
                conn.close()

                img_url = ""
                if hasattr(entry, 'media_content') and entry.media_content:
                    img_url = entry.media_content[0].get('url', '')
                elif hasattr(entry, 'links'):
                    for link in entry.links:
                        if link.get('type', '').startswith('image/'):
                            img_url = link.get('href', '')
                            break
                if not img_url and hasattr(entry, 'description'):
                    img_match = re.search(r'<img.+?src=["\'](.+?)["\']', entry.description)
                    if img_match:
                        img_url = img_match.group(1)
                if not img_url:
                    img_url = fetch_main_image(entry.link)

                # تصنيف الخبر
                news_category = source["type"]
                t = title.lower()
                if "playstation" in t or "ps5" in t or "sony" in t:
                    news_category = "ps"
                elif "xbox" in t or "microsoft" in t:
                    news_category = "xb"
                elif "pc" in t or "steam" in t or "nvidia" in t:
                    news_category = "pc"
                elif "leak" in t or "rumor" in t:
                    news_category = "leak"

                if category and category != "all" and news_category != category:
                    continue

                # ملخص نظيف
                summary = ""
                if hasattr(entry, 'summary'):
                    summary = BeautifulSoup(entry.summary, 'html.parser').get_text()[:300]

                score = (swords * 2) - shields

                all_articles.append({
                    'id':        news_id,
                    'title':     title,
                    'link':      entry.link,
                    'source':    source["name"],
                    'image':     img_url,
                    'type':      news_category,
                    'summary':   summary,
                    'swords':    swords,
                    'shields':   shields,
                    'score':     score,
                    'published': entry.get('published', datetime.now().isoformat())
                })

        except Exception as e:
            app.logger.error(f"Error fetching {source['name']}: {e}")
            continue

    all_articles.sort(key=lambda x: (x['published'], x['score']), reverse=True)
    return all_articles

# --- الصفحة الرئيسية ---

@app.route('/')
@limiter.limit("30 per minute")
def index():
    query    = validate_input(request.args.get('q', '').strip().lower(), 50)
    category = request.args.get('cat', 'all').strip().lower()
    valid_categories = ['all', 'leak', 'ps', 'xb', 'pc']
    category = category if category in valid_categories else 'all'

    all_news     = get_gaming_news(category if category != 'all' else None)
    display_news = all_news
    if query:
        display_news = [
            item for item in all_news
            if query in item['title'].lower() or query in item['source'].lower()
        ]

    response = make_response(render_template(
        'index.html',
        news=display_news,
        ticker=all_news[:30],
        query=query,
        current_category=category
    ))
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# ============================================================
# API للـ gamezone_v2.html — أُضيف هنا
# ============================================================

@app.route('/api/news')
@limiter.limit("30 per minute")
def api_news():
    """يُعيد الأخبار كـ JSON لـ gamezone_v2.html"""
    category = request.args.get('cat', None)
    news = get_gaming_news(category)

    formatted = []
    for item in news[:50]:
        # تصنيف اللون حسب المصدر
        color_map = {
            'IGN':            '#ff3b5c',
            'GameSpot':       '#1a9f29',
            'Eurogamer':      '#f4a11a',
            'Kotaku':         '#ff6600',
            'Gematsu':        '#00e5ff',
            'VGC':            '#a855f7',
            'Insider Gaming': '#ec4899',
            'Reddit Leaks':   '#ff4500',
        }
        source_color = color_map.get(item['source'], '#d4af37')

        formatted.append({
            'id':          item['id'],
            'tag':         item['type'],
            'category':    item['type'],
            'date':        item.get('published', '')[:10],
            'emoji':       '🎮',
            'image':       item.get('image', ''),
            'link':        item['link'],
            'sourceColor': source_color,
            'en': {
                'h':       item['title'],
                's':       item['source'],
                'summary': item.get('summary', '')
            },
            'ar': {
                'h':       item['title'],
                's':       item['source'],
                'summary': item.get('summary', '')
            },
            'swords':  item.get('swords',  0),
            'shields': item.get('shields', 0),
        })

    response = jsonify(formatted)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

# --- التصويت ---

@app.route('/api/react', methods=['POST'])
@limiter.limit("10 per minute")
def react():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid data"}), 400

        news_id   = validate_input(data.get("id"), 64)
        vote_type = data.get("type")

        if not news_id or vote_type not in ["sword", "shield"]:
            return jsonify({"error": "Invalid parameters"}), 400

        session_id = generate_session_id()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute(
            "SELECT vote_type FROM user_votes WHERE session_id = ? AND news_id = ?",
            (session_id, news_id)
        )
        existing = c.fetchone()

        if existing:
            if existing[0] == vote_type:
                c.execute(
                    "DELETE FROM user_votes WHERE session_id = ? AND news_id = ?",
                    (session_id, news_id)
                )
                col = "swords" if vote_type == "sword" else "shields"
                c.execute(f"UPDATE reactions SET {col} = {col} - 1 WHERE news_id = ?", (news_id,))
                conn.commit()
                conn.close()
                cache.delete_memoized(get_gaming_news)
                return jsonify({"status": "success", "action": "undo"})
            else:
                old_col = "swords" if existing[0] == "sword" else "shields"
                c.execute(f"UPDATE reactions SET {old_col} = {old_col} - 1 WHERE news_id = ?", (news_id,))
                c.execute(
                    "UPDATE user_votes SET vote_type = ? WHERE session_id = ? AND news_id = ?",
                    (vote_type, session_id, news_id)
                )
                new_col = "swords" if vote_type == "sword" else "shields"
                c.execute(f"UPDATE reactions SET {new_col} = {new_col} + 1 WHERE news_id = ?", (news_id,))
        else:
            c.execute(
                "INSERT INTO user_votes (session_id, news_id, vote_type) VALUES (?, ?, ?)",
                (session_id, news_id, vote_type)
            )
            col = "swords" if vote_type == "sword" else "shields"
            c.execute(
                f"INSERT OR REPLACE INTO reactions (news_id, {col}) VALUES (?, COALESCE((SELECT {col} FROM reactions WHERE news_id = ?), 0) + 1)",
                (news_id, news_id)
            )

        conn.commit()
        conn.close()
        cache.delete_memoized(get_gaming_news)
        return jsonify({"status": "success", "action": "vote"})

    except Exception as e:
        app.logger.error(f"Vote error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- التفاعلات ---

@app.route('/api/reactions/<news_id>')
@limiter.limit("60 per minute")
def get_reactions(news_id):
    try:
        news_id    = validate_input(news_id, 64)
        session_id = generate_session_id()

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT swords, shields FROM reactions WHERE news_id=?", (news_id,))
        res = c.fetchone()
        swords, shields = res if res else (0, 0)

        c.execute(
            "SELECT vote_type FROM user_votes WHERE session_id = ? AND news_id = ?",
            (session_id, news_id)
        )
        user_vote = c.fetchone()
        conn.close()

        return jsonify({
            "swords":    swords,
            "shields":   shields,
            "user_vote": user_vote[0] if user_vote else None
        })

    except Exception as e:
        app.logger.error(f"Reactions fetch error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- تحديث الكاش ---

@app.route('/admin/refresh-cache', methods=['POST'])
def refresh_cache():
    auth_key = request.headers.get('X-Admin-Key')
    if not auth_key or not hmac.compare_digest(auth_key, app.config.get('ADMIN_KEY', 'admin-key')):
        return jsonify({"error": "Unauthorized"}), 401
    cache.delete_memoized(get_gaming_news)
    return jsonify({"status": "success", "message": "Cache cleared"})

# --- معالج الأخطاء ---

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(429)
def rate_limit_exceeded(error):
    return jsonify({"error": "Too many requests. Please try again later."}), 429

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
