import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import Agent
from .observability import ProcessMetrics
from .sandbox import SandboxError, Workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", str(REPO_ROOT / "workspace")))
workspace = Workspace(WORKSPACE_ROOT)
app = FastAPI(title="Sandboxed Tool Agent", version="0.2.0")
_metrics = ProcessMetrics()
_METRICS_EXCLUDED = {"/", "/health", "/ready", "/metrics"}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["list", "read"]
    args: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_arguments(self):
        if set(self.args) - {"path"}:
            raise ValueError("only the path argument is allowed")
        path = self.args.get("path", ".")
        if not path or len(path) > Workspace.MAX_PATH_CHARS:
            raise ValueError("path is empty or too long")
        if self.tool == "read" and "path" not in self.args:
            raise ValueError("read requires args.path")
        return self


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[Step] = Field(min_length=1, max_length=4)


@app.middleware("http")
async def collect_request_metrics(request, call_next):
    if request.url.path in _METRICS_EXCLUDED:
        return await call_next(request)
    started = _metrics.start()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        _metrics.finish(f"{request.method} {request.url.path}", status_code, started)


@app.exception_handler(SandboxError)
async def handle_sandbox_error(request, exc: SandboxError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sandboxed Tool Agent</title><style>
body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;color:#18212b;background:#f5f7fa}
main{background:white;border:1px solid #d9e0e8;border-radius:14px;padding:1.25rem;margin:1rem 0}
input,select,button{font:inherit;padding:.65rem;box-sizing:border-box;width:100%;margin:.35rem 0}
button{cursor:pointer;background:#18212b;color:white;border:0;border-radius:8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f7;padding:1rem;border-radius:8px}
small{color:#526070}
</style></head><body><h1>Sandboxed Tool Agent</h1>
<p>Run bounded, read-only file tools against a dedicated demo workspace. This executor does not call an LLM and is not an OS/container sandbox.</p>
<main><h2>Run one tool</h2><label for="tool">Tool</label><select id="tool"><option value="list">List directory</option><option value="read">Read file</option></select>
<label for="path">Workspace-relative path</label><input id="path" value="demo.txt" maxlength="240">
<button onclick="runTool()">Run</button><pre id="result">No tool call yet.</pre></main>
<main><h2>Inspect behavior</h2><p><a href="/docs">API documentation</a> · <a href="/metrics">Process metrics</a> · <a href="/health">Liveness</a> · <a href="/ready">Readiness</a></p>
<small>Reads are capped at 100 KB; directory listings are capped at 200 entries; each request allows at most four steps. Hidden paths and workspace escapes are rejected.</small></main>
<script>
async function runTool(){
 const output=document.getElementById('result'); output.textContent='Running...';
 const tool=document.getElementById('tool').value; const path=document.getElementById('path').value;
 const step={tool:tool,args:{path:path}};
 try{
  const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({steps:[step]})});
  const data=await r.json(); output.textContent=JSON.stringify({status:r.status,response:data},null,2);
 }catch(e){output.textContent='Request failed: '+e.name;}
}
</script></body></html>"""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/ready")
def ready(response: Response):
    if not workspace.root.is_dir():
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready", "workspace": "dedicated demo workspace"}


@app.get("/metrics")
def metrics():
    return _metrics.snapshot()


@app.post("/run")
def run(body: Run):
    return Agent(workspace).run([step.model_dump() for step in body.steps])
