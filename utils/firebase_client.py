import firebase_admin
from firebase_admin import credentials, firestore, auth, storage
from config import Config
from datetime import datetime, timezone
import uuid

cred = credentials.Certificate(Config.FIREBASE_CREDENTIALS_PATH)
firebase_admin.initialize_app(cred, {
    'storageBucket': Config.FIREBASE_STORAGE_BUCKET
})

db = firestore.client()
bucket = storage.bucket()

# ── Firestore helpers ──

def create_document(collection: str, data: dict, doc_id: str = None) -> str:
    if not doc_id:
        doc_id = str(uuid.uuid4())
    data['created_at'] = datetime.now(timezone.utc)
    data['updated_at'] = datetime.now(timezone.utc)
    db.collection(collection).document(doc_id).set(data)
    return doc_id

def get_document(collection: str, doc_id: str) -> dict | None:
    doc = db.collection(collection).document(doc_id).get()
    if doc.exists:
        return {'id': doc.id, **doc.to_dict()}
    return None

def update_document(collection: str, doc_id: str, data: dict):
    data['updated_at'] = datetime.now(timezone.utc)
    db.collection(collection).document(doc_id).update(data)

def query_collection(collection: str, filters: list = None, 
                     limit: int = 20, order_by: str = None) -> list:
    ref = db.collection(collection)
    if filters:
        for field, op, value in filters:
            ref = ref.where(field, op, value)
    if order_by:
        ref = ref.order_by(order_by, direction=firestore.Query.DESCENDING)
    if limit:
        ref = ref.limit(limit)
    return [{'id': d.id, **d.to_dict()} for d in ref.stream()]

def increment_counter(doc_path: str, field: str, amount: int = 1):
    ref = db.document(doc_path)
    ref.set({field: firestore.Increment(amount)}, merge=True)

def upload_file_to_storage(local_path: str, storage_path: str) -> str:
    blob = bucket.blob(storage_path)
    blob.upload_from_filename(local_path)
    blob.make_public()
    return blob.public_url

def verify_firebase_token(id_token: str) -> dict:
    return auth.verify_id_token(id_token)
