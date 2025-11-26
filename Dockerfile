# On utilise une version légère de Python
FROM python:3.9-slim

# On se place dans le dossier de l'application
WORKDIR /app

# On copie tous vos fichiers dans l'image
COPY . .

# On installe les librairies nécessaires (Flask, etc.)
RUN pip install --no-cache-dir -r requirements.txt

# On ouvre le port 5000 (celui de votre serveur)
EXPOSE 5000

# La commande de démarrage (celle qui lançait votre site)
CMD ["python3", "api/index.py"]
