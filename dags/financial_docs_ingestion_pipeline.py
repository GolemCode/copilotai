import logging
import os
import requests
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

default_args = {
    'owner': 'rag_engine',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=10),
}

API_HOST = os.getenv("API_HOST", "api")
API_PORT = os.getenv("API_PORT", "8000")
API_URL = f"http://{API_HOST}:{API_PORT}/api/v1"

def trigger_rag_ingestion_pipeline(**context):
    """
    Déclenche le pipeline documentaire via le service Copilot API.
    Scrape les fiches ETF (Yahoo Finance + JustETF), les enregistre en base (fund_profiles,
    fund_holdings, fund_exposures), puis vectorise ces données dans pgvector pour le RAG.
    """
    endpoint = f"{API_URL}/pipeline/rag-ingestion"
    logger.info(f"Démarrage de l'orchestration du pipeline RAG : {endpoint}")
    
    try:
        response = requests.post(endpoint, timeout=600)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Succès de l'ingestion RAG : {result.get('message')}")
        
        data = result.get("data", {})
        context['ti'].xcom_push(key='rag_result', value=data)
        return data
    except Exception as e:
        logger.error(f"Erreur critique lors de l'ingestion RAG : {e}")
        raise

def verify_rag_pipeline_status(**context):
    """Vérifie l'état et le nombre d'embeddings vectoriels dans pgvector."""
    endpoint = f"{API_URL}/pipeline/status"
    try:
        response = requests.get(endpoint, timeout=30)
        response.raise_for_status()
        status = response.json()
        logger.info(f"Statut global des pipelines et pgvector : {status}")
        return status
    except Exception as e:
        logger.warning(f"Avertissement vérification statut RAG : {e}")
        return {"warning": str(e)}

with DAG(
    'financial_docs_ingestion_pipeline',
    default_args=default_args,
    description='Pipeline hebdomadaire automatisé d\'ingestion documentaire et vectorisation RAG dans pgvector.',
    schedule_interval='0 4 * * 1',  # Chaque lundi à 04:00 AM UTC
    start_date=datetime(2023, 1, 1),
    catchup=False,
    tags=['rag', 'pgvector', 'embeddings', 'nlp', 'automated'],
) as dag:

    task_ingest = PythonOperator(
        task_id='ingest_and_vectorize_documents',
        python_callable=trigger_rag_ingestion_pipeline,
        provide_context=True,
    )

    task_verify = PythonOperator(
        task_id='verify_rag_status',
        python_callable=verify_rag_pipeline_status,
        provide_context=True,
    )

    task_ingest >> task_verify
