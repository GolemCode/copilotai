import redis

try:
    r = redis.Redis(host='localhost', port=6379, db=0)
    if r.ping():
        print("Succès : Connexion à Redis établie.")
except Exception as e:
    print(f"Erreur : {e}")