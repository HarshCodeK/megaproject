import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Agent
from .sandbox import SandboxError, Workspace

app = FastAPI(title="Read-only Workspace Tool Agent")


class Step(BaseModel):
    tool: str = Field(min_length=1, max_length=20)
    args: dict = Field(default_factory=dict)


class Run(BaseModel):
    steps: list[Step] = Field(max_length=4)


def workspace() -> Workspace:
    return Workspace(os.getenv("WORKSPACE_DIR", "workspace"))


@app.get("/health")
def health():
    return {"ok": True, "mode": "read_only_tools"}


@app.get("/ready")
def ready():
    return {"ready": True, "workspace": os.getenv("WORKSPACE_DIR", "workspace")}


@app.post("/run")
def run(body: Run):
    try:
        return Agent(workspace()).run([step.model_dump() for step in body.steps])
    except (SandboxError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
