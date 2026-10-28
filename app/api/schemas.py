from datetime import date as Date
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

class AssetItem(BaseModel):
    ticker: str
    shares: float = Field(default=1.0, ge=0.0001, description="Nombre de parts détenues")

class PortfolioSimulationRequest(BaseModel):
    client_id: str
    assets: List[AssetItem]
    time_horizon_days: int = Field(default=252, ge=10, le=1260)
    num_paths: int = Field(default=10000, ge=100, le=50000)
    strategy: Optional[Dict[str, Any]] = None
    # Graine optionnelle : même graine + mêmes données => mêmes trajectoires (reproductibilité)
    seed: Optional[int] = Field(default=None, ge=0)

class ProjectionAsset(BaseModel):
    ticker: str
    weight: float = Field(..., gt=0, description="Poids dans l'allocation (normalisé côté serveur)")

class SavingsProjectionRequest(BaseModel):
    """Plan d'épargne : mise de départ, versement mensuel et horizon, investis selon une allocation."""
    assets: List[ProjectionAsset] = Field(..., min_length=1)
    initial_amount: float = Field(default=0.0, ge=0, le=10_000_000)
    monthly_contribution: float = Field(default=0.0, ge=0, le=1_000_000)
    years: int = Field(default=10, ge=1, le=40)
    num_paths: int = Field(default=5000, ge=500, le=20000)
    seed: Optional[int] = Field(default=None, ge=0)

class TaskSubmissionResponse(BaseModel):
    status: str
    task_id: str

class RagQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="La question posée à l'assistant financier")
    portfolio_context: Optional[Dict[str, Any]] = None
    chat_history: Optional[List[Dict[str, str]]] = None

class RagSourceItem(BaseModel):
    ticker: str
    name: str
    category: str
    # None pour une source directe (récupérée par ticker exact, sans score de similarité)
    similarity_score_pct: Optional[float] = None
    match_type: str = "semantic"
    snippet: str
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None
    provider: Optional[str] = None

class RagQueryResponse(BaseModel):
    answer: str
    sources: List[RagSourceItem] = []
    latency_ms: Optional[float] = None
    suggested_questions: Optional[List[str]] = []
    # Chiffres cités par le LLM mais absents du contexte / des données du portefeuille
    unverified_numbers: Optional[List[str]] = []

class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    k: int = Field(default=4, ge=1, le=20)

class DocumentIngestItem(BaseModel):
    content: str = Field(..., min_length=10)
    title: Optional[str] = "Document Financier"
    ticker: Optional[str] = "GENERAL"
    metadata: Optional[Dict[str, Any]] = None

class DocumentIngestRequest(BaseModel):
    documents: List[DocumentIngestItem]

class PortfolioMetricsRequest(BaseModel):
    assets: List[AssetItem]
    # None : taux sans risque déduit de l'€STR (XEON.DE, 12 mois glissants)
    risk_free_rate: Optional[float] = Field(default=None, ge=-0.05, le=0.20)
    benchmark_ticker: Optional[str] = "SPY"
    years: Optional[int] = Field(default=3, ge=1, le=10)

class CountryExposureItem(BaseModel):
    name: str
    weight_pct: float

class AssetGeoExposureResponse(BaseModel):
    ticker: str
    countries: List[CountryExposureItem] = []
    regions: Dict[str, float] = {}
    source: str = "unknown"
    updated_at: Optional[str] = None

class PortfolioGeoExposureRequest(BaseModel):
    tickers: List[str] = Field(..., min_length=1)

class PortfolioGeoExposureResponse(BaseModel):
    assets: Dict[str, AssetGeoExposureResponse]

class TransactionCreateRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    date: Date
    type: Literal["buy", "sell"] = "buy"
    shares: float = Field(..., gt=0, description="Quantité achetée ou vendue")
    # Prix unitaire payé en EUR. Absent : cours de clôture du jour (actifs cotés uniquement)
    price: Optional[float] = Field(default=None, ge=0)

class StressScenarioRequest(BaseModel):
    assets: List[AssetItem] = Field(..., min_length=1)
    years: int = Field(default=3, ge=1, le=10)
    # Indice de marché de référence pour le bêta et le scénario "pire mois du marché" (MSCI World en EUR)
    market_ticker: str = "CW8.PA"

class PipelineScrapeRequest(BaseModel):
    tickers: Optional[List[str]] = None
    # True : re-télécharge l'historique complet (à lancer une fois après migration du stockage)
    full_refresh: bool = False
