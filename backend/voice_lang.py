"""
voice_lang.py — SchemeScan Voice & Multilingual Support
---------------------------------------------------------
• /voice/stt  — Speech-to-text (browser Web Speech API preferred;
                Bhashini ASR as backend fallback)
• /voice/tts  — Text-to-speech (returns SSML/plain text for browser;
                Bhashini TTS as backend fallback)
• /translate  — Translate text via Bhashini (online) or hardcoded map (offline)
• /nlp/intent — Parse plain-language citizen descriptions into profile dicts
"""

import os, re, json
from typing import Optional, Dict, Any
import httpx

# ── Language codes ────────────────────────────────────────────────────────────
LANG_CODES = {
    "en": "en",
    "hi": "hi",
    "gu": "gu",
    "mr": "mr",
    "bn": "bn",
    "ta": "ta",
    "te": "te",
    "kn": "kn",
    "ml": "ml",
    "pa": "pa",
    "ur": "ur",
}

LANG_NAMES = {
    "en": "English",
    "hi": "हिन्दी",
    "gu": "ગુજરાતી",
    "mr": "मराठी",
    "bn": "বাংলা",
    "ta": "தமிழ்",
    "te": "తెలుగు",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
    "pa": "ਪੰਜਾਬੀ",
    "ur": "اردو",
}

# ══════════════════════════════════════════════════════════════════════════════
# HARDCODED TRANSLATIONS (offline fallback for common UI phrases)
# ══════════════════════════════════════════════════════════════════════════════

UI_STRINGS: Dict[str, Dict[str, str]] = {
    "welcome": {
        "en": "Welcome! Tell me about yourself and I will find government schemes for you.",
        "hi": "नमस्ते! अपने बारे में बताइए और मैं आपके लिए सरकारी योजनाएं खोजूंगा।",
        "gu": "નમસ્તે! તમારા વિશે જણાવો અને હું તમારા માટે સરકારી યોજનાઓ શોધીશ।",
        "mr": "नमस्कार! स्वतःबद्दल सांगा आणि मी तुमच्यासाठी सरकारी योजना शोधतो.",
        "ta": "வணக்கம்! உங்களைப் பற்றி சொல்லுங்கள், நான் உங்களுக்கான அரசு திட்டங்களை கண்டுபிடிப்பேன்.",
        "te": "నమస్కారం! మీ గురించి చెప్పండి, మీకు ప్రభుత్వ పథకాలు కనుగొంటాను.",
        "bn": "স্বাগতম! আপনার সম্পর্কে বলুন এবং আমি আপনার জন্য সরকারি প্রকল্প খুঁজে দেব।",
    },
    "speak_now": {
        "en": "Please speak now…",
        "hi": "कृपया अभी बोलें…",
        "gu": "કૃપા કરીને હવે બોલો…",
        "mr": "कृपया आता बोला…",
        "ta": "இப்போது பேசுங்கள்…",
        "te": "ఇప్పుడు మాట్లాడండి…",
        "bn": "এখন কথা বলুন…",
    },
    "found_schemes": {
        "en": "I found {count} schemes for you.",
        "hi": "मुझे आपके लिए {count} योजनाएं मिलीं।",
        "gu": "મને તમારા માટે {count} યોજનાઓ મળી.",
        "mr": "मला तुमच्यासाठी {count} योजना सापडल्या.",
        "ta": "உங்களுக்கு {count} திட்டங்கள் கண்டுபிடிக்கப்பட்டன.",
        "te": "మీకు {count} పథకాలు కనుగొన్నాను.",
        "bn": "আপনার জন্য {count}টি প্রকল্প খুঁজে পাওয়া গেছে।",
    },
    "no_schemes": {
        "en": "No schemes found matching your profile. Try adjusting your details.",
        "hi": "आपकी प्रोफ़ाइल से मेल खाती कोई योजना नहीं मिली। अपनी जानकारी बदलकर देखें।",
        "gu": "તમારી પ્રોફ઼ાઇલ સાથે મળતી કોઈ યોજના મળી નહીં. તમારી માહિતી બદલો.",
        "mr": "तुमच्या प्रोफाइलशी जुळणारी कोणतीही योजना सापडली नाही.",
        "ta": "உங்கள் விவரங்களுக்கு பொருந்தும் திட்டங்கள் எதுவும் கண்டுபிடிக்கப்படவில்லை.",
        "te": "మీ వివరాలకు సరిపోయే పథకాలు లేవు.",
        "bn": "আপনার প্রোফাইলের সাথে মিলে যায় এমন কোনো প্রকল্প পাওয়া যায়নি।",
    },
    "contradiction_alert": {
        "en": "⚠️ Warning: Conflicting information found in official documents. Please verify at your local government office.",
        "hi": "⚠️ चेतावनी: सरकारी दस्तावेजों में परस्पर विरोधी जानकारी पाई गई। कृपया अपने स्थानीय सरकारी कार्यालय में सत्यापित करें।",
        "gu": "⚠️ ચેતવણી: સત્તાવાર દસ્તાવેજોમાં વિરોધાભાસી માહિતી મળી. કૃપા કરીને તમારા સ્થાનિક સરકારી કચેરીમાં ચકાસો.",
        "mr": "⚠️ इशारा: अधिकृत दस्तावेजांमध्ये परस्परविरोधी माहिती आढळली. स्थानिक सरकारी कार्यालयात तपासा.",
        "ta": "⚠️ எச்சரிக்கை: அரசு ஆவணங்களில் முரண்பட்ட தகவல்கள் உள்ளன. உங்கள் உள்ளூர் அரசு அலுவலகத்தில் சரிபார்க்கவும்.",
        "te": "⚠️ హెచ్చరిక: అధికారిక పత్రాలలో విరుద్ధ సమాచారం ఉంది. దయచేసి స్థానిక ప్రభుత్వ కార్యాలయంలో ధృవీకరించండి.",
        "bn": "⚠️ সতর্কতা: সরকারি নথিতে পরস্পরবিরোধী তথ্য পাওয়া গেছে। স্থানীয় সরকারি অফিসে যাচাই করুন।",
    },
    "upload_doc": {
        "en": "Upload your Aadhaar, PAN, or income certificate to auto-fill your details.",
        "hi": "अपनी जानकारी स्वचालित भरने के लिए आधार, पैन या आय प्रमाण पत्र अपलोड करें।",
        "gu": "તમારી વિગતો આપોઆપ ભરવા માટે આધાર, PAN અથવા આવક પ્રમાણ-પત્ર અપલોડ કરો.",
        "mr": "तुमचे तपशील आपोआप भरण्यासाठी आधार, पॅन किंवा उत्पन्न प्रमाणपत्र अपलोड करा.",
        "ta": "உங்கள் விவரங்களை தானாகப் நிரப்ப ஆதார், பான் அல்லது வருமான சான்றிதழ் பதிவேற்றவும்.",
        "te": "మీ వివరాలను స్వయంగా నింపడానికి ఆధార్, పాన్ లేదా ఆదాయ ధృవీకరణ పత్రాన్ని అప్‌లోడ్ చేయండి.",
        "bn": "আপনার বিবরণ স্বয়ংক্রিয়ভাবে পূরণ করতে আধার, প্যান বা আয় সনদ আপলোড করুন।",
    },
}


def get_ui_string(key: str, lang: str = "en", **kwargs) -> str:
    """Get a translated UI string. Falls back to English."""
    strings = UI_STRINGS.get(key, {})
    text = strings.get(lang) or strings.get("en") or key
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text


