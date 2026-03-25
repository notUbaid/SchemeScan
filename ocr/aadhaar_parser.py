import pytesseract
from PIL import Image
import pdfplumber
import re, os

def parse_aadhaar(file_path: str) -> dict:
    text = extract_text(file_path)
    
    result = {
        'aadhaar_number': None,
        'name': None,
        'dob': None,
        'gender': None,
        'address': None,
        'pincode': None,
        'state_from_address': None,
        '_raw_text': text[:500],
        '_confidence': 0
    }
    
    # Aadhaar number: 12 digits, possibly spaced as XXXX XXXX XXXX
    aadhaar_match = re.search(r'\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b', text)
    if aadhaar_match:
        result['aadhaar_number'] = re.sub(r'[\s-]', '', aadhaar_match.group(1))
    
    # DOB: DD/MM/YYYY or DD-MM-YYYY or YYYY
    dob_match = re.search(r'(?:DOB|Date of Birth|जन्म)[:\s]+(\d{2}[/\-]\d{2}[/\-]\d{4})', text, re.I)
    if dob_match:
        result['dob'] = dob_match.group(1)
        dob_year = int(dob_match.group(1).split('/')[-1].split('-')[-1])
        import datetime
        result['age'] = datetime.date.today().year - dob_year
    
    # Gender
    if re.search(r'\bMALE\b|\bPURUSH\b|\bपुरुष\b', text, re.I):
        result['gender'] = 'Male'
    elif re.search(r'\bFEMALE\b|\bSTRI\b|\bमहिला\b', text, re.I):
        result['gender'] = 'Female'
    
    # Name: line after "Name" or before DOB
    name_match = re.search(r'(?:Name|नाम)[:\s]+([A-Z][A-Z\s]{2,40})', text)
    if name_match:
        result['name'] = name_match.group(1).strip()
    
    # Pincode
    pin_match = re.search(r'\b([1-9][0-9]{5})\b', text)
    if pin_match:
        result['pincode'] = pin_match.group(1)
    
    # State from address (simple heuristic)
    indian_states = ['Gujarat', 'Maharashtra', 'Tamil Nadu', 'Karnataka', 
                     'Rajasthan', 'Uttar Pradesh', 'Bihar', 'West Bengal',
                     'Madhya Pradesh', 'Andhra Pradesh', 'Telangana', 'Kerala',
                     'Punjab', 'Haryana', 'Odisha', 'Jharkhand', 'Assam']
    for state in indian_states:
        if state.lower() in text.lower():
            result['state_from_address'] = state
            break
    
    confidence = sum(1 for v in [result['aadhaar_number'], result['name'], 
                                   result['dob'], result['gender']] if v) / 4
    result['_confidence'] = confidence
    
    return result

def extract_text(file_path: str) -> str:
    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        with pdfplumber.open(file_path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    else:
        img = Image.open(file_path)
        return pytesseract.image_to_string(img, lang='eng+hin')
