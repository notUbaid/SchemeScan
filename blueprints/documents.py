from flask import Blueprint, request, jsonify, g
from utils.auth_middleware import require_auth
from utils.firebase_client import create_document, get_document, upload_file_to_storage
from ocr.aadhaar_parser import parse_aadhaar
from ocr.income_parser import parse_income_certificate
from ocr.caste_parser import parse_caste_certificate
import tempfile, os, uuid

documents_bp = Blueprint('documents', __name__)

DOC_PARSERS = {
    'aadhaar': parse_aadhaar,
    'income': parse_income_certificate,
    'caste': parse_caste_certificate
}

@documents_bp.route('/upload', methods=['POST'])
@require_auth
def upload_document():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    doc_type = request.form.get('doc_type', '').lower()
    
    if doc_type not in DOC_PARSERS:
        return jsonify({'error': f'Invalid doc_type. Use: {list(DOC_PARSERS.keys())}'}), 400
    
    allowed = {'pdf', 'jpg', 'jpeg', 'png'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        return jsonify({'error': 'File must be PDF, JPG, or PNG'}), 400
    
    doc_id = str(uuid.uuid4())
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name
    
    try:
        # Run OCR
        extracted_fields = DOC_PARSERS[doc_type](tmp_path)
        
        # Upload to Firebase Storage
        storage_path = f"documents/{g.user_uid}/{doc_type}/{doc_id}.{ext}"
        file_url = upload_file_to_storage(tmp_path, storage_path)
        
        # Save to Firestore
        doc_data = {
            'user_uid': g.user_uid,
            'doc_type': doc_type,
            'storage_path': storage_path,
            'file_url': file_url,
            'extracted_fields': extracted_fields,
            'verified': False,
            'ocr_confidence': extracted_fields.get('_confidence', 0)
        }
        create_document('user_documents', doc_data, doc_id)
        
        # Update user profile with extracted data
        from utils.firebase_client import update_document
        update_document('users', g.user_uid, {
            f'documents.{doc_type}': doc_id,
            f'profile.{doc_type}_verified': True
        })
        
        return jsonify({
            'doc_id': doc_id,
            'extracted_fields': extracted_fields,
            'file_url': file_url,
            'doc_type': doc_type
        })
    finally:
        os.unlink(tmp_path)

@documents_bp.route('/list', methods=['GET'])
@require_auth
def list_documents():
    from utils.firebase_client import query_collection
    docs = query_collection(
        'user_documents',
        filters=[('user_uid', '==', g.user_uid)],
        order_by='created_at'
    )
    return jsonify({'documents': docs})
