# Utilisation d'une image Python légère
FROM python:3.9-slim

# Installation des dépendances système nécessaires pour certaines libs (comme lxml ou audio)
RUN apt-get update && apt-get install -y \
    gcc \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Dossier de travail
WORKDIR /app

# Copie des requirements et installation
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du projet
COPY . .

# Exposition du port
EXPOSE 5000

# Lancement du serveur via le script server.py (qui lance Uvicorn)
CMD ["python", "server.py"]