"""
Fonctions de contrôle du pipeline RAG, sans dépendance lourde (testables isolément) :
- conversion distance pgvector -> similarité cosinus réelle et seuil de pertinence ;
- vérification a posteriori que les chiffres cités par le LLM figurent dans ses sources.
"""
import re
from typing import Iterable, List, Set

# Seuil sur la similarité cosinus RÉELLE (et non sur (1 + cos) / 2).
# pgvector (stratégie COSINE de langchain_postgres) renvoie une distance d = 1 - cos(θ), dans [0, 2].
# Valeur à calibrer sur un jeu de requêtes annotées : elle dépend du modèle d'embedding
# (all-MiniLM-L6-v2, entraîné en anglais, donne des cosinus modestes sur du texte français).
MIN_COSINE_SIMILARITY = 0.30


def cosine_similarity_from_distance(distance: float) -> float:
    """Convertit une distance cosinus pgvector (1 - cos) en similarité cosinus, bornée à [-1, 1]."""
    return max(-1.0, min(1.0, 1.0 - float(distance)))


# Nombre isolé (pas collé à des lettres, pour ignorer les tickers comme CW8 ou PE500).
# Accepte les séparateurs de milliers par espace (y compris insécables), virgule ou point.
_NUMBER_PATTERN = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[   ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![\w])"
    r"(\s?(?:%|€|\$|EUR|USD))?"
)


def _candidate_values(token: str) -> Set[float]:
    """
    Renvoie les lectures numériques plausibles d'un nombre écrit en texte libre.
    Les formats français (1 234,56) et anglais (1,234.56) coexistent dans le prompt :
    une écriture ambiguë comme "1,234" produit donc deux candidats (1.234 et 1234).
    """
    t = re.sub(r"[   ]", "", token)
    candidates: Set[float] = set()

    if "," in t and "." in t:
        # Le dernier séparateur rencontré est le séparateur décimal
        if t.rfind(",") > t.rfind("."):
            candidates.add(float(t.replace(".", "").replace(",", ".")))
        else:
            candidates.add(float(t.replace(",", "")))
        return candidates

    for sep in (",", "."):
        if sep in t:
            parts = t.split(sep)
            if len(parts) == 2:
                candidates.add(float(parts[0] + "." + parts[1]))
            if all(len(p) == 3 for p in parts[1:]):
                candidates.add(float("".join(parts)))
            return candidates

    candidates.add(float(t))
    return candidates


def _decimals(token: str) -> int:
    """Nombre de décimales affichées (sert de tolérance d'arrondi)."""
    m = re.search(r"[.,](\d{1,2})$", re.sub(r"[   ]", "", token))
    return len(m.group(1)) if m else 0


def extract_numbers(text: str) -> List[dict]:
    """Extrait les nombres d'un texte avec leurs lectures possibles et leur précision d'affichage."""
    found = []
    for match in _NUMBER_PATTERN.finditer(text):
        token, unit = match.group(1), (match.group(2) or "").strip()
        try:
            values = _candidate_values(token)
        except ValueError:
            continue
        found.append({"raw": (token + (" " + unit if unit else "")).strip(), "values": values,
                      "decimals": _decimals(token), "unit": unit})
    return found


def find_unverified_numbers(answer: str, reference_texts: Iterable[str]) -> List[str]:
    """
    Liste les nombres cités dans la réponse qui ne figurent dans aucun texte de référence
    (contexte documentaire, données du portefeuille, question de l'utilisateur).

    Un nombre est considéré comme vérifié s'il est égal à un nombre des sources,
    à l'arrondi d'affichage près (ex. "7,1 %" dans la réponse et "7.10%" dans la fiche).
    Les petits entiers sans unité (0 à 10) sont ignorés : ce sont surtout des
    énumérations ("2 questions", "3 axes") plutôt que des données financières.
    """
    reference_values: Set[float] = set()
    for text in reference_texts:
        for num in extract_numbers(text or ""):
            reference_values.update(num["values"])

    unverified: List[str] = []
    for num in extract_numbers(answer):
        if not num["unit"] and num["decimals"] == 0 and all(0 <= v <= 10 for v in num["values"]):
            continue
        tolerance = 0.5 * 10 ** (-num["decimals"]) + 1e-9
        is_verified = any(abs(v - ref) <= tolerance for v in num["values"] for ref in reference_values)
        if not is_verified and num["raw"] not in unverified:
            unverified.append(num["raw"])
    return unverified
