import os
import re
import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from groq import AsyncGroq

from app.core.settings import PSYCOPG_CONNINFO
from app.services.rag_ingestion import find_explicit_tickers, get_documents_for_tickers, search_similar_documents
from app.services.rag_validation import find_unverified_numbers

logger = logging.getLogger(__name__)

# Initialisation du client Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Modèles de PRODUCTION GroqCloud (liste vérifiée sur console.groq.com/docs/models) :
# cascade de secours pour les réponses RAG, et modèle non "raisonneur" pour les résumés courts.
RAG_MODEL_CASCADE = [
    ("openai/gpt-oss-120b", 1500),
    ("openai/gpt-oss-20b", 1500),
    ("llama-3.3-70b-versatile", 1200),
]
DESCRIPTION_MODEL = "llama-3.3-70b-versatile"

def extract_doc_structured_info(content: str) -> Dict[str, Any]:
    """Extrait proprement les sous-jacents, l'AUM et la stratégie résumée du document."""
    info: Dict[str, Any] = {
        "holdings": [],
        "aum": None,
        "strategy": "",
        "paris_aligned": False
    }
    
    lines = content.split("\n")
    in_holdings = False
    in_description = False
    strategy_lines = []
    
    for line in lines:
        stripped = line.strip()
        if "Actifs sous gestion (AUM) :" in stripped:
            info["aum"] = stripped.split(":", 1)[1].strip()
        elif any(k in stripped for k in ["Principales positions", "Top Holdings", "Top 10 Holdings", "Mode Look-Through"]):
            in_holdings = True
        elif in_holdings:
            if stripped.startswith("- "):
                info["holdings"].append(stripped[2:].strip())
            elif stripped:
                # Toute autre ligne non vide clôt la liste des positions
                in_holdings = False
        elif "Description détaillée" in stripped:
            in_description = True
            continue
        elif stripped.endswith(":") or stripped.startswith("- "):
            # En-têtes et lignes des répartitions pays / secteurs : pas de la stratégie
            in_description = False
        elif in_description and len(strategy_lines) < 2 and stripped:
            strategy_lines.append(stripped)
            
    info["strategy"] = " ".join(strategy_lines)[:250]
    # Indice "Paris-Aligned Benchmark" (règlement UE 2020/1818) : déduit du nom officiel du fonds
    name_line = next((line for line in lines if line.strip().startswith("Nom du fonds")), "")
    info["paris_aligned"] = "paris aligned" in name_line.lower().replace("-", " ")
    return info

def fetch_source_for_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """Source à afficher pour un ticker cité : sa fiche indexée, sinon sa ligne du référentiel d'actifs."""
    clean = ticker.strip().upper()
    clean_base = clean.split(".")[0]
    if len(clean_base) < 2:
        return None

    docs = get_documents_for_tickers([clean])
    if docs:
        doc = docs[0]
        content = doc["content"]
        parsed_info = extract_doc_structured_info(content)
        snippet = content[:240] + "..." if len(content) > 240 else content
        return {
            "ticker": doc["ticker"],
            "name": doc["name"],
            "category": doc["category"],
            # Source récupérée par ticker exact, pas par recherche sémantique : pas de score
            "similarity_score_pct": None,
            "match_type": "direct",
            "snippet": snippet,
            "strategy": parsed_info.get("strategy") or snippet,
            "aum": parsed_info.get("aum"),
            "holdings": parsed_info.get("holdings", []),
            "paris_aligned": parsed_info.get("paris_aligned", False),
            "source_url": doc["source_url"],
            "pdf_url": doc.get("pdf_url") or f"https://www.justetf.com/fr/search.html?search=ETFS&query={clean_base}",
            "provider": doc["provider"],
        }

    try:
        import psycopg
        with psycopg.connect(PSYCOPG_CONNINFO) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ticker, name, asset_class, sector
                    FROM assets
                    WHERE UPPER(ticker) = %s OR UPPER(ticker) = %s OR UPPER(ticker) LIKE %s
                    LIMIT 1;
                """, (clean, clean_base, f"{clean_base}.%"))
                asset_row = cur.fetchone()
    except Exception as e:
        logger.warning(f"Erreur lors de la récupération directe de la source pour {ticker} : {e}")
        return None
    if not asset_row:
        return None
    t, n, ac, sec = asset_row
    category = ac or sec or "Actif Coté"
    desc_clean = f"Actif financier {n or t} ({category}) coté sous le symbole {t}."
    return {
        "ticker": t,
        "name": n or t,
        "category": category,
        "similarity_score_pct": None,
        "match_type": "direct",
        "snippet": desc_clean,
        "strategy": desc_clean,
        "aum": None,
        "holdings": [],
        "paris_aligned": False,
        "source_url": f"https://finance.yahoo.com/quote/{t}",
        "pdf_url": f"https://www.justetf.com/fr/search.html?search=ETFS&query={clean_base}",
        "provider": "Bourse & Marché Financier",
    }

OFF_TOPIC_TERMS = [
    "crepe", "crêpe", "recette", "cuisine", "gâteau", "gateau", "cuisinez", "cuisiner",
    "météo", "meteo", "blague", "joke", "poème", "poeme", "horoscope", "football",
    "fifa", "jeux vidéo", "chanson", "musique", "film", "cinéma", "restaurant"
]
FINANCIAL_TERMS = [
    "bourse", "action", "etf", "fnb", "obligation", "portefeuille", "invest",
    "dividende", "sharpe", "volatilité", "rendement", "pru", "crypto", "or",
    "gold", "cac", "nasdaq", "s&p", "amundi", "ishares", "blackrock", "pe500",
    "cw8", "spy", "qqq", "agg", "gld", "aum", "finance", "marché", "taux",
    "frais", "arbitrage", "valorisation", "drawdown", "pabz", "xdpe", "allocation"
]

def is_strictly_off_topic(query: str) -> bool:
    """Détecte rapidement les requêtes purement extra-financières pour éviter les hallucinations hors-sujet."""
    q = query.lower()
    has_off = any(t in q for t in OFF_TOPIC_TERMS)
    has_fin = any(t in q for t in FINANCIAL_TERMS)
    return has_off and not has_fin


def get_dynamic_fallback_questions(
    user_query: str,
    portfolio_context: Optional[Dict[str, Any]] = None,
    existing_questions: Optional[List[str]] = None
) -> List[str]:
    """Génère des questions de suivi pertinentes et contextualisées au portefeuille et à la requête."""
    if existing_questions is None:
        existing_questions = []

    tickers = []
    if portfolio_context and isinstance(portfolio_context, dict):
        assets = portfolio_context.get("assets", [])
        tickers = [a.get("ticker", "").upper() for a in assets if isinstance(a, dict) and a.get("ticker")]

    q_lower = user_query.lower()
    candidates: List[str] = []

    if any(k in tickers for k in ["CW8.PA", "WPEA.PA", "SPY", "QQQ", "PE500.PA", "ESE.PA"]):
        candidates.append("Quelle est la concentration exacte de mon portefeuille sur les 7 géants de la Tech ?")
        candidates.append("Quel actif décorrélé intégrer pour réduire le chevauchement sans impacter le rendement ?")
        candidates.append("Quels sont les frais courants de WPEA.PA et de CW8.PA, et lequel est le moins cher ?")

    if any(k in tickers for k in ["ETZ.PA", "MEUD.PA", "CAC.PA", "MC.PA", "AIR.PA"]):
        candidates.append("Quelle part d'actions européennes permet d'équilibrer le risque de change dollar/euro ?")
        candidates.append("Mon portefeuille est-il suffisamment exposé aux secteurs 'value' européens face à la Tech US ?")

    if any(k in tickers for k in ["XEON.DE", "C3M.PA", "CASH-EUR"]):
        candidates.append("Quelle part de liquidités allouer sur XEON.DE selon mon horizon de placement ?")
        candidates.append("À quels signaux macroéconomiques devrais-je arbitrer ma trésorerie vers les actions ?")

    if any(k in tickers for k in ["GLD"]):
        candidates.append("Quel pourcentage d'or GLD permet d'amortir un choc boursier sans pénaliser la performance ?")

    if any(k in q_lower for k in ["frais", "ter", "cout", "coût"]):
        candidates.append("Comment réduire le total des frais de gestion annuels de mon portefeuille ?")

    if any(k in q_lower for k in ["sharpe", "volatilité", "volatilite", "drawdown", "risque", "faiblesse"]):
        candidates.append("Quels arbitrages précis permettraient de maximiser le ratio de Sharpe de mon portefeuille ?")
        candidates.append("Existe-t-il une corrélation excessive entre mes différentes lignes d'actions ?")

    universal = [
        "Quelle allocation cible recommandes-tu entre actions, obligations et actifs décorrélés ?",
        "Comment répartir mes prochains versements réguliers (DCA) pour optimiser mon prix de revient ?",
        "Quel serait le comportement historique de ce portefeuille lors d'un krach de marché ?",
        "Quels sont les avantages fiscaux d'arbitrer au sein du PEA face à un compte-titres ordinaire ?"
    ]
    candidates.extend(universal)

    results = list(existing_questions)
    for c in candidates:
        if len(results) >= 2:
            break
        if c not in results:
            results.append(c)

    return results[:2]


def extract_questions_and_clean_answer(
    clean_answer: str,
    user_query: str = "",
    portfolio_context: Optional[Dict[str, Any]] = None
) -> Tuple[str, List[str]]:
    """
    Extrait les questions suggérées situées après la mention de non-conseil 
    (ou dans un bloc de questions explicite) et nettoie le corps du texte 
    afin que les questions n'apparaissent que dans les boutons interactifs dédiés.
    """
    suggested_questions: List[str] = []
    clean_body = clean_answer

    # 1. Détection de la mention de non-conseil obligatoire
    disclaimer_pattern = r"(ceci\s+ne\s+constitue\s+pas\s+un\s+conseil[^\n.]*(?:\.|\n|$))"
    match_disc = re.search(disclaimer_pattern, clean_answer, flags=re.IGNORECASE)

    # 2. Détection de balises explicites de questions de suivi
    split_pattern = r"(?:SUGGESTED_QUESTIONS\s*:|\*{0,2}Questions?\s+de\s+suivi\s+suggérée?s?\*{0,2}\s*:?|\*{0,2}Questions?\s+suggérée?s?\*{0,2}\s*:?|\*{0,2}Pour\s+aller\s+plus\s+loin\s*:?)"
    match_split = re.search(split_pattern, clean_answer, flags=re.IGNORECASE)

    questions_raw = ""

    if match_disc:
        # Tout ce qui se trouve après le disclaimer est destiné aux questions suggérées
        clean_body = clean_answer[:match_disc.end()].strip()
        questions_raw = clean_answer[match_disc.end():].strip()

        # Si un séparateur explicite était placé AVANT le disclaimer
        if match_split and match_split.start() < match_disc.start():
            questions_raw = clean_answer[match_split.end():match_disc.start()].strip()
            clean_body = (clean_answer[:match_split.start()].strip() + "\n\n" + clean_answer[match_disc.start():match_disc.end()].strip()).strip()

    elif match_split:
        clean_body = clean_answer[:match_split.start()].strip()
        questions_raw = clean_answer[match_split.end():].strip()
    else:
        # Vérification si les 2 dernières lignes du texte sont des questions
        lines = [line.strip() for line in clean_answer.split("\n") if line.strip()]
        trailing_qs = []
        for line in reversed(lines):
            if line.endswith("?") and len(line) >= 12:
                trailing_qs.insert(0, line)
                if len(trailing_qs) == 2:
                    break
            else:
                break
        if len(trailing_qs) == 2:
            questions_raw = "\n".join(trailing_qs)
            clean_body = clean_answer[:clean_answer.rfind(trailing_qs[0])].strip()

    skip_phrases = [
        "souhaitez-vous approfondir", "voulez-vous approfondir", "souhaitez-vous aller plus loin",
        "voulez-vous en savoir plus", "pour aller plus loin", "questions suggerees", 
        "suggested_questions", "voici 2 questions", "voici deux questions", 
        "questions que vous pourriez", "propositions de questions", "questions de suivi"
    ]

    if questions_raw:
        for line in questions_raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^[-*•\d.)\s]+", "", line).strip()
            cleaned = re.sub(r"^\*{1,2}|\*{1,2}$", "", cleaned).strip()

            # Ignorer les phrases d'introduction pures
            if any(intro in cleaned.lower() for intro in skip_phrases) and not cleaned.endswith("?"):
                continue
            if any(cleaned.lower().startswith(intro) for intro in ["souhaitez-vous approfondir", "voulez-vous approfondir"]):
                continue
            if "approfondir l'un de ces" in cleaned.lower() or "questions suivantes" in cleaned.lower():
                continue

            if len(cleaned) >= 10 and (cleaned.endswith("?") or any(cleaned.lower().startswith(w) for w in ["comment", "quel", "quelle", "quels", "quelles", "faut", "est-ce", "pourquoi", "dans quelle"])):
                suggested_questions.append(cleaned)
            elif len(cleaned) >= 15:
                suggested_questions.append(cleaned)

    # 3. Fallback dynamique si moins de 2 questions trouvées
    if len(suggested_questions) < 2:
        suggested_questions = get_dynamic_fallback_questions(user_query, portfolio_context, suggested_questions)

    return clean_body, suggested_questions[:2]


async def ask_financial_assistant(
    user_query: str,
    portfolio_context: Optional[Dict[str, Any]] = None,
    chat_history: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    Exécute une recherche vectorielle dans PGVector (similarité cosinus) 
    et génère une réponse sourcée, factuelle et contextualisée au portefeuille de l'utilisateur via Groq.
    
    Retourne un dictionnaire avec la réponse rédigée et les sources documentaires utilisées.
    """
    start_time = time.time()
    
    # 0. Garde-fou rapide : refus immédiat des requêtes hors-périmètre (recettes, blagues, météo, etc.)
    if is_strictly_off_topic(user_query):
        return {
            "answer": (
                "Je suis Copilot AI, votre assistant spécialisé en ingénierie financière et gestion de portefeuille. "
                "Mon rôle est strictement dédié à l'analyse de vos investissements, de vos actifs et des marchés financiers. "
                "Comment puis-je vous aider concernant votre portefeuille ou vos investissements ?"
            ),
            "sources": [],
            "latency_ms": round((time.time() - start_time) * 1000, 2)
        }
    
    # 1. Recherche vectorielle dans PGVector avec score cosinus et déduplication par ticker
    try:
        semantic_docs = search_similar_documents(user_query, k=30, max_results=10)
    except Exception as e:
        logger.error(f"Erreur de recherche vectorielle : {e}")
        semantic_docs = []

    # Recherche hybride : fiches des tickers cités explicitement d'abord, puis résultats sémantiques
    direct_docs = get_documents_for_tickers(find_explicit_tickers(user_query))
    direct_tickers = {d["ticker"] for d in direct_docs}
    retrieved_docs = (direct_docs + [d for d in semantic_docs if d.get("ticker") not in direct_tickers])[:10]

    # Formatage des sources pour le client frontend
    sources_summary: List[Dict[str, Any]] = []
    context_parts = []
    
    for i, doc in enumerate(retrieved_docs, 1):
        parsed_info = extract_doc_structured_info(doc.get("content", ""))
        snippet = doc["content"][:220] + "..." if len(doc["content"]) > 220 else doc["content"]
        
        sources_summary.append({
            "ticker": doc.get("ticker", "N/A"),
            "name": doc.get("name", f"Document {i}"),
            "category": doc.get("category", "Général"),
            "similarity_score_pct": doc.get("similarity_score_pct"),
            "match_type": doc.get("match_type", "semantic"),
            "snippet": snippet,
            "strategy": parsed_info.get("strategy") or snippet,
            "aum": parsed_info.get("aum"),
            "holdings": parsed_info.get("holdings", []),
            "paris_aligned": parsed_info.get("paris_aligned", False),
            "source_url": doc.get("source_url") or f"https://finance.yahoo.com/quote/{doc.get('ticker', '')}",
            "pdf_url": doc.get("pdf_url") or f"https://www.justetf.com/fr/search.html?search=ETFS&query={doc.get('ticker', '')}",
            "provider": doc.get("provider", "Yahoo Finance & Émetteur")
        })
        context_parts.append(f"--- Fiche {i} [{doc.get('ticker')}] : {doc.get('name')} ---\n{doc['content']}")

    if not retrieved_docs:
        context_str = "Aucun document financier pertinent trouvé dans la base vectorielle pour cette requête."
    else:
        context_str = "\n\n".join(context_parts)

    # 2. Formatage des données réelles du portefeuille de l'utilisateur (si fourni)
    portfolio_str = ""
    if portfolio_context:
        port_val = portfolio_context.get("total_value", 0.0)
        cost_basis = portfolio_context.get("cost_basis", 0.0)
        positions = portfolio_context.get("positions", [])
        metrics = portfolio_context.get("metrics", {})
        alloc = portfolio_context.get("allocation", {})

        pos_lines = []
        for p in positions:
            pos_lines.append(
                f"  - **{p.get('ticker')}** ({p.get('name')}) : Poids {p.get('weight_pct', 0):.1f}% | "
                f"Valeur {p.get('value_eur', 0):,.2f} € | PRU {p.get('pru', 0):,.2f} € | "
                f"Catégorie: {p.get('category', 'N/A')}"
            )
        pos_summary = "\n".join(pos_lines) if pos_lines else "  - Aucune position actuellement renseignée."

        portfolio_str = f"""
===================================================================
DONNÉES RÉELLES DU PORTEFEUILLE DE L'UTILISATEUR CONNECTÉ :
• Valeur Totale du Portefeuille : {port_val:,.2f} € (Coût de revient d'achat : {cost_basis:,.2f} €)
• Métriques Quantitatives & Indicateurs de Risque Réels :
  - Ratio de Sharpe Annualisé : {metrics.get('sharpe_ratio', 'N/A')}
  - Volatilité Annualisée : {metrics.get('volatility_pct', 'N/A')}%
  - Rendement Annualisé : {metrics.get('annualized_return_pct', 'N/A')}%
  - Perte Maximale Historique (Max Drawdown) : {metrics.get('max_drawdown_pct', 'N/A')}%
• Répartition Actuelle par Classe d'Actif :
  - FNB / ETF Cotés : {alloc.get('fnb_pct', 0):.1f}%
  - Actions Individuelles : {alloc.get('stocks_pct', 0):.1f}%
  - Actifs Non Boursiers (SCPI, Private Equity, Liquidités) : {alloc.get('unlisted_pct', 0):.1f}%
• Détail des Lignes d'Actifs Détenues :
{pos_summary}
===================================================================
"""

    # 3. Construction du prompt institutionnel RAG enrichi du portefeuille
    RAG_PROMPT = f"""Tu es Copilot AI, un conseiller et ingénieur financier d'élite spécialisé en gestion de portefeuille et analyse quantitative.
Ton style est fluide, direct, hautement informatif, chiffré et SUR-MESURE. Tu réponds avec le ton naturel d'un gérant de portefeuille chevronné.

{portfolio_str}

DIRECTIVES MÉTHODOLOGIQUES :

1. PÉRIMÈTRE D'INTERVENTION STRICT (FINANCE & MARCHÉS UNIQUEMENT) :
   - Tu traites EXCLUSIVEMENT de gestion de patrimoine, finance de marché, ETF, actions, macroéconomie et ingénierie de portefeuille.
   - Si la demande de l'utilisateur sort de ce cadre (recettes, cuisine, cinéma, météo, blagues, programmation générale, jeux vidéo, etc.) :
     * REFUSE poliment, sobrement et fermement en une phrase :
       "Je suis Copilot AI, votre assistant dédié à l'analyse financière et à la gestion de portefeuille. Comment puis-je vous aider concernant vos investissements ou vos actifs ?"
     * Ne cite aucune source documentaire, aucune question suggérée et n'ajoute pas de clause de non-conseil.

2. ADAPTABILITÉ ABSOLUE & REFUS DE FORMAT FIGÉ (NE SOIS JAMAIS RIGIDE) :
   - N'APPLIQUE JAMAIS de structure mécanique ou de modèle préfabriqué répétitif (interdiction formelle de forcer systématiquement un plan "Diagnostic Flash / Faiblesses / Plan d'Action").
   - Adapte organiquement ta réponse à la nature et à la spécificité exacte de la question posée :
     * Si l'utilisateur demande un audit global ou les risques généraux : Synthétise les métriques réelles (valeur, Sharpe, volatilité, drawdown) et dégage les 2-3 points clés d'attention et d'arbitrage.
     * Si la question porte sur les doublons ou chevauchements (ex: S&P 500 / MSCI World / Nasdaq) : Réponds DIRECTEMENT sur le chevauchement sous-jacent en t'appuyant sur les positions listées dans les fiches (titres communs, redondance des frais) et propose une solution de dé-corrélation ciblée sans réciter tout le portefeuille.
     * Si la question porte sur l'exposition sectorielle ou géographique (ex: Tech, USA) : Commente l'exposition à partir des poids présents dans les fiches et les données du portefeuille, sans récapituler tout le portefeuille.
     * Si la question porte sur un actif spécifique ou une idée d'ajout (ex: XEON, GLD, WPEA, obligations) : Analyse directement les forces, faiblesses, frais et le rôle de cet actif dans la diversification par rapport aux positions actuelles.
     * Si la question est une relance ou une précision : Réponds directement et précisément à la demande, sans préambule superflu.

3. RÈGLE STRICTE SUR LES CHIFFRES (AUCUN CALCUL) :
   - Tu n'effectues AUCUN calcul : ni pondération, ni somme, ni moyenne, ni exposition look-through cumulée, ni projection.
   - Chaque chiffre que tu cites doit figurer tel quel dans le contexte documentaire ou dans les données du portefeuille ci-dessus.
   - Si un chiffre utile n'y figure pas (ex: exposition cumulée à un titre via plusieurs ETF), dis explicitement qu'il n'est pas disponible dans les sources au lieu de l'estimer.
   - Les chiffres de ta réponse sont vérifiés automatiquement contre ces sources ; tout chiffre absent sera signalé à l'utilisateur.

4. FORME, TONE OF VOICE & EXIGENCE :
   - Réponses directes, concises et à haute densité informative (150 à 300 mots maximum).
   - Pas de politesse creuse en début de message ("Bonjour", "C'est une excellente question..."). Entre directement dans le vif du sujet.
   - Utilise des puces courtes ou des paragraphes nets selon ce qui rend l'explication la plus percutante.
   - Mets systématiquement les tickers en gras (`CW8.PA`, `SPY`, `GLD`, `XEON.DE`, `WPEA.PA`).
   - N'utilise AUCUN émoji dans ton texte.
   - La réponse doit être achevée et complète (toutes les phrases terminées).
   - Pour toute analyse financière, termine obligatoirement sur une nouvelle ligne par :
     "Ceci ne constitue pas un conseil en investissement personnalisé."

5. QUESTIONS DE SUIVI SUGGÉRÉES (OBLIGATOIRE - EXACTEMENT 2 QUESTIONS SPÉCIFIQUES & ADAPTÉES) :
   - Immédiatement après la mention "Ceci ne constitue pas un conseil en investissement personnalisé.", saute une ligne et propose EXACTEMENT 2 questions de relance personnalisées, en lien direct et spécifique avec le sujet discuté :
- [Première question précise pour approfondir ?]
- [Deuxième question précise pour approfondir ?]
   - Ne mets AUCUN titre ou texte d'introduction entre la mention de non-conseil et les deux questions (elles sont automatiquement extraites et présentées comme boutons interactifs sous ta réponse).


Contexte documentaire :
{context_str}
"""

    if not client:
        return {
            "answer": "Service LLM non configuré (clé GROQ_API_KEY manquante). Voici néanmoins les documents trouvés dans la base vectorielle :\n\n" + 
                     "\n".join([f"- **[{s['name']}]({s['source_url']})** (`{s['ticker']}`) : {'source directe' if s['similarity_score_pct'] is None else 'similarité ' + str(s['similarity_score_pct']) + ' %'} — [Fiche KID ↗]({s['pdf_url']})" for s in sources_summary]),
            "sources": sources_summary,
            "suggested_questions": [],
            "latency_ms": round((time.time() - start_time) * 1000, 2)
        }

    # 3. Génération via Groq avec cascade de modèles fiables et plafond de tokens calibré
    candidate_models = RAG_MODEL_CASCADE
    last_error = None

    # Préparation des messages avec historique conversationnel multi-tours
    messages_payload = [{"role": "system", "content": RAG_PROMPT}]
    if chat_history:
        for h in chat_history[-6:]:
            r = h.get("role")
            c = h.get("content")
            if r in ["user", "assistant"] and c:
                messages_payload.append({"role": r, "content": c})
    messages_payload.append({"role": "user", "content": user_query})

    for model_name, limit_tokens in candidate_models:
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages_payload,
                temperature=0.15,
                max_tokens=limit_tokens
            )
            
            raw_answer = response.choices[0].message.content or ""
            # Nettoyer d'éventuels blocs de réflexion internes
            clean_answer = re.sub(r"<think>.*?</think>", "", raw_answer, flags=re.DOTALL).strip()
            
            latency = round((time.time() - start_time) * 1000, 2)
            
            # Filtrage de pertinence des sources :
            # 1. Si la réponse est un refus hors-sujet ou conversationnel, aucune source à afficher
            is_off_topic_reply = any(phrase in clean_answer.lower() for phrase in [
                "spécialisé dans", "spécialisé en", "recette", "hors de mon domaine", 
                "ne peux pas vous fournir", "ne peux pas vous aider", "ne suis pas en mesure", 
                "hors sujet", "je ne traite que", "demandes extra-financières", 
                "mon périmètre est strictement", "strictement limité", "extra-financière"
            ])
            
            # Gestion des questions suggérées : extraction stricte après la mention de non-conseil
            if is_off_topic_reply:
                suggested_questions = []
            else:
                clean_answer, suggested_questions = extract_questions_and_clean_answer(
                    clean_answer,
                    user_query=user_query,
                    portfolio_context=portfolio_context
                )

            final_sources: List[Dict[str, Any]] = []
            seen_source_tickers = set()

            if not is_off_topic_reply and len(clean_answer) > 40:
                # 1. Sources issues de la recherche vectorielle : le seuil de similarité cosinus
                # est déjà appliqué dans search_similar_documents, aucun second filtre ici.
                for s in sources_summary:
                    final_sources.append(s)
                    tick = (s.get("ticker") or "").upper().strip()
                    if tick:
                        seen_source_tickers.add(tick)
                        seen_source_tickers.add(tick.split(".")[0])

                # 2. Détection dynamique de tous les tickers boursiers cités dans la réponse ou le portefeuille
                candidate_tickers = set()
                # Tickers en gras, entre guillemets, ou en code (ex: **SPY**, `CW8.PA`, **AGG**, **EMB**, **GLD**)
                for match in re.finditer(r"[\*`\"']{1,2}([A-Z][A-Z0-9]{1,5}(?:\.[A-Z]{2})?)[\*`\"']{1,2}", clean_answer):
                    candidate_tickers.add(match.group(1).upper())
                
                # Tickers entre parenthèses ou mots-clés boursiers
                for match in re.finditer(r"\b([A-Z][A-Z0-9]{1,5}(?:\.[A-Z]{2})?)\b", clean_answer):
                    cand = match.group(1).upper()
                    if cand in ["SPY", "QQQ", "AGG", "EMB", "GLD", "TLT", "CW8", "WPEA", "PE500", "PUST", "XEON", "ETZ", "CAC", "PAEEM", "IWM", "XLK", "XLF", "XLV", "XLE", "SMH", "SLV", "VTI", "VEA", "VWO", "PABZ"]:
                        candidate_tickers.add(cand)

                # Positions réelles du portefeuille
                if portfolio_context:
                    for pos in portfolio_context.get("positions", []):
                        if pos.get("ticker"):
                            candidate_tickers.add(pos["ticker"].upper())

                # Requête de l'utilisateur
                for match in re.finditer(r"\b([A-Z][A-Z0-9]{1,5}(?:\.[A-Z]{2})?)\b", user_query):
                    candidate_tickers.add(match.group(1).upper())

                # Récupération automatique de la fiche documentaire pour chaque ticker cité
                for cand in candidate_tickers:
                    cand_base = cand.split(".")[0]
                    if cand not in seen_source_tickers and cand_base not in seen_source_tickers:
                        extra_src = fetch_source_for_ticker(cand)
                        if extra_src:
                            final_sources.append(extra_src)
                            seen_source_tickers.add(cand)
                            seen_source_tickers.add(cand_base)

            # Vérification a posteriori : chaque chiffre de la réponse doit provenir de ce que le LLM
            # a réellement reçu (fiches du contexte, données du portefeuille, messages de l'utilisateur).
            unverified_numbers: List[str] = []
            if not is_off_topic_reply:
                user_turns = [h.get("content", "") for h in (chat_history or []) if h.get("role") == "user"]
                unverified_numbers = find_unverified_numbers(
                    clean_answer, [context_str, portfolio_str, user_query, *user_turns]
                )
                if unverified_numbers:
                    logger.warning(f"Chiffres non retrouvés dans les sources : {unverified_numbers}")
                    clean_answer += (
                        "\n\n> **Vérification automatique :** les chiffres suivants ne figurent pas dans les "
                        "sources fournies au modèle et doivent être considérés comme non vérifiés : "
                        + ", ".join(f"`{n}`" for n in unverified_numbers) + "."
                    )

            # Ne plus brider à 3 sources : inclure toutes les sources pertinentes (jusqu'à 12)
            return {
                "answer": clean_answer,
                "sources": final_sources[:12],
                "suggested_questions": suggested_questions,
                "unverified_numbers": unverified_numbers,
                "latency_ms": latency
            }
            
        except Exception as e:
            logger.warning(f"Modèle {model_name} indisponible ou limite atteinte : {e}. Bascule sur le modèle de secours...")
            last_error = e

    # Fallback si tous les modèles ont échoué
    fallback_answer = (
        f"Une erreur temporaire est survenue lors de la synthèse de la réponse ({str(last_error)}). "
        f"Toutefois, voici les {len(sources_summary)} documents identifiés dans la base vectorielle avec leurs liens de vérification directe :\n\n"
    )
    for s in sources_summary:
        fallback_answer += f"- **[{s['name']}]({s['source_url']})** (`{s['ticker']}`) : {'source directe' if s['similarity_score_pct'] is None else 'pertinence ' + str(s['similarity_score_pct']) + ' %'} — [Fiche KID / Factsheet ↗]({s['pdf_url']})\n"
        
    return {
        "answer": fallback_answer,
        "sources": sources_summary,
        "suggested_questions": get_dynamic_fallback_questions(user_query, portfolio_context),
        "latency_ms": round((time.time() - start_time) * 1000, 2)
    }