# Utiliser une image Python légère
FROM python:3.10-slim

# Définir le dossier de travail
WORKDIR /app

# Installation des dépendances système nécessaires (gcc pour la compilation de certaines libs)
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Copie des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY . .

# Variables d'environnement
ENV PORT=8000
ENV FLASK_APP=api/index.py
ENV PYTHONPATH=/app/api

# Exposition du port
EXPOSE 8000

# Commande de démarrage avec Gunicorn (Serveur de production WSGI)
# On pointe vers l'objet 'app' dans le fichier api/index.py
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "api.index:app"]