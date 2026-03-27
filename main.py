from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

API_KEY = "stoklyn-secret-key"

@app.post("/generate-schedule")
def generate_schedule(data: dict, x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {
        "generated_assignments": [],
        "shortages": [],
        "conflicts": [],
        "summary": {
            "message": "API works"
        }
    }
