import os
import json
import numpy as np
import polars as pl
import redis
from app.worker.celery_app import celery_app
from app.quant.monte_carlo import generate_monte_carlo_simulation

# Connexion synchrone pour le worker vers l'instance Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

@celery_app.task(name="compute_risk_metrics")
def compute_risk_metrics_task(
    client_id: str,
    arrow_filepath: str,
    num_paths: int,
    time_horizon_days: int,
    initial_investment: float,
    strategy: dict = None,
    seed: int = None
) -> dict:
    """Tâche Celery pour la simulation stochastique de Monte Carlo."""
    try:
        if not os.path.exists(arrow_filepath):
            raise FileNotFoundError(f"Le fichier IPC spécifié est introuvable : {arrow_filepath}")

        shared_df = pl.read_ipc(arrow_filepath, memory_map=True)

        expected_returns = shared_df["expected_return"].drop_nulls().to_numpy()
        weights = shared_df["weight"].drop_nulls().to_numpy()

        cov_columns = [c for c in shared_df.columns if c.startswith("cov_")]
        covariance_matrix = shared_df.select(cov_columns).to_numpy()

        try:
            os.remove(arrow_filepath)
        except OSError:
            pass

        results = generate_monte_carlo_simulation(
            expected_returns=expected_returns,
            covariance_matrix=covariance_matrix,
            weights=weights,
            num_paths=num_paths,
            time_horizon_days=time_horizon_days,
            initial_investment=initial_investment,
            strategy=strategy,
            seed=seed
        )

        # Bouclier anti-crash RAM / payload
        payload_data = {}
        for key, value in results.items():
            if isinstance(value, (int, float, str, bool, dict)):
                payload_data[key] = value
                continue
            
            if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                if "day" in value[0] and "median" in value[0]:
                    clean_list = []
                    for row in value:
                        clean_row = {
                            "day": row.get("day"),
                            "p1": row.get("p1"),
                            "median": row.get("median"),
                            "p99": row.get("p99"),
                            "abs_p1": row.get("abs_p1"),
                            "abs_median": row.get("abs_median"),
                            "abs_p99": row.get("abs_p99"),
                            "inv_p1": row.get("inv_p1"),
                            "inv_median": row.get("inv_median"),
                            "inv_p99": row.get("inv_p99")
                        }
                        for i in range(20):
                            path_key = f"path_{i}"
                            if path_key in row:
                                clean_row[path_key] = row[path_key]
                        clean_list.append(clean_row)
                    payload_data[key] = clean_list
                else:
                    payload_data[key] = value
                continue

            if isinstance(value, list) and len(value) == 0:
                payload_data[key] = value
                continue

            try:
                arr = np.array(value)
                if arr.size <= 10000:
                    payload_data[key] = value
            except Exception:
                pass

        channel_name = f"ws_channel_{client_id}"
        payload = {
            "status": "success",
            "type": "monte_carlo",
            "data": payload_data
        }
        redis_client.publish(channel_name, json.dumps(payload))

        return {"status": "completed", "client_id": client_id}

    except Exception as e:
        channel_name = f"ws_channel_{client_id}"
        error_payload = {
            "status": "error",
            "message": f"Erreur lors du traitement asynchrone : {str(e)}"
        }
        try:
            redis_client.publish(channel_name, json.dumps(error_payload))
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}
