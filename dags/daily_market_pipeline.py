import logging
import os
import requests
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

default_args = {
    'owner': 'quant_engine',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

API_HOST = os.getenv("API_HOST", "api")
API_PORT = os.getenv("API_PORT", "8000")
API_URL = f"http://{API_HOST}:{API_PORT}/api/v1"

def trigger_daily_market_pipeline(**context):
    """
    Déclenche le pipeline de marché nocturne via le service Copilot API.
    Collecte les cotations EOD (cours bruts et ajustés, taux de change) et met à jour PostgreSQL ;
    l'historique d'un actif est recalculé en entier lorsqu'un dividende ou un split l'a ré-ajusté.
    """
    endpoint = f"{API_URL}/pipeline/daily-market"
    logger.info(f"Démarrage de l'orchestration du pipeline marché : {endpoint}")
    
    try:
        response = requests.post(endpoint, timeout=300)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Succès du pipeline marché : {result.get('message')}")
        
        data = result.get("data", {})
        context['ti'].xcom_push(key='pipeline_result', value=data)
        return data
    except Exception as e:
        logger.error(f"Erreur critique lors de l'exécution du pipeline marché : {e}")
        raise

def verify_pipeline_health(**context):
    """Vérifie l'état de santé et la fraîcheur des données après exécution."""
    endpoint = f"{API_URL}/pipeline/status"
    try:
        response = requests.get(endpoint, timeout=30)
        response.raise_for_status()
        status = response.json()
        logger.info(f"Statut global des pipelines : {status}")
        return status
    except Exception as e:
        logger.warning(f"Avertissement vérification statut : {e}")
        return {"warning": str(e)}

with DAG(
    'daily_market_pipeline',
    default_args=default_args,
    description='Pipeline nocturne automatisé de collecte des cours et des taux de change.',
    schedule_interval='0 3 * * 1-5',  # Du lundi au vendredi à 03:00 AM UTC
    start_date=datetime(2023, 1, 1),
    catchup=False,
    tags=['quant', 'production', 'market-data', 'automated'],
) as dag:

    task_fetch = PythonOperator(
        task_id='fetch_and_update_market_data',
        python_callable=trigger_daily_market_pipeline,
        provide_context=True,
    )

    task_verify = PythonOperator(
        task_id='verify_pipeline_health',
        python_callable=verify_pipeline_health,
        provide_context=True,
    )

    task_fetch >> task_verify