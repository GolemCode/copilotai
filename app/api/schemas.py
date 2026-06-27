from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class AssetItem(BaseModel):
    ticker: str
    shares: float

class PortfolioSimulationRequest(BaseModel):
    client_id: str
    assets: List[AssetItem]
    time_horizon_days: int
    num_paths: int
    strategy: Optional[Dict[str, Any]] = None

class TaskSubmissionResponse(BaseModel):
    status: str
    task_id: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
class PortfolioSimulationResponse(BaseModel):
    value_at_risk_95: Optional[float] = None
    expected_shortfall_95: Optional[float] = None
    histogram: Optional[List[Dict[str, Any]]] = None