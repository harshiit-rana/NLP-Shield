import os
import logging
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from pipeline_service import run_pipeline, run_depseudonymize
from src.pseudonymiser.store import MappingStore
from src.pseudonymiser.config import StoreConfig

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NLP Shield — Privacy-Preserving AI Workspace",
    description="API for detecting, pseudonymising, and restoring PII in documents.",
    version="1.4.0"
)

# 1. Health Probe
@app.get("/health")
async def health():
    return {"status": "ok", "app": "NLP Shield", "version": "1.4.0"}

# 2. Serve the VaultDoc Web Application
VAULTDOC_HTML = os.path.join(os.path.dirname(__file__), "vaultdoc", "index.html")

@app.get("/")
async def serve_ui():
    if os.path.exists(VAULTDOC_HTML):
        return FileResponse(VAULTDOC_HTML)
    return JSONResponse(status_code=404, content={"detail": "VaultDoc frontend not found"})

# 3. Document Pseudonymisation
@app.post("/pseudorun")
async def pseudorun(file: UploadFile = File(...)):
    """
    Upload a PDF or image file.
    The pipeline extracts text via OCR, detects PII,
    and performs deterministic right-to-left pseudonymisation using the MappingStore.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
        
    ext = file.filename.split('.')[-1].lower()
    valid_exts = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp", "txt", "docx"}
    if ext not in valid_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Must be one of: {valid_exts}")
        
    try:
        content = await file.read()
        logger.info(f"Processing uploaded file: {file.filename} ({len(content)} bytes)")
        
        result = run_pipeline(content, file.filename)
        return JSONResponse(status_code=200, content=result)
        
    except Exception as e:
        logger.error(f"Failed to process {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# 4. De-pseudonymisation (Supports JSON, Multipart with custom CSV, or selected key bundle)
@app.post("/depseudorun")
async def depseudorun(request: Request):
    """
    Restores the original PII from pseudonymised text using database, selected bundle, or uploaded CSV.
    Accepts:
      - JSON: {"text": "...", "bundle_id": "1-keys.csv"}
      - Form: text="..." and optional keys_file=keys.csv or bundle_id="..."
    """
    try:
        content_type = request.headers.get("content-type", "")
        text = ""
        csv_content = None
        bundle_id = None

        if "multipart/form-data" in content_type:
            form = await request.form()
            text = form.get("text", "")
            bundle_id = form.get("bundle_id")
            keys_file = form.get("keys_file")
            if keys_file and hasattr(keys_file, "read"):
                file_bytes = await keys_file.read()
                csv_content = file_bytes.decode("utf-8", errors="ignore")
        else:
            body = await request.json()
            text = body.get("text", "")
            bundle_id = body.get("bundle_id")

        if not text:
            raise HTTPException(status_code=400, detail="No text provided")

        source_label = "Vault Database"
        # If a specific bundle file was selected
        if not csv_content and bundle_id and bundle_id != "vault":
            bundle_path = os.path.join(os.path.dirname(__file__), "outputs", bundle_id)
            if os.path.exists(bundle_path):
                with open(bundle_path, "r", encoding="utf-8") as f:
                    csv_content = f.read()
                source_label = f"Bundle: {bundle_id}"

        if csv_content and not bundle_id:
            source_label = "Custom CSV"

        result = run_depseudonymize(text, csv_content=csv_content)
        
        # Return both keys for VaultDoc UI and n8n compatibility
        return JSONResponse(status_code=200, content={
            "restored_text": result["restored_text"],
            "text": result["restored_text"],
            "resolved": result.get("resolved", []),
            "source_label": source_label
        })
        
    except Exception as e:
        logger.error(f"Failed to depseudonymize text: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# 4b. List Available Key Bundles (from outputs/ and active DB)
@app.get("/api/bundles")
async def get_bundles():
    """Returns all available key bundles from the outputs folder and active database."""
    bundles = [
        {
            "id": "vault",
            "name": "Active Vault Session (All Stored Keys)",
            "filename": "Active Vault Session",
            "keys_count": 0
        }
    ]
    outputs_dir = os.path.join(os.path.dirname(__file__), "outputs")
    if os.path.exists(outputs_dir):
        for fname in sorted(os.listdir(outputs_dir)):
            if fname.endswith("-keys.csv"):
                fpath = os.path.join(outputs_dir, fname)
                count = 0
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        count = max(0, sum(1 for line in f if line.strip()) - 1)
                except Exception:
                    pass
                doc_name = fname.replace("-keys.csv", "")
                bundles.append({
                    "id": fname,
                    "name": f"{doc_name} ({count} keys)",
                    "filename": fname,
                    "keys_count": count
                })
    return JSONResponse(content={"bundles": bundles})

# 5. Recent Sessions / Vault Mappings
@app.get("/api/sessions")
async def get_sessions():
    """Returns recent mappings stored in the vault database."""
    try:
        store_cfg = StoreConfig(db_path="pseudonymiser.db")
        with MappingStore(store_cfg) as store:
            mappings = store.all_mappings(scope="corpus")
            keys = [
                {"token": p, "category": t, "value": r, "occurrences": 1}
                for t, p, r in mappings
            ]
            if keys:
                return JSONResponse(content=[{
                    "id": "current-vault",
                    "document": "Active Vault Session",
                    "entities": len(keys),
                    "created_at": "Active",
                    "keys": keys
                }])
            return JSONResponse(content=[])
    except Exception as e:
        logger.warning(f"Could not load sessions: {e}")
        return JSONResponse(content=[])

DEFAULT_SYSTEM_PROMPT = (
    "You are NLP Shield AI, an expert analytical assistant for privacy-preserved, confidential documents.\n\n"
    "CRITICAL CONTEXT & INSTRUCTIONS:\n"
    "1. DOCUMENT PSEUDONYMISATION:\n"
    "   The provided document text is intentionally pseudonymised to protect privacy. Real-world sensitive entities (such as people, organisations, locations, dates, monetary amounts, and IDs) have been replaced with privacy tokens (e.g., PERSON_001, ORGANIZATION_001, LOCATION_001, DATE_001, MONEY_001, ID_001).\n\n"
    "2. TOKEN PRESERVATION (MANDATORY):\n"
    "   Always preserve and use these exact privacy tokens (e.g., PERSON_001, ORGANIZATION_001, LOCATION_003) in your answers, analysis, and citations. Do NOT modify the token syntax, invent fictional names, or guess the masked identities. Preserving the exact token codes allows the user's secure local vault to de-pseudonymise and restore real identities seamlessly.\n\n"
    "3. CONVERSATIONAL & HELPFUL ATTITUDE:\n"
    "   Be cordial, natural, and helpful. Always respond politely to greetings (e.g., 'hello', 'hi'), acknowledgments, and general questions. Introduce your capabilities and invite the user to ask about specific clauses, parties, obligations, or terms.\n\n"
    "4. IN-DEPTH ANALYSIS & STRUCTURE:\n"
    "   When asked about the document, provide comprehensive, detailed analytics: summarize obligations, identify rights and liabilities, point out dates and financial terms, and cite specific sections or clauses (e.g., §3.1, Clause 4). Format your answers with clear bullet points and bold headers for clarity."
)

@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Proxies chat requests to Groq, automatically falling back across active models and optionally restoring identities in the response."""
    try:
        body = await request.json()
        message = body.get("message", "")
        context = body.get("context", "")
        api_key = body.get("api_key") or os.environ.get("GROQ_API_KEY", "")
        requested_model = body.get("model") or "openai/gpt-oss-120b"
        temperature = float(body.get("temperature", 0.2))
        max_tokens = int(body.get("max_tokens", 3072))
        
        system_input = (body.get("system") or "").strip()
        legacy_defaults = [
            "Answer only from the supplied redacted context. Never speculate about the meaning of privacy tokens.",
            "Answer only from the supplied redacted context and cite section numbers. Never speculate about the meaning of privacy tokens."
        ]
        if not system_input or any(legacy in system_input for legacy in legacy_defaults):
            system_prompt = DEFAULT_SYSTEM_PROMPT
        else:
            system_prompt = system_input
            
        history = body.get("history", [])
        auto_restore = body.get("auto_restore", False)
        
        if not api_key:
            return JSONResponse(status_code=400, content={
                "detail": "Groq API key required. Please enter it in Advanced Settings."
            })
            
        if context:
            sys_content = (
                f"{system_prompt}\n\n"
                f"--- REDACTED DOCUMENT CONTEXT (Privacy tokens like PERSON_001, ORGANIZATION_001 are intentional) ---\n"
                f"{context}\n"
                f"--- END DOCUMENT CONTEXT ---"
            )
        else:
            sys_content = system_prompt

        messages = [{"role": "system", "content": sys_content}]
        for h in history:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": message})
        
        # Candidate chain for fallback: primary requested model -> current public models
        candidate_models = [
            requested_model,
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b"
        ]
        seen = set()
        models_to_try = [m for m in candidate_models if m and not (m in seen or seen.add(m))]
        
        reply = None
        successful_model = None
        last_error_resp = None

        async with httpx.AsyncClient(timeout=45.0) as client:
            for model_attempt in models_to_try:
                logger.info(f"Attempting Groq chat with model: {model_attempt}")
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_attempt,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    successful_model = model_attempt
                    break
                elif resp.status_code in (400, 404):
                    logger.warning(f"Groq model '{model_attempt}' returned HTTP {resp.status_code}: {resp.text[:120]}. Trying fallback...")
                    last_error_resp = resp
                    continue
                else:
                    return JSONResponse(status_code=resp.status_code, content={"detail": f"Groq Error: {resp.text}"})

            if not reply:
                # Query available models to help the user diagnose account access
                account_models = []
                try:
                    probe = await client.get(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"}
                    )
                    if probe.status_code == 200:
                        account_models = [m["id"] for m in probe.json().get("data", [])]
                except Exception:
                    pass
                
                err_text = last_error_resp.text if last_error_resp else "Unknown error"
                extra = f" (Your account has access to: {', '.join(account_models[:6])})" if account_models else ""
                return JSONResponse(
                    status_code=last_error_resp.status_code if last_error_resp else 500,
                    content={"detail": f"Groq Error: {err_text}{extra}"}
                )

            if auto_restore:
                restored_res = run_depseudonymize(reply)
                reply = restored_res["restored_text"]
                
            return JSONResponse(content={"reply": reply, "model_used": successful_model})
            
    except Exception as e:
        logger.error(f"Chat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# 7. Groq Model Discovery
@app.post("/api/groq-models")
async def groq_models(request: Request):
    """Returns available models for the user's Groq API key."""
    try:
        body = await request.json()
        api_key = body.get("api_key") or os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            return JSONResponse(status_code=400, content={"detail": "Groq API key required"})
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"detail": resp.text})
            
            data = resp.json().get("data", [])
            chat_models = [m["id"] for m in data if not m["id"].startswith("whisper")]
            return JSONResponse(content={"models": chat_models})
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
