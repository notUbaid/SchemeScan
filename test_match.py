import firebase_admin
from firebase_admin import credentials, firestore
from blueprints.schemes import db
from utils.firebase_client import query_collection

def safe_int(val, default=0):
    try: return int(float(val)) if val else default
    except: return default

def test_matching():
    # User Profile
    user_age = 19
    user_income = 200000
    user_gender = "male"
    user_state = "gujarat"
    user_category = "general"

    print(f"Testing for: Age={user_age}, Income={user_income}, Gender={user_gender}, State={user_state}")

    schemes_ref = db.collection('schemes').stream()
    matches = []

    for doc in schemes_ref:
        s = doc.to_dict()
        print(f"\nEvaluating: {s.get('name')}")
        
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

        print(f"Missed: {missed_criteria}")

        if len(missed_criteria) <= 1:
            matches.append(s.get('name'))

    print(f"\nTotal matches found (Exact or Partial): {len(matches)}")
    print(matches)

if __name__ == "__main__":
    test_matching()
