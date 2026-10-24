import sys; sys.path.insert(0, ".")
# BUG-3: ai.py resolves model then discards it
from app.routers.ai import DEFAULT_MODELS
print("DEFAULT_MODELS:", DEFAULT_MODELS)
# simulate the endpoint logic
for req_model, provider in [(None, "gemini"), (None, "meta"), (None, "ollama")]:
    model = req_model or DEFAULT_MODELS.get(provider, "")
    print(f"provider={provider:7} resolved model={model!r}  but AIOptions(model=req.model)={req_model!r}  -> provider fallback used instead")

# investigate summary inconsistency
from app.routers.investigate import run_investigation
import inspect
src = inspect.getsource(run_investigation)
print("\nsummary strings found:")
for line in src.splitlines():
    if "Không phát hiện" in line:
        print("  ", line.strip())
