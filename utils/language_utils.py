from langdetect import detect
import anthropic
from config import Config

LANGUAGE_CODES = {
    'hi': 'Hindi', 'bn': 'Bengali', 'te': 'Telugu', 'mr': 'Marathi',
    'ta': 'Tamil', 'gu': 'Gujarati', 'kn': 'Kannada', 'ml': 'Malayalam',
    'pa': 'Punjabi', 'or': 'Odia', 'en': 'English'
}

client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

def detect_language(text: str) -> str:
    try:
        code = detect(text)
        return code if code in LANGUAGE_CODES else 'en'
    except:
        return 'en'

def translate_to_english(text: str, source_lang: str) -> str:
    if source_lang == 'en':
        return text
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"Translate this {LANGUAGE_CODES.get(source_lang, source_lang)} "
                      f"text to English. Return ONLY the translation, nothing else:\n\n{text}"
        }]
    )
    return response.content[0].text.strip()

def translate_response(matches: list, target_lang: str) -> list:
    if target_lang == 'en':
        return matches
    
    lang_name = LANGUAGE_CODES.get(target_lang, 'Hindi')
    
    for match in matches:
        if match.get('benefits'):
            match['benefits_translated'] = _translate_field(
                match['benefits'], lang_name
            )
        if match.get('reasoning'):
            for r in match['reasoning']:
                if r.get('criterion'):
                    r['criterion_translated'] = _translate_field(
                        r['criterion'], lang_name
                    )
    
    return matches

def _translate_field(text: str, lang_name: str) -> str:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"Translate to {lang_name}. Return ONLY translation:\n{text}"
            }]
        )
        return response.content[0].text.strip()
    except:
        return text
