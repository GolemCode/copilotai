import os
from groq import AsyncGroq
from tenacity import retry, stop_after_attempt, wait_exponential
from json_repair import repair_json
import json
from fastapi import HTTPException
from app.api.schemas import PortfolioSimulationRequest

# Initialisation asynchrone du client Groq
# Assurez-vous d'avoir GROQ_API_KEY dans votre fichier .env
client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

# Le prompt système force le LLM à se comporter comme un simple extracteur
SYSTEM_PROMPT = """
Tu es un routeur sémantique financier. Ton unique rôle est d'extraire les intentions d'un utilisateur et de les formater STRICTEMENT en JSON.
Tu ne fais aucun calcul. Tu ne donnes aucun conseil.

Actifs disponibles en base : AAPL (Apple), MSFT (Microsoft), GOOGL (Google), AMZN (Amazon), TSLA (Tesla).

Règles de pondération : 
- Si l'utilisateur exprime une pondération (ex: "moitié/moitié"), respecte-la.
- Sinon, répartis les poids de manière équitable pour que la somme fasse 1.0.

Règles d'investissement :
- Défaut : 10000.
- Horizon par défaut : 252 (jours de trading).

Format de sortie exigé (JSON uniquement) :
{
  "tickers": ["AAPL", "MSFT"],
  "weights": [0.5, 0.5],
  "initial_investment": 10000,
  "time_horizon_days": 252
}
"""

# Le décorateur Tenacity protège votre backend des pannes de l'API Groq (Rate Limits, 502, etc.)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def parse_user_intent(user_prompt: str) -> dict:
    try:
        response = await client.chat.completions.create(
            model="llama3-8b-8192", # Modèle extrêmement rapide, parfait pour du routage
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0 # Température à 0 pour un maximum de déterminisme
        )
        
        raw_content = response.choices[0].message.content
        
        # json_repair récupère les JSON mal formés (ex: LLM qui oublie une virgule ou ajoute du texte avant)
        repaired_json_str = repair_json(raw_content)
        parsed_data = json.loads(repaired_json_str)
        
        # Validation "Fail-Fast" : On vérifie que la sortie possède les clés minimales
        if "tickers" not in parsed_data or "weights" not in parsed_data:
            raise ValueError("Le JSON généré manque de champs obligatoires.")
            
        return parsed_data
        
    except Exception as e:
        raise Exception(f"Échec de l'extraction sémantique après retentatives : {str(e)}")