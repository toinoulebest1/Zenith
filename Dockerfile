# Utiliser une image Python légère
FROM python:3.10-slim

# Définir le dossier de travail
WORKDIR /app

# Installation des dépendances système
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Copie des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY . .

# Configuration de l'environnement
# PORT 80 est le standard pour le web
ENV PORT=80
ENV FLASK_APP=api/index.py
ENV PYTHONPATH=/app/api

# Exposition du port 80
EXPOSE 80

# Commande de démarrage sur le port 80
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:80", "api.index:app"]