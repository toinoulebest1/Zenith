# Python 3.13 : les versions récentes de ytmusicapi (radio, paroles YouTube) exigent Python 3.10+.
# En 3.9, pip installait une vieille version qui ne fonctionne plus avec YouTube Music.
FROM python:3.13-slim

WORKDIR /app

# Dépendances système : ffmpeg seulement (source Amazon / squid.wtf, en pause).
# Pas de gcc : toutes les dépendances Python existent en version précompilée pour Python 3.13,
# et gcc doublait la taille de cette étape (risque de build interrompu sur un petit serveur).
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ffmpeg \
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