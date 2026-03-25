try:
    import whisper
    model = whisper.load_model("base")  # use "small" for better accuracy
except ImportError:
    whisper = None

import tempfile, os

def transcribe_audio(audio_file) -> tuple[str, str]:
    if not whisper:
        return "Audio transcription depends on C++ build tools and whisper. To test UI, this is skipped.", "en"
        
    ext = audio_file.filename.rsplit('.', 1)[-1] if audio_file.filename else 'webm'
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name
    
    try:
        result = model.transcribe(tmp_path)
        transcribed = result['text'].strip()
        detected_lang = result.get('language', 'en')
        return transcribed, detected_lang
    finally:
        os.unlink(tmp_path)
