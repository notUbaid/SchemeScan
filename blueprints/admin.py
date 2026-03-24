from flask import Blueprint, request, jsonify, g
from utils.auth_middleware import require_admin
from utils.firebase_client import db, query_collection, get_document
from analytics.trend_engine import get_trend_data
import anthropic, json
from config import Config

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/verify', methods=['GET'])
def verify_admin():
    from utils.auth_middleware import require_auth
    from utils.firebase_client import get_document, verify_firebase_token
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({'is_admin': False}), 200
    try:
        token = auth_header.split(' ')[1]
        decoded = verify_firebase_token(token)
        uid = decoded['uid']
        email = decoded.get('email', '')
        admin_doc = get_document('admin_users', uid)
        is_admin = bool(admin_doc) or email in Config.ADMIN_EMAILS
        return jsonify({
            'is_admin': is_admin,
            'access_level': admin_doc.get('access_level', 'admin') if admin_doc else 'admin'
        })
    except:
        return jsonify({'is_admin': False}), 200

@admin_bp.route('/analytics/daily', methods=['GET'])
@require_admin
def daily_analytics():
    days = int(request.args.get('days', 30))
    from datetime import date, timedelta
    
    dates = []
    query_counts = []
    user_counts = []
    
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        doc = db.document(f'analytics_daily/{d}').get()
        data = doc.to_dict() if doc.exists else {}
        dates.append(d)
        query_counts.append(data.get('total_queries', 0))
        user_counts.append(data.get('unique_users', 0))
    
    return jsonify({'dates': dates, 'query_counts': query_counts, 
                    'user_counts': user_counts})

@admin_bp.route('/analytics/category', methods=['GET'])
@require_admin
def category_analytics():
    from datetime import date, timedelta
    
    categories = ['agriculture', 'education', 'health', 'housing', 
                  'employment', 'general']
    counts = {c: 0 for c in categories}
    
    for i in range(30):
        d = (date.today() - timedelta(days=i)).isoformat()
        doc = db.document(f'analytics_daily/{d}').get()
        if doc.exists:
            data = doc.to_dict()
            by_cat = data.get('by_category', {})
            for cat, count in by_cat.items():
                if cat in counts:
                    counts[cat] += count
    
    return jsonify({'categories': list(counts.keys()), 
                    'counts': list(counts.values())})

@admin_bp.route('/analytics/schemes', methods=['GET'])
@require_admin
def scheme_analytics():
    limit = int(request.args.get('limit', 10))
    
    matches = db.collection('scheme_matches')\
                .order_by('created_at', direction='DESCENDING')\
                .limit(200).stream()
    
    scheme_counts = {}
    for match in matches:
        data = match.to_dict()
        name = data.get('scheme_name', 'Unknown')
        if name not in scheme_counts:
            scheme_counts[name] = {'count': 0, 'has_conflicts': False}
        scheme_counts[name]['count'] += 1
        if data.get('has_conflict'):
            scheme_counts[name]['has_conflicts'] = True
    
    sorted_schemes = sorted(scheme_counts.items(), 
                             key=lambda x: x[1]['count'], reverse=True)[:limit]
    
    return jsonify({'schemes': [
        {'name': name, 'count': data['count'], 
         'has_conflicts': data['has_conflicts']}
        for name, data in sorted_schemes
    ]})

@admin_bp.route('/analytics/languages', methods=['GET'])
@require_admin
def language_analytics():
    from datetime import date, timedelta
    
    lang_counts = {}
    for i in range(30):
        d = (date.today() - timedelta(days=i)).isoformat()
        doc = db.document(f'analytics_daily/{d}').get()
        if doc.exists:
            data = doc.to_dict()
            for lang, count in data.get('by_language', {}).items():
                lang_counts[lang] = lang_counts.get(lang, 0) + count
    
    sorted_langs = sorted(lang_counts.items(), key=lambda x: x[1], reverse=True)
    return jsonify({'languages': [l[0] for l in sorted_langs],
                    'counts': [l[1] for l in sorted_langs]})

@admin_bp.route('/analytics/seasonal', methods=['GET'])
@require_admin
def seasonal_analytics():
    return jsonify(get_trend_data())

@admin_bp.route('/users', methods=['GET'])
@require_admin
def list_users():
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    
    users_ref = db.collection('users')\
                  .order_by('last_active', direction='DESCENDING')\
                  .limit(limit * page)
    
    all_users = [{'id': d.id, **d.to_dict()} for d in users_ref.stream()]
    page_users = all_users[(page-1)*limit : page*limit]
    
    # Remove sensitive fields for privacy
    safe_users = [{
        'uid': u['id'],
        'display_name': u.get('display_name', 'Anonymous'),
        'state': u.get('state', ''),
        'preferred_language': u.get('preferred_language', 'en'),
        'query_count': u.get('query_count', 0),
        'last_active': str(u.get('last_active', '')),
        'role': u.get('role', 'citizen')
    } for u in page_users]
    
    return jsonify({'users': safe_users, 'total': len(all_users)})

@admin_bp.route('/ai-insights', methods=['POST'])
@require_admin
def ai_insights():
    question = request.get_json().get('question', '')
    if not question:
        return jsonify({'error': 'Question required'}), 400
    
    # Gather analytics context
    from datetime import date, timedelta
    summary = {}
    for i in range(7):
        d = (date.today() - timedelta(days=i)).isoformat()
        doc = db.document(f'analytics_daily/{d}').get()
        if doc.exists:
            summary[d] = doc.to_dict()
    
    client_ai = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    
    response = client_ai.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": f"""You are an analytics assistant for SarkariSaathi, 
a government scheme navigator for Indian citizens.

Analytics data (last 7 days):
{json.dumps(summary, indent=2, default=str)}

Admin question: {question}

Provide a clear, actionable insight. Be specific with numbers from the data.
Format: plain text answer, 2-4 paragraphs maximum."""
        }]
    )
    
    return jsonify({'answer': response.content[0].text, 'chart_data': None})

@admin_bp.route('/schemes/ingest', methods=['POST'])
@require_admin
def ingest_scheme():
    if 'pdf' not in request.files:
        return jsonify({'error': 'PDF file required'}), 400
    
    pdf_file = request.files['pdf']
    scheme_name = request.form.get('scheme_name', pdf_file.filename)
    level = request.form.get('level', 'central')
    state = request.form.get('state', '')
    category = request.form.get('category', 'general')
    
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_file.save(tmp.name)
        tmp_path = tmp.name
    
    try:
        from rag.ingestor import ingest_pdf
        result = ingest_pdf(tmp_path, scheme_name, level, state, category)
        
        # Log admin action
        from utils.firebase_client import create_document
        create_document('admin_logs', {
            'admin_uid': g.user_uid,
            'action': 'scheme_ingested',
            'target_id': result['scheme_id'],
            'metadata': {'scheme_name': scheme_name, 'level': level, 'state': state}
        })
        
        return jsonify({
            'scheme_id': result['scheme_id'],
            'chunks_created': result['chunks'],
            'status': 'success'
        })
    finally:
        os.unlink(tmp_path)
