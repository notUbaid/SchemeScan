import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    # Required for JWT auth: use a long random value in production (never commit it).
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
    JWT_EXPIRATION_DAYS = int(os.getenv("JWT_EXPIRATION_DAYS", "7"))
    FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
    FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_store")
    PDF_CORPUS_PATH = os.getenv("PDF_CORPUS_PATH", "./scheme_pdfs")
    ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "").split(",")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB upload limit
