from fastapi import FastAPI

app = FastAPI(title="Tickipate", description="Research, not investment advice.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
