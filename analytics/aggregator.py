from utils.firebase_client import db, increment_counter
from datetime import datetime, timezone, date

def log_query_event(user_uid: str, query_id: str, language: str, 
                    category: str, state: str):
    today = date.today().isoformat()  # "2026-03-23"
    month = today[:7]  # "2026-03"
    
    # Daily analytics
    daily_ref = db.document(f'analytics_daily/{today}')
    daily_ref.set({
        'total_queries': db.field_path_increment_hack,
        'date': today
    }, merge=True)
    
    # Use Firestore atomic increments
    daily_ref.update({
        'total_queries': db.SERVER_TIMESTAMP.__class__,
    })
    
    # Simpler approach with increment
    batch = db.batch()
    
    daily_doc = db.document(f'analytics_daily/{today}')
    batch.set(daily_doc, {
        'total_queries': 1,
        'date': today,
        f'by_language.{language}': 1,
        f'by_category.{category}': 1,
        f'by_state.{state}': 1
    }, merge=True)
    
    # Using FieldPath for increments
    from firebase_admin.firestore import SERVER_TIMESTAMP
    
    # Simpler: just use update with Increment
    from firebase_admin import firestore as fs
    
    db.document(f'analytics_daily/{today}').set({
        'date': today,
        'total_queries': fs.Increment(1),
        f'by_language': {language: fs.Increment(1)},
        f'by_category': {category: fs.Increment(1)},
        f'by_state': {state: fs.Increment(1)},
    }, merge=True)
    
    # Monthly analytics
    db.document(f'analytics_monthly/{month}').set({
        'month': month,
        'total_queries': fs.Increment(1),
        f'by_category': {category: fs.Increment(1)},
    }, merge=True)
    
    # User activity tracking
    db.document(f'users/{user_uid}').set({
        'query_count': fs.Increment(1),
        'last_active': SERVER_TIMESTAMP
    }, merge=True)
