import csv
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate('./firebase_service_account.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()
from rag.ingestor import collection

with open('schemes_data.csv', mode='r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        scheme_id = row['scheme_id']
        name = row['name']
        
        # Save to Firebase
        def safe_int(val, default=0):
            try: return int(float(val)) if val else default
            except: return default

        db.collection('schemes').document(scheme_id).set({
            'scheme_id': scheme_id,
            'name': name,
            'category': row['category'],
            'level': row['level'],
            'state': row['state'],
            'ministry': row['ministry'],
            'benefits': row.get('benefits', ''),
            'age_min': safe_int(row.get('age_min'), 0),
            'age_max': safe_int(row.get('age_max'), 150),
            'income_limit_annual': safe_int(row.get('income_limit_annual'), 999999999),
            'gender': row.get('gender', 'All'),
            'caste_category': row.get('caste_category', 'All'),
            'chunk_count': 1,
            'pdf_url': row['official_pdf_url']
        })
        
        # Save to AI Vector Database
        semantic_text = f"Scheme: {name}. Description: {row['description']} Benefits: {row['benefits']} Eligibility: Age {row['age_min']}-{row['age_max']}, Gender {row['gender']}, Category {row['caste_category']}, Occupation {row['occupation']}."
        collection.upsert(
            documents=[semantic_text],
            metadatas=[{
                'scheme_id': scheme_id,
                'scheme_name': name,
                'category': row['category'],
                'level': row['level'],
                'state': row['state']
            }],
            ids=[f"{scheme_id}_chunk_1"]
        )
        count += 1

print(f"✅ Successfully piped {count} new schemes into your Database!")
