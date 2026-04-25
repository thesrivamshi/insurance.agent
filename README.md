# Health Insurance Chatbot Backend

FastAPI backend for a health insurance website chatbot powered by the OpenAI Agents SDK and OpenAI File Search. It answers questions from uploaded insurance PDFs, classifies intent, extracts lead fields, and persists leads to a `leads` database table.

## What It Includes

- `POST /chat` main chatbot endpoint
- `GET /` official OpenAI ChatKit web interface served by FastAPI
- `POST /chatkit` ChatKit custom backend protocol endpoint
- `POST /lead` manual lead capture endpoint
- `GET /health` health check
- One main agent: `HealthInsuranceAgent`
- OpenAI File Search against `OPENAI_VECTOR_STORE_ID`
- Structured JSON output with classification and extracted fields
- SQLite development database through SQLAlchemy async, with Postgres-ready URL support
- Persistent ChatKit conversations and messages through the same database
- Request/error logging without logging request bodies
- Safety checks that reject Aadhaar, PAN, OTP, bank/card details, medical reports, and prescriptions

## Setup

Use Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```bash
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_VECTOR_STORE_ID=vs_your_vector_store_id
DATABASE_URL=sqlite+aiosqlite:///./data/insurance_chatbot.db
CHATKIT_DOMAIN_KEY=local-dev
```

The default model is `gpt-5.4-mini`. Override with `OPENAI_MODEL` if needed.

## Vector Store

Upload your insurance PDFs to an OpenAI vector store, then set the vector store ID in `OPENAI_VECTOR_STORE_ID`.

The backend does not upload PDFs on every startup. Production deployments should treat vector store ingestion as an admin/setup workflow, not as part of request handling.

## Run

```bash
uvicorn app.main:app --reload
```

Open:

- ChatKit UI: `http://127.0.0.1:8000/`
- Health check: `http://127.0.0.1:8000/health`
- API docs: `http://127.0.0.1:8000/docs`

## API Examples

### Chat

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "demo-user-1",
    "message": "Compare Care Supreme and ReAssure for a family of 4 in Bengaluru."
  }'
```

Response shape:

```json
{
  "reply": "string",
  "classification": "compare_plans",
  "extracted_fields": {
    "name": null,
    "phone": null,
    "email": null,
    "city": "Bengaluru",
    "age": null,
    "family_members": 4,
    "preferred_plan": null,
    "preferred_insurer": null,
    "budget_range": null,
    "has_existing_policy": null,
    "pre_existing_condition": null
  },
  "lead": {
    "saved": false,
    "id": null,
    "missing_fields": [],
    "reason": null
  },
  "safety": {
    "blocked": false,
    "warnings": []
  },
  "needs_human_callback": false
}
```

Leads are auto-saved from `/chat` only when the agent sets `should_create_lead=true`, a phone or email is present, and the message is not classified as `unrelated` or `unsafe_sensitive`.

### Manual Lead

```bash
curl -X POST http://127.0.0.1:8000/lead \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Riya Sharma",
    "phone": "9876543210",
    "email": "riya@example.com",
    "city": "Mumbai",
    "age": 34,
    "family_members": 3,
    "preferred_plan": "Care Supreme",
    "preferred_insurer": "Care Health",
    "budget_range": "15000-25000 yearly",
    "has_existing_policy": false,
    "pre_existing_condition": "hypertension",
    "intent": "callback_request"
  }'
```

## Switching to Postgres

Set `DATABASE_URL` to an async Postgres URL:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/insurance_chatbot
```

`postgres://` and `postgresql://` URLs are normalized to `postgresql+asyncpg://`.

For serious production use, add Alembic migrations before changing table shape. The current app creates the `leads` table automatically on startup for easy deployment and development.

## Safety Rules

The backend and agent are configured to avoid collecting:

- Aadhaar
- PAN
- OTP
- Bank details
- Card details
- Medical reports
- Prescriptions

The chatbot also must not guarantee premiums, claim approval, eligibility, or coverage. Responses should say that premiums and claims are subject to insurer underwriting and policy terms.

## Connecting to ChatKit

This repository does not include a custom HTML chat widget. It serves the official OpenAI ChatKit web component at `/` and connects that component to the backend through `POST /chatkit`.

For local testing:

1. Run the FastAPI app.
2. Open `http://127.0.0.1:8000/`.
3. Send a message from the ChatKit UI.

The browser talks only to `/chatkit`. The OpenAI API key stays on the FastAPI backend.

If you already have a separate website, embed the official ChatKit component there and configure its API URL to this backend's `/chatkit` endpoint. Keep CORS restricted to your website domain through `CORS_ORIGINS`.

`POST /chat` remains available for non-ChatKit clients or server-side integrations that want a simpler JSON request/response API.

## Deploying to Vercel

This app includes `index.py` and `vercel.json` so Vercel can run it as a Python FastAPI service.

Set these Vercel environment variables:

```bash
OPENAI_API_KEY=sk-...
OPENAI_VECTOR_STORE_ID=vs_...
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/insurance_chatbot
CHATKIT_DOMAIN_KEY=your-chatkit-domain-key
CORS_ORIGINS=https://your-domain.com
```

Use Postgres for `DATABASE_URL` on Vercel. SQLite is fine locally but is not durable in serverless production.

Deploy:

```bash
vercel
vercel --prod
```

After deployment:

1. Open the deployed URL to test the ChatKit UI.
2. Open `/health` to confirm database and OpenAI configuration.
3. Register the deployed domain with ChatKit if your OpenAI project requires a domain key for production.

## Alternative Chat SDK Integration

For a Next.js/Vercel AI SDK style route, proxy the last user message to this backend:

```ts
export async function POST(req: Request) {
  const { messages, id } = await req.json();
  const latest = messages[messages.length - 1];

  const response = await fetch(`${process.env.INSURANCE_API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: id,
      message: latest.content,
      history: messages.slice(0, -1).map((m: any) => ({
        role: m.role,
        content: m.content,
      })),
    }),
  });

  const data = await response.json();
  return Response.json({
    role: "assistant",
    content: data.reply,
    metadata: {
      classification: data.classification,
      extracted_fields: data.extracted_fields,
      lead: data.lead,
    },
  });
}
```

## Project Structure

```text
app/
  agent.py            HealthInsuranceAgent and OpenAI File Search setup
  chatkit_backend.py  ChatKit protocol adapter around HealthInsuranceAgent
  chatkit_store.py    SQLAlchemy-backed ChatKit thread/message store
  config.py           environment configuration
  database.py         async SQLAlchemy engine/session setup
  main.py             FastAPI routes, logging, error handling, ChatKit UI
  models.py           leads and ChatKit persistence tables
  repository.py       lead persistence helpers
  safety.py           sensitive-data detection and redaction
  schemas.py          request/response and structured output models
index.py              Vercel Python entrypoint
vercel.json           Vercel function configuration
.vercelignore         Excludes local PDFs, databases, and virtualenvs from deploy uploads
```
