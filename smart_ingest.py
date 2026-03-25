import csv
import firebase_admin
from firebase_admin import credentials, firestore
import os, re, json, uuid
import chromadb
from chromadb.utils import embedding_functions

# Initialize Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate('./firebase_service_account.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Initialize ChromaDB
sentence_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
chroma_client = chromadb.PersistentClient(path="./chroma_store")
collection = chroma_client.get_or_create_collection(
    name="government_schemes",
    embedding_function=sentence_ef,
    metadata={"hnsw:space": "cosine"}
)

def extract_age(text):
    if not text: return 0, 150
    match = re.search(r'(\d+)\s*[-to]+\s*(\d+)\s*years', text.lower())
    if match: return int(match.group(1)), int(match.group(2))
    match = re.search(r'age\s*(\d+)\s*[-to]+\s*(\d+)', text.lower())
    if match: return int(match.group(1)), int(match.group(2))
    return 0, 150

def extract_income(text):
    if not text: return 999999999
    match = re.search(r'income\s*[^0-9]*?([\d,]+)', text.lower())
    if match:
        val = match.group(1).replace(',', '')
        if val.isdigit():
            return int(val)
    return 999999999

CSV_PATH = 'archive/updated_data.csv'
if not os.path.exists(CSV_PATH):
    print(f"Error: {CSV_PATH} not found!")
    exit(1)

print("Starting smart ingestion with batching (Improved parsing)...")

with open(CSV_PATH, mode='r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    items = list(reader)
    total_items = len(items)
    print(f"Total schemes to process: {total_items}")
    
    count = 0
    batch = db.batch()
    batch_count = 0
    
    # ChromaDB batch data
    c_docs = []
    c_metas = []
    c_ids = []

    for row in items:
        name = row.get('scheme_name')
        if not name: continue
        
        scheme_id = row.get('slug') or str(uuid.uuid4())
        description = row.get('details', '')
        eligibility_text = row.get('eligibility', '')
        
        age_min, age_max = extract_age(eligibility_text)
        income_limit = extract_income(eligibility_text)
        
        level = row.get('level', 'Central').lower()
        state = ""
        if level == 'state':
            states = ['Andhra Pradesh', 'Karnataka', 'Madhya Pradesh', 'West Bengal', 'Rajasthan', 'Chhattisgarh', 'Maharashtra', 'Uttar Pradesh']
            for s in states:
                if s.lower() in eligibility_text.lower() or s.lower() in name.lower():
                    state = s
                    break
        
        category = row.get('schemeCategory', 'General')
        
        # Add to Firestore batch
        doc_ref = db.collection('schemes').document(scheme_id)
        batch.set(doc_ref, {
            'scheme_id': scheme_id,
            'name': name,
            'category': category,
            'level': level,
            'state': state,
            'ministry': 'N/A', 
            'benefits': row.get('benefits', ''),
            'description': description,
            'eligibility': eligibility_text,
            'age_min': age_min,
            'age_max': age_max,
            'income_limit_annual': income_limit,
            'gender': 'All', 
            'caste_category': 'All', 
            'chunk_count': 1,
            'pdf_url': '' 
        })
        
        # Add to ChromaDB batch
        c_docs.append(f"Scheme: {name}. Description: {description} Benefits: {row.get('benefits', '')} Eligibility: {eligibility_text}")
        c_metas.append({
            'scheme_id': scheme_id,
            'scheme_name': name,
            'category': category,
            'level': level,
            'state': state
        })
        c_ids.append(f"{scheme_id}_chunk_1")
        
        batch_count += 1
        count += 1
        
        # Commit batches every 100 items
        if batch_count >= 100:
            try:
                batch.commit()
                collection.upsert(ids=c_ids, documents=c_docs, metadatas=c_metas)
                print(f"Committed batch of 100 (Total: {count}/{total_items})")
            except Exception as e:
                print(f"Error committing batch: {e}")
            
            batch = db.batch()
            batch_count = 0
            c_docs, c_metas, c_ids = [], [], []

    # Final commit for leftover items
    if batch_count > 0:
        batch.commit()
        collection.upsert(ids=c_ids, documents=c_docs, metadatas=c_metas)
        print(f"Committed final batch (Total: {count})")

print(f"✅ Successfully ingested {count} schemes!")
