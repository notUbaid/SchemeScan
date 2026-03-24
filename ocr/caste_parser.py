import re
from ocr.aadhaar_parser import extract_text

def parse_caste_certificate(file_path: str) -> dict:
    text = extract_text(file_path)
    
    result = {
        'category': None,
        'caste_name': None,
        'sub_category': None,
        'applicant_name': None,
        'certificate_number': None,
        'issuing_authority': None,
        'issue_date': None,
        'state': None,
        '_confidence': 0
    }
    
    # Category detection
    if re.search(r'\bSC\b|scheduled caste|अनुसूचित जाति', text, re.I):
        result['category'] = 'SC'
    elif re.search(r'\bST\b|scheduled tribe|अनुसूचित जनजाति', text, re.I):
        result['category'] = 'ST'
    elif re.search(r'\bOBC\b|other backward|अन्य पिछड़ा', text, re.I):
        result['category'] = 'OBC'
    elif re.search(r'\bEWS\b|economically weaker', text, re.I):
        result['category'] = 'EWS'
    
    cert_match = re.search(r'(?:certificate no|cert\.?\s*no)[:\s.#]+([A-Z0-9/\-]+)', text, re.I)
    if cert_match:
        result['certificate_number'] = cert_match.group(1).strip()
    
    date_match = re.search(r'(\d{2}[/\-]\d{2}[/\-]\d{4})', text)
    if date_match:
        result['issue_date'] = date_match.group(1)
    
    result['_confidence'] = 1.0 if result['category'] else 0.3
    
    return result
