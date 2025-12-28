FROM python:3.9-slim

WORKDIR /app

# Installation des dépendances système nécessaires (gcc pour la compilation de certains packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Copie et installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du code
COPY . .

# On expose le port 8000
EXPOSE 8000

# COMMANDE DE DÉMARRAGE : Lance api.index (site web + api) au lieu de main.py
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]