import re
import logging
from typing import List, Dict, Any, Optional
from langchain_core.documents import Document

from app.core.settings import PSYCOPG_CONNINFO, SQLALCHEMY_DATABASE_URL
from app.services.rag_validation import MIN_COSINE_SIMILARITY, cosine_similarity_from_distance

logger = logging.getLogger(__name__)

# Fallback résilient pour RecursiveCharacterTextSplitter
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError:
        class RecursiveCharacterTextSplitter:
            def __init__(self, chunk_size=800, chunk_overlap=120, separators=None):
                self.chunk_size = chunk_size
                self.chunk_overlap = chunk_overlap
                self.separators = separators or ["\n\n", "\n", ". ", "; ", " ", ""]

            def split_text(self, text: str) -> List[str]:
                if not text or not text.strip():
                    return []
                text = text.strip()
                if len(text) <= self.chunk_size:
                    return [text]

                chunks = []
                start = 0
                while start < len(text):
                    if len(text) - start <= self.chunk_size:
                        chunk = text[start:].strip()
                        if chunk:
                            chunks.append(chunk)
                        break

                    end = start + self.chunk_size
                    best_pos = -1
                    for sep in self.separators:
                        pos = text.rfind(sep, start, end)
                        if pos > start + (self.chunk_size // 2):
                            best_pos = pos + len(sep)
                            break
                    if best_pos != -1:
                        end = best_pos

                    chunk = text[start:end].strip()
                    if chunk:
                        chunks.append(chunk)
                    start = max(start + 1, end - self.chunk_overlap)
                return chunks

            def split_documents(self, documents: List[Document]) -> List[Document]:
                result = []
                for doc in documents:
                    text = doc.page_content if hasattr(doc, "page_content") else str(doc)
                    meta = doc.metadata.copy() if hasattr(doc, "metadata") and doc.metadata else {}
                    for c in self.split_text(text):
                        result.append(Document(page_content=c, metadata=meta))
                return result

COLLECTION_NAME = "financial_rag_store"

# Singletons paresseux (Lazy-loading)
_embeddings = None
_vectorstore = None

def get_embeddings():
    """Initialise le modèle d'embedding Sentence Transformers de façon paresseuse."""
    global _embeddings
    if _embeddings is None:
        from langchain_huggingface import HuggingFaceEmbeddings
        logger.info("Chargement du modèle d'embedding sentence-transformers/all-MiniLM-L6-v2...")
        _embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    return _embeddings

def get_vectorstore():
    """Initialise ou récupère la connexion PGVector."""
    global _vectorstore
    if _vectorstore is None:
        from langchain_postgres.vectorstores import PGVector
        embeddings = get_embeddings()
        _vectorstore = PGVector(
            embeddings=embeddings,
            collection_name=COLLECTION_NAME,
            connection=SQLALCHEMY_DATABASE_URL,
            use_jsonb=True,
        )
    return _vectorstore

class _LazyVectorstoreProxy:
    def __getattr__(self, name):
        return getattr(get_vectorstore(), name)

vectorstore = _LazyVectorstoreProxy()

def get_text_splitter() -> RecursiveCharacterTextSplitter:
    """Retourne un splitter configuré pour les documents financiers denses."""
    return RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
        separators=["\n\n", "\n", ". ", "; ", " ", ""]
    )

FUND_DOCUMENT_SOURCE_TYPE = "Fiche scrapée (Yahoo Finance / JustETF)"


def _fmt_pct(value: Optional[float]) -> str:
    return f"{value:.2f}%".replace(".", ",") if value is not None else "Non publié"


def build_fund_document(snapshot: Dict[str, Any]) -> Document:
    """
    Transforme une fiche stockée en base (fund_profiles / fund_holdings / fund_exposures)
    en document texte à vectoriser. Les champs non publiés par les sources sont indiqués
    comme tels : le LLM ne doit jamais recevoir une valeur inventée.
    """
    p = snapshot["profile"]
    ticker = snapshot["ticker"]
    total_assets = p.get("total_assets")
    aum = f"{total_assets:,.0f} {p.get('total_assets_currency') or ''}".strip() if total_assets else "Non publié"
    collected = p["updated_at"].date().isoformat() if p.get("updated_at") else "inconnue"

    lines = [
        "Fiche Descriptive d'Actif Financier",
        f"Nom du fonds : {p.get('name') or ticker}",
        f"Symbole boursier (Ticker) : {ticker}",
        f"ISIN : {p.get('isin') or 'Non publié'}",
        f"Catégorie d'investissement : {p.get('category') or 'Non publiée'}",
        f"Gestionnaire (Émetteur) : {p.get('fund_family') or 'Non publié'}",
        f"Devise de cotation : {p.get('currency') or 'Non publiée'}",
        f"Actifs sous gestion (AUM) : {aum}",
        f"Frais courants (TER) : {_fmt_pct(p.get('expense_ratio_pct'))}",
        f"Date de collecte des données : {collected}",
        "",
    ]
    if snapshot["countries"]:
        lines.append("Répartition par pays :")
        lines += [f"- {c['name']} : {_fmt_pct(c['weight_pct'])}" for c in snapshot["countries"][:10]]
        lines.append("")
    if snapshot["sectors"]:
        lines.append("Répartition sectorielle :")
        lines += [f"- {s['name']} : {_fmt_pct(s['weight_pct'])}" for s in snapshot["sectors"][:10]]
        lines.append("")
    lines.append("Description détaillée et stratégie d'investissement :")
    lines.append(p.get("description") or "Non publiée par les sources.")
    lines.append("")
    lines.append("Principales positions du fonds (Top 10 Holdings) :")
    if snapshot["holdings"]:
        lines += [
            f"- {h['name']}{' (' + h['symbol'] + ')' if h.get('symbol') else ''} : {_fmt_pct(h['weight_pct'])}"
            for h in snapshot["holdings"]
        ]
    else:
        lines.append("Composition non publiée par Yahoo Finance ni par JustETF.")

    isin = p.get("isin")
    justetf_url = f"https://www.justetf.com/fr/etf-profile.html?isin={isin}" if isin else None
    return Document(
        page_content="\n".join(lines),
        metadata={
            "ticker": ticker,
            "name": p.get("name") or ticker,
            "category": p.get("category") or "Non publiée",
            "fundFamily": p.get("fund_family"),
            "isin": isin,
            "source_type": FUND_DOCUMENT_SOURCE_TYPE,
            "source_url": f"https://finance.yahoo.com/quote/{ticker}",
            "pdf_url": justetf_url or f"https://finance.yahoo.com/quote/{ticker}/holdings",
            "provider": " & ".join(s.capitalize() for s in (p.get("sources") or "yahoo").split(",")),
            "collected_at": collected,
        },
    )


def ingest_custom_documents(raw_docs: List[Dict[str, Any]]) -> int:
    """Ingère une liste de documents textuels personnalisés."""
    store = get_vectorstore()
    splitter = get_text_splitter()
    
    docs_to_split = []
    for item in raw_docs:
        content = item.get("content", "").strip()
        if not content:
            continue
            
        meta = item.get("metadata", {})
        meta.setdefault("title", item.get("title", "Document Financier"))
        meta.setdefault("ticker", item.get("ticker", "GENERAL"))
        meta.setdefault("source_type", item.get("source_type", "custom_doc"))
        
        docs_to_split.append(Document(page_content=content, metadata=meta))
        
    if not docs_to_split:
        return 0
        
    chunks = splitter.split_documents(docs_to_split)
    store.add_documents(chunks)
    logger.info(f"{len(chunks)} chunks vectorisés et insérés dans PGVector.")
    return len(chunks)

def delete_documents_by_tickers(tickers: List[str]):
    """Purge les anciens segments des tickers cibles dans PostgreSQL pour éviter les doublons."""
    import psycopg
    try:
        clean_tickers = [t.strip().upper() for t in tickers]
        with psycopg.connect(PSYCOPG_CONNINFO) as conn:
            with conn.cursor() as cur:
                for t in clean_tickers:
                    cur.execute("DELETE FROM langchain_pg_embedding WHERE cmetadata->>'ticker' = %s;", (t,))
            conn.commit()
        logger.info(f"Purge préalable des anciens chunks effectuée pour {len(clean_tickers)} tickers.")
    except Exception as pe:
        logger.warning(f"Avertissement lors de la purge préalable des chunks : {pe}")

def purge_legacy_documents() -> int:
    """
    Supprime de pgvector les segments produits par les anciennes versions du pipeline
    (dont les fiches climat rédigées à la main) : seuls restent les fiches scrapées et
    les documents ajoutés explicitement via /rag/ingest.
    """
    import psycopg
    try:
        with psycopg.connect(PSYCOPG_CONNINFO) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM langchain_pg_embedding
                    WHERE COALESCE(cmetadata->>'source_type', '') NOT IN (%s, 'custom_doc');
                """, (FUND_DOCUMENT_SOURCE_TYPE,))
                deleted = cur.rowcount
            conn.commit()
        logger.info(f"{deleted} segments hérités supprimés de pgvector.")
        return deleted
    except Exception as e:
        logger.warning(f"Purge des segments hérités impossible : {e}")
        return 0


def index_fund_documents(snapshots: List[Dict[str, Any]]) -> int:
    """Vectorise les fiches collectées : purge des anciens segments des tickers puis insertion."""
    if not snapshots:
        logger.warning("Aucune fiche à indexer.")
        return 0
    docs = [build_fund_document(s) for s in snapshots]
    split_docs = get_text_splitter().split_documents(docs)
    # Rang du segment dans sa fiche : permet de reconstituer la fiche dans l'ordre (recherche par ticker)
    counters: Dict[str, int] = {}
    for doc in split_docs:
        ticker = doc.metadata["ticker"]
        doc.metadata["chunk_index"] = counters.get(ticker, 0)
        counters[ticker] = doc.metadata["chunk_index"] + 1
    delete_documents_by_tickers([s["ticker"] for s in snapshots])
    get_vectorstore().add_documents(split_docs)
    logger.info(f"{len(split_docs)} segments indexés pour {len(snapshots)} fiches.")
    return len(split_docs)


def purge_fund_documents_except(tickers: List[str]) -> int:
    """Supprime les fiches scrapées des tickers absents de la dernière collecte (actif retiré ou inconnu)."""
    import psycopg
    keep = [t.strip().upper() for t in tickers]
    try:
        with psycopg.connect(PSYCOPG_CONNINFO) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM langchain_pg_embedding
                    WHERE cmetadata->>'source_type' = %s AND NOT (UPPER(cmetadata->>'ticker') = ANY(%s));
                """, (FUND_DOCUMENT_SOURCE_TYPE, keep))
                deleted = cur.rowcount
            conn.commit()
        return deleted
    except Exception as e:
        logger.warning(f"Purge des fiches orphelines impossible : {e}")
        return 0


def find_explicit_tickers(text: str) -> List[str]:
    """Symboles cités tels quels dans une question (ex. CW8.PA, SPY, cw8.pa)."""
    candidates = set(re.findall(r"\b[A-Z][A-Z0-9]{1,5}(?:\.[A-Z]{1,2})?\b", text))
    candidates |= {m.upper() for m in re.findall(r"\b[a-zA-Z0-9]{2,6}\.[a-zA-Z]{1,2}\b", text)}
    return sorted(candidates)


def get_documents_for_tickers(tickers: List[str]) -> List[Dict[str, Any]]:
    """
    Recherche EXACTE, complément de la recherche sémantique : la fiche d'un actif cité explicitement
    dans la question est toujours fournie au LLM, même si sa similarité cosinus est faible
    (le modèle d'embedding, entraîné en anglais, classe mal des fiches très proches entre elles).
    """
    if not tickers:
        return []
    import psycopg
    keys = sorted({t.strip().upper() for t in tickers})
    bases = sorted({k.split(".")[0] for k in keys})
    try:
        with psycopg.connect(PSYCOPG_CONNINFO) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT document, cmetadata FROM langchain_pg_embedding
                    WHERE cmetadata->>'source_type' = %s
                      AND (UPPER(cmetadata->>'ticker') = ANY(%s) OR SPLIT_PART(UPPER(cmetadata->>'ticker'), '.', 1) = ANY(%s))
                    ORDER BY cmetadata->>'ticker', COALESCE((cmetadata->>'chunk_index')::int, 0);
                """, (FUND_DOCUMENT_SOURCE_TYPE, keys, bases))
                rows = cur.fetchall()
    except Exception as e:
        logger.warning(f"Recherche exacte par ticker impossible : {e}")
        return []

    grouped: Dict[str, Dict[str, Any]] = {}
    for content, meta in rows:
        ticker = (meta or {}).get("ticker", "N/A").upper()
        entry = grouped.setdefault(ticker, {"meta": meta or {}, "chunks": []})
        entry["chunks"].append(content)

    results = []
    for ticker, entry in grouped.items():
        meta = entry["meta"]
        results.append({
            "ticker": ticker,
            "name": meta.get("name", ticker),
            "category": ETF_CATEGORY_OVERRIDES.get(ticker) or meta.get("category", "Fonds"),
            # segments qui se chevauchent : la fiche reconstituée peut répéter quelques lignes
            "content": "\n".join(entry["chunks"]),
            "similarity_score_pct": None,
            "match_type": "direct",
            "source_url": meta.get("source_url") or f"https://finance.yahoo.com/quote/{ticker}",
            "pdf_url": meta.get("pdf_url"),
            "provider": meta.get("provider", "Yahoo Finance & JustETF"),
            "metadata": meta,
        })
    return results


ETF_CATEGORY_OVERRIDES = {
    "CW8.PA": "Actions Internationales (MSCI World)",
    "PE500.PA": "Actions US (S&P 500 Large Cap)",
    "SPY": "Actions US (S&P 500 Large Cap)",
    "PUST.PA": "Actions US Tech (Nasdaq-100)",
    "QQQ": "Actions US Tech (Nasdaq-100)",
    "WPEA.PA": "Actions Internationales (MSCI World PEA)",
    "XEON.DE": "Monétaire (€STR / Trésorerie sans risque)",
    "ETZ.PA": "Actions Europe (Stoxx Europe 600)",
    "PAEEM.PA": "Actions Marchés Émergents",
    "TLT": "Obligations Souveraines US (20 ans+)",
    "AGG": "Obligations US Investment Grade",
    "GLD": "Matières Premières / Or Physique"
}

def search_similar_documents(
    query: str,
    k: int = 30,
    max_results: int = 12,
    min_cosine: float = MIN_COSINE_SIMILARITY
) -> List[Dict[str, Any]]:
    """
    Exécute une recherche par similarité cosinus dans pgvector, déduplique strictement par ticker
    et ne conserve que les documents dont la similarité cosinus réelle est >= min_cosine.
    """
    store = get_vectorstore()
    try:
        # On extrait un pool plus large pour permettre la déduplication de chunks d'un même actif
        results_with_score = store.similarity_search_with_score(query, k=k)
    except Exception as e:
        logger.warning(f"Recherche pgvector impossible (probablement vide) : {e}")
        return []
    
    seen_tickers = set()
    formatted_results = []

    for doc, distance in results_with_score:
        # pgvector renvoie la distance cosinus d = 1 - cos(θ) : la similarité réelle est 1 - d.
        # (L'ancienne formule 1 - d/2 = (1 + cos)/2 gonflait les scores : 55 % <=> cos = 0,10.)
        cosine_similarity = cosine_similarity_from_distance(distance)
        score_pct = round(cosine_similarity * 100, 1)

        # 1. Seuil de pertinence sur le cosinus réel : éliminer le bruit sémantique hors-sujet
        if cosine_similarity < min_cosine:
            continue

        meta = doc.metadata or {}
        raw_ticker = meta.get("ticker", "N/A").strip().upper()

        # 2. Déduplication stricte : un seul document représentatif par ticker
        if raw_ticker != "N/A" and raw_ticker in seen_tickers:
            continue
        if raw_ticker != "N/A":
            seen_tickers.add(raw_ticker)

        default_url = f"https://finance.yahoo.com/quote/{raw_ticker}" if raw_ticker != "N/A" else "https://finance.yahoo.com"
        source_url = meta.get("source_url") or default_url
        pdf_url = meta.get("pdf_url") or f"https://www.justetf.com/fr/search.html?search=ETFS&query={raw_ticker}"
        raw_cat = meta.get("category", meta.get("source_type", "Fonds"))
        category_clean = ETF_CATEGORY_OVERRIDES.get(raw_ticker) or raw_cat
        if category_clean in ["Actions / Thématique", "Actions / Thématiques"]:
            category_clean = "Actions Thématiques / Sectorielles"

        formatted_results.append({
            "ticker": raw_ticker,
            "name": meta.get("name", meta.get("title", "Document")),
            "category": category_clean,
            "content": doc.page_content,
            "distance": round(float(distance), 4),
            "similarity_score": cosine_similarity,
            "similarity_score_pct": score_pct,
            "source_url": source_url,
            "pdf_url": pdf_url,
            "provider": meta.get("provider", "Yahoo Finance & Référentiel Émetteur"),
            "metadata": meta
        })

        if len(formatted_results) >= max_results:
            break
        
    return formatted_results