import requests
import json
import logging

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "llama3.2"

def get_ollama_response(messages, model=DEFAULT_MODEL):
    """
    Sends a chat conversation to the local Ollama instance and returns the response.
    messages: list of dicts with 'role' and 'content' keys.
    """
    try:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        if 'message' in data and 'content' in data['message']:
            return data['message']['content']
        else:
            logging.error(f"Unexpected response from Ollama: {data}")
            return "Sorry, I am having trouble understanding right now."
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Error communicating with Ollama: {e}")
        return "Sorry, I couldn't reach the AI engine. Please ensure Ollama is running locally."
