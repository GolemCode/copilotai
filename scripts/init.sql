-- Création des bases de données distinctes
CREATE DATABASE copilot_db;
CREATE DATABASE airflow_db;

-- Optionnel : Création d'utilisateurs dédiés pour le cloisonnement des privilèges
-- Cela renforce la sécurité et évite les accès croisés accidentels
CREATE USER airflow_user WITH PASSWORD 'airflow_password';
GRANT ALL PRIVILEGES ON DATABASE airflow_db TO airflow_user;

CREATE USER app_user WITH PASSWORD 'app_password';
GRANT ALL PRIVILEGES ON DATABASE copilot_db TO app_user;