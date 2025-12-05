import uvicorn
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur FastAPI (Uvicorn) lancé sur le port {port}")
    # On pointe vers le module api.index et l'objet app
    # reload=True permet le rechargement à chaud
    uvicorn.run("api.index:app", host="0.0.0.0", port=port, reload=True)