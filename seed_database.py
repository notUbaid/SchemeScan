import os
import json
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
cred = credentials.Certificate('./firebase_service_account.json')
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'projectId': 'scheme-iar'
    })

db = firestore.client()

def seed_database():
    print("Starting database seed...")

    # 1. USERS
    user_id = 'test_citizen_123'
    db.collection('users').document(user_id).set({
        'uid': user_id,
        'display_name': 'Ram Kumar',
        'preferred_language': 'hi',
        'state': 'Uttar Pradesh',
        'role': 'citizen',
        'created_at': firestore.SERVER_TIMESTAMP,
        'profile_json': json.dumps({"annual_income": 45000, "category": "OBC", "occupation": "Farmer"})
    })
    print("Created User")

    # 2. ADMIN_USERS
    admin_id = 'test_admin_999'
    db.collection('admin_users').document(admin_id).set({
        'uid': admin_id,
        'email': 'admin@sarkari.in',
        'name': 'System Administrator',
        'access_level': 'superadmin',
        'last_login': firestore.SERVER_TIMESTAMP
    })
    print("Created Admin")

    # 3. SCHEMES
    scheme_id = 'scheme_pm_kisan'
    db.collection('schemes').document(scheme_id).set({
        'scheme_id': scheme_id,
        'name': 'PM-KISAN Samman Nidhi',
        'ministry': 'Ministry of Agriculture',
        'level': 'central',
        'state': '',
        'category': 'agriculture',
        'pdf_url': 'https://schemes.gov.in/pmkisan.pdf',
        'last_updated': firestore.SERVER_TIMESTAMP,
        'chunk_count': 24
    })
    print("Created Scheme")

    # 4. QUERIES
    query_id = 'query_001'
    db.collection('queries').document(query_id).set({
        'query_id': query_id,
        'user_uid': user_id,
        'raw_input': 'Mujhe kheti ke liye scheme batao',
        'detected_language': 'hi',
        'translated_input': 'Tell me schemes for farming',
        'extracted_profile': '{"occupation":"farmer"}', # stored as json string to match schema
        'created_at': firestore.SERVER_TIMESTAMP,
        'state_filter': 'Uttar Pradesh',
        'category': 'agriculture'
    })
    print("Created Query")

    # 5. USER_DOCUMENTS
    doc_id = 'doc_001'
    db.collection('user_documents').document(doc_id).set({
        'doc_id': doc_id,
        'user_uid': user_id,
        'doc_type': 'aadhaar',
        'storage_path': f'documents/{user_id}/aadhaar/sample.pdf',
        'extracted_fields': json.dumps({'name': 'Ram Kumar', 'dob': '12/05/1980'}),
        'uploaded_at': firestore.SERVER_TIMESTAMP,
        'verified': True
    })
    print("Created User Document")

    # 6. SCHEME_MATCHES
    match_id = 'match_001'
    db.collection('scheme_matches').document(match_id).set({
        'match_id': match_id,
        'query_id': query_id,
        'scheme_id': scheme_id,
        'eligible': True,
        'reasoning_json': json.dumps([{'criterion': 'Farmer', 'met': True}]),
        'citations_json': json.dumps([{'text': 'Available to all landholding farmers'}]),
        'checklist_json': json.dumps([{'step': 1, 'description': 'Submit Aadhaar'}]),
        'has_conflict': False,
        'created_at': firestore.SERVER_TIMESTAMP
    })
    print("Created Scheme Match")

    # 7. SAVED_SCHEMES
    save_id = 'save_001'
    db.collection('saved_schemes').document(save_id).set({
        'save_id': save_id,
        'user_uid': user_id,
        'scheme_id': scheme_id,
        'saved_at': firestore.SERVER_TIMESTAMP,
        'notes': 'Need to find my land records first'
    })
    print("Created Saved Scheme")

    # 8. SCHEME_CONFLICTS
    conflict_id = 'conflict_001'
    db.collection('scheme_conflicts').document(conflict_id).set({
        'conflict_id': conflict_id,
        'scheme_id': scheme_id,
        'central_clause': 'Income limit is 2 Lakhs',
        'state_clause': 'Income limit is 1.5 Lakhs (State specific)',
        'explanation': 'State scheme has a stricter income barrier.',
        'central_pdf_ref': 'PM-KISAN Page 4',
        'state_pdf_ref': 'UP-KISAN Page 2'
    })
    print("Created Scheme Conflict")

    # 9. ADMIN_LOGS
    log_id = 'log_001'
    db.collection('admin_logs').document(log_id).set({
        'log_id': log_id,
        'admin_uid': admin_id,
        'action': 'SYSTEM_INIT',
        'target_id': 'all',
        'timestamp': firestore.SERVER_TIMESTAMP,
        'ip_address': '127.0.0.1'
    })
    print("Created Admin Log")

    print("\n✅ Database seeding complete! Run your frontend to verify!")

if __name__ == '__main__':
    seed_database()
