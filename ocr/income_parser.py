import re
from ocr.aadhaar_parser import extract_text

def parse_income_certificate(file_path: str) -> dict:
    text = extract_text(file_path)
    
    result = {
        'annual_income': None,
        'annual_income_number': None,
        'applicant_name': None,
        'issuing_authority': None,
        'issue_date': None,
        'certificate_number': None,
        'financial_year': None,
        '_confidence': 0
    }
    
    # Income: handles Rs., ₹, lakh, thousand formats
    income_patterns = [
        r'(?:annual income|yearly income|वार्षिक आय|income)[:\s]+(?:Rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?(?:\s*(?:lakhs?|lakh|thousand))?',
        r'(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:/-)',
        r'([\d,]+)\s*(?:per annum|p\.a\.|annually)',
    ]
    
    for pattern in income_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            raw_num = match.group(1).replace(',', '')
            try:
                num = float(raw_num)
                # Normalize to rupees
                if 'lakh' in text[max(0, match.start()-20):match.end()+20].lower():
                    num = num * 100000
                result['annual_income'] = f"₹{int(num):,}"
                result['annual_income_number'] = int(num)
            except:
                pass
            break
    
    # Date: DD/MM/YYYY
    date_match = re.search(r'(?:date|दिनांक)[:\s]+(\d{2}[/\-]\d{2}[/\-]\d{4})', text, re.I)
    if date_match:
        result['issue_date'] = date_match.group(1)
    
    # Certificate number
    cert_match = re.search(r'(?:certificate no|cert\.?\s*no|प्रमाण पत्र)[:\s.#]+([A-Z0-9/\-]+)', text, re.I)
    if cert_match:
        result['certificate_number'] = cert_match.group(1).strip()
    
    confidence = sum(1 for v in [result['annual_income_number'], 
                                   result['issue_date']] if v) / 2
    result['_confidence'] = confidence
    
    return result
