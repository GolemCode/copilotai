import asyncio
from types import SimpleNamespace

import pytest

from app.services.rag_validation import (
    MIN_COSINE_SIMILARITY,
    cosine_similarity_from_distance,
    find_unverified_numbers,
)


# ---------------------------------------------------------------------------
# Seuil de similarité cosinus
# ---------------------------------------------------------------------------

def test_cosine_similarity_from_pgvector_distance():
    # pgvector renvoie d = 1 - cos : la similarité réelle est 1 - d (et non 1 - d/2)
    assert cosine_similarity_from_distance(0.0) == pytest.approx(1.0)
    assert cosine_similarity_from_distance(0.9) == pytest.approx(0.1)
    assert cosine_similarity_from_distance(1.0) == pytest.approx(0.0)
    assert cosine_similarity_from_distance(2.0) == pytest.approx(-1.0)


class _FakeDoc:
    def __init__(self, ticker, content="Fiche descriptive de test"):
        self.metadata = {"ticker": ticker, "name": f"Fonds {ticker}"}
        self.page_content = content


class _FakeStore:
    def __init__(self, results):
        self._results = results

    def similarity_search_with_score(self, query, k):
        return self._results[:k]


def test_search_filters_on_real_cosine_threshold(monkeypatch):
    rag_ingestion = pytest.importorskip("app.services.rag_ingestion")
    fake_results = [
        (_FakeDoc("CW8.PA"), 0.20),   # cos = 0.80 -> conservé
        (_FakeDoc("SPY"), 0.65),      # cos = 0.35 -> conservé (>= 0.30)
        # cos = 0.10 : passait l'ancien seuil "55 %" car (1 + 0.10) / 2 = 0.55, doit être rejeté
        (_FakeDoc("GLD"), 0.90),
        (_FakeDoc("TLT"), 1.20),      # cos < 0 -> rejeté
    ]
    monkeypatch.setattr(rag_ingestion, "get_vectorstore", lambda: _FakeStore(fake_results))

    results = rag_ingestion.search_similar_documents("ETF monde", k=10)

    assert [r["ticker"] for r in results] == ["CW8.PA", "SPY"]
    assert results[0]["similarity_score_pct"] == pytest.approx(80.0)
    assert results[1]["similarity_score_pct"] == pytest.approx(35.0)
    assert all(r["similarity_score"] >= MIN_COSINE_SIMILARITY for r in results)


# ---------------------------------------------------------------------------
# Vérification des chiffres cités par le LLM
# ---------------------------------------------------------------------------

CONTEXT = (
    "--- Fiche 1 [XDPE.PA] ---\n"
    "- Microsoft Corp (7.10%) : Énergie renouvelable.\n"
    "- Apple Inc (6.30%) : Produits recyclés.\n"
    "Profil de Risque : SRI 4/7\n"
)
PORTFOLIO = (
    "• Valeur Totale du Portefeuille : 10,000.00 € (Coût de revient d'achat : 8,500.00 €)\n"
    "  - Ratio de Sharpe Annualisé : 0.85\n"
)


def test_numbers_present_in_sources_are_verified_across_formats():
    answer = (
        "Votre portefeuille vaut 10 000 € pour un coût de 8 500,00 €, avec un Sharpe de 0,85. "
        "Microsoft pèse 7,1 % de **XDPE.PA** et Apple 6.3 %. Deux lignes (CW8, PE500) se recoupent."
    )
    assert find_unverified_numbers(answer, [CONTEXT, PORTFOLIO]) == []


def test_numbers_computed_by_the_llm_are_flagged():
    # 13,4 % = 7,10 + 6,30 : un calcul fait par le LLM, absent des sources -> doit être signalé
    answer = "L'exposition cumulée Microsoft + Apple atteint 13,4 %, soit 1 340 € investis."
    unverified = find_unverified_numbers(answer, [CONTEXT, PORTFOLIO])
    assert "13,4 %" in unverified
    assert "1 340 €" in unverified


def test_numbers_from_user_question_count_as_sources():
    answer = "Avec un apport de 2 500 € par mois, la part de Microsoft reste de 7,10 %."
    assert find_unverified_numbers(answer, [CONTEXT, "J'investis 2500 € par mois"]) == []


def test_ask_financial_assistant_flags_unverified_numbers(monkeypatch):
    llm = pytest.importorskip("app.services.llm")

    retrieved = [{
        "ticker": "XDPE.PA",
        "name": "Xtrackers S&P 500 ESG",
        "category": "Actions US / ESG",
        "content": CONTEXT,
        "similarity_score_pct": 62.0,
    }]
    llm_answer = (
        "**XDPE.PA** détient Microsoft à 7,10 % et Apple à 6,30 %. "
        "Votre exposition cumulée à ces deux titres est de 13,4 %.\n"
        "Ceci ne constitue pas un conseil en investissement personnalisé.\n"
        "- Quelle part de Tech US détient mon portefeuille ?\n"
        "- Comment réduire le chevauchement entre mes ETF ?"
    )

    async def fake_create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=llm_answer))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(llm, "client", fake_client)
    monkeypatch.setattr(llm, "search_similar_documents", lambda *a, **kw: retrieved)
    monkeypatch.setattr(llm, "get_documents_for_tickers", lambda tickers: [])

    result = asyncio.run(llm.ask_financial_assistant("Quel est mon chevauchement sur XDPE ?"))

    assert result["unverified_numbers"] == ["13,4 %"]
    assert "Vérification automatique" in result["answer"]
    assert result["sources"][0]["match_type"] == "semantic"


def _doc(ticker, score=None, match_type="semantic"):
    return {"ticker": ticker, "similarity_score_pct": score, "match_type": match_type}


def test_context_keeps_cited_assets_and_only_close_semantic_matches():
    from app.services.rag_validation import select_context_documents

    direct = [_doc("CW8.PA", match_type="direct")]
    semantic = [_doc("VWCE.DE", 46.0), _doc("WPEA.PA", 41.0), _doc("PUST.PA", 38.0), _doc("XEON.DE", 37.0)]
    selected = [d["ticker"] for d in select_context_documents(direct, semantic)]
    # Fiche citée d'abord, puis au plus deux compléments à moins de 8 points du meilleur score
    assert selected == ["CW8.PA", "VWCE.DE", "WPEA.PA"]


def test_context_without_cited_asset_drops_distant_matches():
    from app.services.rag_validation import select_context_documents

    semantic = [_doc("CW8.PA", 52.0), _doc("WPEA.PA", 49.0), _doc("XEON.DE", 33.0)]
    assert [d["ticker"] for d in select_context_documents([], semantic)] == ["CW8.PA", "WPEA.PA"]


def test_displayed_sources_are_those_cited_in_the_answer():
    from app.services.rag_validation import select_cited_sources

    sources = [_doc("CW8.PA", match_type="direct"), _doc("VWCE.DE", 46.0), _doc("XEON.DE", 37.0)]
    answer = "**CW8.PA** : frais courants de 0,38 %, contre 0,19 % pour VWCE."
    assert [s["ticker"] for s in select_cited_sources(sources, answer)] == ["CW8.PA", "VWCE.DE"]
    # « PE500 » ne doit pas valoir citation de « PE5 », ni « CW8 » dans « XCW80 »
    assert select_cited_sources([_doc("CW8.PA", 50.0)], "Voir XCW80 et PE500.")[0]["ticker"] == "CW8.PA"


def test_uncited_answer_falls_back_to_requested_sheets():
    from app.services.rag_validation import select_cited_sources

    sources = [_doc("CW8.PA", match_type="direct"), _doc("VWCE.DE", 46.0)]
    assert [s["ticker"] for s in select_cited_sources(sources, "Réponse générale sans ticker.")] == ["CW8.PA"]


def test_portfolio_reference_detection():
    from app.services.rag_validation import refers_to_portfolio

    assert refers_to_portfolio("Quels sont les frais de mes ETF ?")
    assert refers_to_portfolio("Les points faibles de mon portefeuille")
    assert not refers_to_portfolio("Que contient un ETF MSCI World ?")
