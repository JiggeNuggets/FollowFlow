from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "API is running"}

@app.get("/api/health")
def health():
    return {"status": "ok"}
