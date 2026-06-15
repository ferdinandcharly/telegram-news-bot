import os, requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT  = os.getenv("TELEGRAM_CHAT_ID")

print(f"Token : {TOKEN[:20]}...")
print(f"Chat ID : {CHAT}")

# 1. Vérifier que le bot existe
print("\n--- Test 1 : bot valide ? ---")
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=15)
print(r.json())

# 2. Envoyer un message test
print("\n--- Test 2 : envoi message ---")
r = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={"chat_id": CHAT, "text": "Test de connexion ✅"},
    timeout=15,
)
print(r.json())
