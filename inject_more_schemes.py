import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Initialize Firebase safely
if not firebase_admin._apps:
    cred = credentials.Certificate('./firebase_service_account.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Import Chroma collection AFTER ensuring environment variables or paths
from rag.ingestor import collection

new_schemes = [
    {
        "id": "scheme_ayushman",
        "name": "Ayushman Bharat PM-JAY",
        "ministry": "Ministry of Health",
        "category": "health",
        "level": "central",
        "state": "",
        "desc": "Health insurance cover of Rs. 5 lakhs per family per year for secondary and tertiary care hospitalization. Eligibility: socio economic poor households, landless laborers. Documents: Aadhaar card, Ration card.",
    },
    {
        "id": "scheme_pmay",
        "name": "Pradhan Mantri Awas Yojana (PMAY)",
        "ministry": "Ministry of Housing",
        "category": "housing",
        "level": "central",
        "state": "",
        "desc": "Affordable housing scheme for urban and rural poor. Benefit: Financial assistance for house construction. Eligibility: Must not own a pucca house, annual income limit under 3 lakhs for EWS. Documents required: Income certificate, Aadhaar.",
    },
    {
        "id": "scheme_mgnrega",
        "name": "Mahatma Gandhi National Rural Employment Guarantee Act",
        "ministry": "Ministry of Rural Development",
        "category": "employment",
        "level": "central",
        "state": "",
        "desc": "Guarantees 100 days of wage employment in a financial year to a rural household whose adult members volunteer to do unskilled manual work. Eligibility: Rural resident, 18+ years age.",
    },
    {
        "id": "scheme_cm_kisan_guj",
        "name": "Chief Minister Kisan Sahay Yojana",
        "ministry": "Department of Agriculture, Gujarat",
        "category": "agriculture",
        "level": "state",
        "state": "Gujarat",
        "desc": "Crop insurance cover with zero premium for Kharif season. Benefit: Financial aid for crop loss due to drought, excess rain, or unseasonal rain. Eligibility: Farmer in Gujarat. Documents: Aadhaar, 8-A Khata Number.",
    }
]

for s in new_schemes:
    # 1. Add to Firestore so UI can fetch metadata
    db.collection('schemes').document(s['id']).set({
        'scheme_id': s['id'],
        'name': s['name'],
        'ministry': s['ministry'],
        'category': s['category'],
        'level': s['level'],
        'state': s['state'],
        'chunk_count': 1,
        'pdf_url': f"https://schemes.gov.in/{s['id']}.pdf",
        'last_updated': firestore.SERVER_TIMESTAMP
    })
    
    # 2. Add to ChromaDB so RAG Search can find it semantically
    chunk_id = f"{s['id']}_chunk_1"
    collection.upsert(
        documents=[s['desc']],
        metadatas=[{
            'scheme_id': s['id'],
            'scheme_name': s['name'],
            'category': s['category'],
            'level': s['level'],
            'state': s['state']
        }],
        ids=[chunk_id]
    )

print("✅ Successfully injected 4 additional schemes into Firestore and ChromaDB!")
