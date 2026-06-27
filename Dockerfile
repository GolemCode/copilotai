# Utilisation d'une image Python officielle, version allégée
FROM python:3.12-slim

# Définition du répertoire de travail dans le conteneur
WORKDIR /app

# Installation de Poetry
RUN pip install --no-cache-dir poetry

# Copie exclusive des fichiers de dépendances pour optimiser le cache Docker
COPY pyproject.toml poetry.lock ./

# Configuration de Poetry pour installer les paquets globalement dans le conteneur
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Copie du reste du code source
COPY . .

# Le port sur lequel Uvicorn écoutera
EXPOSE 8000