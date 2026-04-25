import logging
import json
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from chatkit.server import StreamingResult
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import run_health_insurance_agent
from app.chatkit_backend import chatkit_server
from app.chatkit_store import ChatKitRequestContext
from app.config import get_settings
from app.database import engine, get_db, init_db
from app.repository import create_lead, create_manual_lead
from app.schemas import (
    ChatAgentOutput,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    Intent,
    LeadCreate,
    LeadFields,
    LeadRead,
    LeadStatus,
    SafetyStatus,
)
from app.safety import (
    SENSITIVE_RESPONSE,
    detect_sensitive_input,
    has_contact_details,
    sanitize_lead_fields,
)

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("insurance_chatbot")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    logger.info("application_started")
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("application_stopped")


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = str(uuid4())
    start = time.perf_counter()

    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id

    logger.info(
        "request_completed request_id=%s method=%s path=%s status=%s elapsed_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    logger.exception(
        "unhandled_error request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": request_id},
    )


@app.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    await db.execute(text("SELECT 1"))
    return HealthResponse(
        status="ok",
        database="ok",
        openai_configured=bool(settings.openai_api_key),
        vector_store_configured=bool(settings.openai_vector_store_id),
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def chatkit_ui() -> HTMLResponse:
    return HTMLResponse(_chatkit_html())


@app.post("/chatkit", include_in_schema=False)
async def chatkit_endpoint(request: Request) -> Response:
    user_id = request.headers.get("X-User-ID")
    if not user_id and request.client:
        user_id = request.client.host
    context = ChatKitRequestContext(user_id=user_id or "anonymous")
    result = await chatkit_server.process(await request.body(), context)
    if isinstance(result, StreamingResult):
        return StreamingResponse(result, media_type="text/event-stream")
    return Response(content=result.json, media_type="application/json")


@app.post("/lead", response_model=LeadRead, status_code=status.HTTP_201_CREATED)
async def submit_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
) -> LeadRead:
    safety = detect_sensitive_input(payload.model_dump_json(exclude_none=True))
    if safety.blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sensitive identifiers or documents are not accepted in lead submissions.",
        )

    lead = await create_manual_lead(db, payload)
    logger.info("lead_created source=%s lead_id=%s", payload.source, lead.id)
    return LeadRead.model_validate(lead)


@app.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    safety = detect_sensitive_input(payload.message)
    if safety.blocked:
        logger.info(
            "chat_blocked_sensitive_input conversation_id=%s flags=%s",
            payload.conversation_id,
            ",".join(safety.flags),
        )
        return ChatResponse(
            reply=SENSITIVE_RESPONSE,
            classification=Intent.unsafe_sensitive,
            extracted_fields=LeadFields(),
            lead=LeadStatus(saved=False, reason="Sensitive input was blocked."),
            safety=SafetyStatus(blocked=True, warnings=safety.flags),
        )

    try:
        agent_output = await run_health_insurance_agent(
            message=payload.message,
            history=payload.history,
            conversation_id=payload.conversation_id,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "agent_run_failed conversation_id=%s",
            payload.conversation_id,
            exc_info=exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="HealthInsuranceAgent failed to produce a response.",
        ) from exc

    return await _build_chat_response(db, payload, agent_output)


async def _build_chat_response(
    db: AsyncSession,
    payload: ChatRequest,
    agent_output: ChatAgentOutput,
) -> ChatResponse:
    safe_fields = sanitize_lead_fields(agent_output.lead_fields)
    safety_warnings = list(agent_output.safety_flags)
    lead_status = LeadStatus(
        saved=False,
        missing_fields=agent_output.missing_lead_fields,
    )

    if _should_save_lead(agent_output, safe_fields):
        lead = await create_lead(
            db,
            fields=safe_fields,
            source="chat",
            intent=agent_output.intent.value,
            conversation_id=payload.conversation_id,
        )
        lead_status = LeadStatus(
            saved=True,
            id=lead.id,
            missing_fields=agent_output.missing_lead_fields,
        )
        logger.info(
            "lead_created source=chat lead_id=%s conversation_id=%s intent=%s",
            lead.id,
            payload.conversation_id,
            agent_output.intent.value,
        )
    elif agent_output.should_create_lead:
        lead_status.reason = "Contact details are required before a lead can be saved."

    return ChatResponse(
        reply=agent_output.answer,
        classification=agent_output.intent,
        extracted_fields=safe_fields,
        lead=lead_status,
        safety=SafetyStatus(
            blocked=agent_output.intent == Intent.unsafe_sensitive,
            warnings=safety_warnings,
        ),
        needs_human_callback=agent_output.needs_human_callback,
    )


def _should_save_lead(agent_output: ChatAgentOutput, fields: LeadFields) -> bool:
    return (
        agent_output.should_create_lead
        and has_contact_details(fields)
        and agent_output.intent not in {Intent.unrelated, Intent.unsafe_sensitive}
    )


def _chatkit_html() -> str:
    options = {
        "api": {
            "url": "/chatkit",
            "domainKey": settings.chatkit_domain_key,
        },
        "theme": {
            "colorScheme": "light",
            "radius": "soft",
            "density": "normal",
        },
        "header": {
            "title": {
                "text": "Health Insurance Assistant",
            },
        },
        "startScreen": {
            "greeting": "How can I help with health insurance today?",
            "prompts": [
                {
                    "label": "Compare plans",
                    "prompt": "Compare Care Supreme and ReAssure for a family plan.",
                },
                {
                    "label": "Waiting periods",
                    "prompt": "What are the waiting periods in Care Supreme?",
                },
                {
                    "label": "Claim help",
                    "prompt": "How does the cashless claim process work?",
                },
            ],
        },
        "composer": {
            "placeholder": "Ask about plans, claims, quotes, or callbacks...",
            "attachments": {"enabled": False},
        },
    }
    serialized_options = json.dumps(options)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Health Insurance Assistant</title>
    <script src="https://cdn.platform.openai.com/deployments/chatkit/chatkit.js" async></script>
    <style>
      html, body {{
        margin: 0;
        min-height: 100%;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f6f8fb;
        color: #172033;
      }}

      body {{
        min-height: 100vh;
        display: grid;
        grid-template-rows: auto 1fr;
      }}

      .topbar {{
        border-bottom: 1px solid #dbe2ee;
        background: #ffffff;
        padding: 16px 24px;
      }}

      .topbar h1 {{
        margin: 0;
        font-size: 18px;
        line-height: 1.3;
        font-weight: 650;
      }}

      .topbar p {{
        margin: 4px 0 0;
        color: #5f6f86;
        font-size: 14px;
      }}

      .chat-shell {{
        height: calc(100vh - 74px);
        padding: 16px;
        box-sizing: border-box;
      }}

      openai-chatkit {{
        display: block;
        width: min(100%, 980px);
        height: 100%;
        margin: 0 auto;
        border: 1px solid #dbe2ee;
        border-radius: 12px;
        overflow: hidden;
        background: #ffffff;
      }}

      @media (max-width: 720px) {{
        .topbar {{
          padding: 12px 16px;
        }}

        .chat-shell {{
          height: calc(100vh - 68px);
          padding: 0;
        }}

        openai-chatkit {{
          width: 100%;
          border: 0;
          border-radius: 0;
        }}
      }}
    </style>
  </head>
  <body>
    <header class="topbar">
      <h1>Health Insurance Assistant</h1>
      <p>Policy answers, comparisons, quote requests, callbacks, and claim guidance.</p>
    </header>
    <main class="chat-shell">
      <openai-chatkit id="insurance-chat"></openai-chatkit>
    </main>
    <script>
      const options = {serialized_options};
      options.api.url = new URL(options.api.url, window.location.origin).toString();
      customElements.whenDefined("openai-chatkit").then(() => {{
        const chat = document.getElementById("insurance-chat");
        chat.setOptions(options);
      }});
    </script>
  </body>
</html>"""
