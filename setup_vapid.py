"""
Lance ce script une seule fois pour générer les clés VAPID.
Les clés seront sauvegardées dans vapid_private.pem et affichées pour .env
"""
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64

private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
public_key  = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()
)

public_raw = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)

public_b64  = base64.urlsafe_b64encode(public_raw).decode().rstrip("=")
private_b64 = base64.urlsafe_b64encode(private_pem).decode()

# Sauvegarder la clé privée localement
with open("vapid_private.pem", "wb") as f:
    f.write(private_pem)

print("\n✅ vapid_private.pem créé\n")
print("=== Ajoute ces lignes dans ton .env (et dans Render > Environment) ===\n")
print(f"VAPID_PUBLIC_KEY={public_b64}")
print(f"VAPID_PRIVATE_KEY={private_b64}")
print("\n=== Copie cette valeur dans index.html (const VAPID_PUBLIC) ===\n")
print(f"const VAPID_PUBLIC = '{public_b64}';")
