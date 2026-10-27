FROM python:3.12-slim

# uv, version épinglée (même version que celle qui a généré uv.lock)
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

# L'environnement virtuel est créé HORS de /app : docker-compose monte le code local sur /app,
# ce qui masquerait un .venv placé dans le projet.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dépendances d'abord (couche mise en cache tant que pyproject.toml / uv.lock ne changent pas)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY . .

EXPOSE 8000
