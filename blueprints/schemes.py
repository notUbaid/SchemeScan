from flask import Blueprint, request, jsonify, g
from utils.auth_middleware import require_auth
from utils.firebase_client import (get_document, create_document, 
                                    update_document, query_collection, db)
from rag.retriever import retrieve_relevant_chunks
from rag.generator import generate_scheme_matches
from rag.conflict_detector import detect_conflicts
from utils.language_utils import translate_response
import uuid

schemes_bp = Blueprint('schemes', __name__)

@schemes_bp.route('/match', methods=['POST'])
@require_auth
def match_schemes():
    data = request.get_json()
    query_id = data.get('query_id', str(uuid.uuid4()))
    profile = data.get('profile', {})
    
    # Fast mathematical criteria check (Deterministic)
    def safe_int(val, default=0):
        try: return int(float(val)) if val else default
        except: return default

    user_age = safe_int(profile.get('age', 0))
    user_income = safe_int(profile.get('annual_income', 0))
    user_gender = profile.get('gender', 'All').lower()
    user_category = profile.get('category_caste', 'All').lower()
    user_state = profile.get('state', '').lower()

    schemes_ref = db.collection('schemes').stream()
    matches_with_conflicts = []

    for doc in schemes_ref:
        s = doc.to_dict()
        
        missed_criteria = []
        
        # Check State
        scheme_state = s.get('state', '').lower()
        if s.get('level') == 'state' and scheme_state and user_state and scheme_state != user_state:
            missed_criteria.append(f"State mismatch: Scheme is for {scheme_state.title()}, you are from {user_state.title()}.")
            
        # Check Age
        min_age = safe_int(s.get('age_min'), 0)
        max_age = safe_int(s.get('age_max'), 150)
        if user_age < min_age:
            missed_criteria.append(f"Age too low: You are {user_age}, scheme requires minimum {min_age} years.")
        elif user_age > max_age:
            missed_criteria.append(f"Age too high: You are {user_age}, scheme requires maximum {max_age} years.")
            
        # Check Income
        income_limit = safe_int(s.get('income_limit_annual'), 999999999)
        if user_income > income_limit:
            missed_criteria.append(f"Income too high: Your income (₹{user_income}) exceeds limit (₹{income_limit}).")
            
        # Check Gender
        s_gender = s.get('gender', 'All').lower()
        if s_gender != 'all' and user_gender != 'all' and s_gender != user_gender and user_gender != '':
            missed_criteria.append(f"Gender mismatch: Scheme is for {s_gender.title()} only.")

        # If it misses more than 1 criteria, skip it entirely (too far off)
        if len(missed_criteria) > 1:
            continue
            
        is_exact_match = len(missed_criteria) == 0
        
        reasoning = []
        if is_exact_match:
            reasoning.append(f"Exactly matched your profile: Age ({user_age}), Income (₹{user_income}), State ({user_state.title()}), Gender ({user_gender.title()}).")
        else:
            reasoning.append("Closest Match - You meet most criteria, but missed:")
            reasoning.extend([f"- {miss}" for miss in missed_criteria])

        matches_with_conflicts.append({
            'scheme_id': s.get('scheme_id'),
            'scheme_name': s.get('name'),
            'ministry': s.get('ministry', ''),
            'level': s.get('level', 'central'),
            'state': s.get('state', ''),
            'category': s.get('category', ''),
            'eligible': is_exact_match,
            'match_type': 'Exact Match' if is_exact_match else 'Partial Match',
            'reasoning': reasoning,
            'citations': [f"Evaluated against official rules for {s.get('name')}."],
            'checklist': s.get('documents_required', "Aadhaar Card\nBank Passbook\nIncome Certificate (if applicable)").split('\n'),
            'benefits': s.get('benefits', 'Financial assistance/Services'),
            'application_url': s.get('pdf_url', ''),
            'deadline': 'Check official portal'
        })
        
    # Sort matches: Exact matches first, then partial matches
    matches_with_conflicts.sort(key=lambda x: not x['eligible'])
    
    # Step 4: Save each match to Firestore
    saved_matches = []
    for match in matches_with_conflicts:
        match_id = str(uuid.uuid4())
        match_data = {
            'query_id': query_id,
            'user_uid': g.user_uid,
            'scheme_id': match['scheme_id'],
            'scheme_name': match['scheme_name'],
            'ministry': match.get('ministry', ''),
            'level': match.get('level', 'central'),
            'state': match.get('state', ''),
            'category': match.get('category', ''),
            'eligible': match['eligible'],
            'match_type': match.get('match_type', 'Unknown'),
            'reasoning': match.get('reasoning', []),
            'citations': match.get('citations', []),
            'checklist': match.get('checklist', []),
            'benefits': match.get('benefits', ''),
            'application_url': match.get('application_url', ''),
            'deadline': match.get('deadline', 'No fixed deadline')
        }
        create_document('scheme_matches', match_data, match_id)
        match['match_id'] = match_id
        saved_matches.append(match)
    
    # Create or update query document
    create_document('queries', {
        'user_uid': getattr(g, 'user_uid', 'anonymous'),
        'scheme_match_count': len(saved_matches),
        'status': 'matched'
    }, query_id)
    
    return jsonify({'matches': saved_matches, 'total': len(saved_matches)})

@schemes_bp.route('/matches/<query_id>', methods=['GET'])
@require_auth
def get_query_matches(query_id):
    matches = query_collection('scheme_matches', filters=[('query_id', '==', query_id)])
    return jsonify({'matches': matches})

@schemes_bp.route('/saved', methods=['GET'])
@require_auth
def get_saved_schemes():
    saved = query_collection(
        'saved_schemes',
        filters=[('user_uid', '==', g.user_uid)],
        order_by='saved_at'
    )
    return jsonify({'saved_schemes': saved})

@schemes_bp.route('/save', methods=['POST'])
@require_auth
def save_scheme():
    data = request.get_json()
    scheme_id = data.get('scheme_id')
    match_id = data.get('match_id')
    
    save_data = {
        'user_uid': g.user_uid,
        'scheme_id': scheme_id,
        'match_id': match_id,
        'notes': data.get('notes', '')
    }
    save_id = create_document('saved_schemes', save_data)
    return jsonify({'save_id': save_id, 'status': 'saved'})
