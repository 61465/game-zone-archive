# ============================================================
# أضف هذا الكود لملف main.py الخاص بك
# ============================================================

@app.route('/api/news')
@limiter.limit("30 per minute")
@cache.memoize(timeout=300)  # كاش 5 دقائق
def api_news():
    """API endpoint يُعيد الأخبار كـ JSON للـ HTML frontend"""
    category = request.args.get('cat', None)
    news = get_gaming_news(category)
    
    # تحويل للصيغة التي يتوقعها gamezone_v2.html
    formatted = []
    for item in news[:50]:
        formatted.append({
            'id':          item['id'],
            'tag':         item['type'],
            'category':    item['type'],
            'date':        item.get('published', '')[:10],
            'emoji':       '🎮',
            'image':       item.get('image', ''),
            'link':        item['link'],
            'sourceColor': '#d4af37',
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
    response.headers['Access-Control-Allow-Origin'] = '*'  # السماح للـ HTML بالوصول
    return response


# ثم في gamezone_v2.html غيّر السطر:
#   const PROXY = "https://api.allorigins.win/get?url=";
# إلى:
#   const NEWS_API = "http://localhost:5000/api/news";
#
# واستبدل fetchLatestNews() بهذا:
