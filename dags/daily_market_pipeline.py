import logging
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

# Configuration des logs
logger = logging.getLogger(__name__)

# Définition des paramètres par défaut du DAG
default_args = {
    'owner': 'quant_engine',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Fonction de Simulation : Extraction de données
def fetch_daily_market_data(**context):
    """Simule la récupération des cours de clôture pour le jour courant."""
    execution_date = context['execution_date']
    logger.info(f"Extraction des données du marché pour la date : {execution_date}")
    # En production, appel à l'API Yahoo Finance / Bloomberg
    # On simule un payload extrait
    simulated_data = {"AAPL": 150.0, "MSFT": 310.0, "GOOGL": 2800.0}
    
    # On pousse les données dans XCom pour les passer à la tâche suivante
    context['ti'].xcom_push(key='market_data', value=simulated_data)
    return "Extraction terminée."

# Fonction de Simulation : Calcul de Covariance
def compute_covariance_matrix(**context):
    """Calcule (simule) la matrice de covariance nocturne."""
    # Récupération des données poussées par la tâche précédente
    ti = context['ti']
    market_data = ti.xcom_pull(task_ids='fetch_data', key='market_data')
    
    if not market_data:
        raise ValueError("Aucune donnée de marché récupérée. Arrêt du calcul.")

    logger.info(f"Calcul de la matrice de covariance basé sur : {market_data}")
    # En production, calcul lourd via Numpy/Polars
    simulated_matrix = [[0.04, 0.02, 0.01], [0.02, 0.05, 0.015], [0.01, 0.015, 0.06]]
    
    context['ti'].xcom_push(key='covariance_matrix', value=simulated_matrix)
    return "Calcul de covariance terminé."

# Fonction de Simulation : Insertion dans TimescaleDB
def store_in_timescaledb(**context):
    """Insère (simule) les résultats dans la base de données."""
    ti = context['ti']
    matrix = ti.xcom_pull(task_ids='compute_covariance', key='covariance_matrix')
    
    logger.info(f"Insertion des matrices dans TimescaleDB : {matrix}")
    # En production, connexion asyncpg et requête INSERT
    return "Insertion terminée avec succès."

# Définition du graphe (DAG)
with DAG(
    'daily_market_pipeline',
    default_args=default_args,
    description='Pipeline nocturne de pré-calcul des matrices de marché.',
    schedule_interval='0 3 * * *',  # Exécution tous les jours à 03:00 AM
    start_date=datetime(2023, 1, 1),
    catchup=False, # Ne pas rattraper les jours passés si le serveur était éteint
    tags=['quant', 'batch'],
) as dag:

    # Déclaration des opérateurs (Les "Nœuds" du graphe)
    task_fetch = PythonOperator(
        task_id='fetch_data',
        python_callable=fetch_daily_market_data,
        provide_context=True,
    )

    task_compute = PythonOperator(
        task_id='compute_covariance',
        python_callable=compute_covariance_matrix,
        provide_context=True,
    )

    task_store = PythonOperator(
        task_id='store_results',
        python_callable=store_in_timescaledb,
        provide_context=True,
    )

    # Définition de l'ordre d'exécution (Les "Flèches" du graphe)
    task_fetch >> task_compute >> task_store