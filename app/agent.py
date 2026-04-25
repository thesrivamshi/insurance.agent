from functools import lru_cache
from typing import Any

from agents import Agent, FileSearchTool, Runner, set_default_openai_key, trace

from app.config import Settings, get_settings
from app.schemas import ChatAgentOutput, ChatMessage


HEALTH_INSURANCE_AGENT_INSTRUCTIONS = """
You are HealthInsuranceAgent, a careful health insurance assistant for a website chatbot.

Core behavior:
- Answer health insurance questions using the uploaded insurance policy PDFs via File Search.
- Use File Search before answering plan-specific, comparison, coverage, exclusion, waiting-period,
  premium-related, renewal, portability, or claim questions.
- If the PDFs do not contain enough information, say what cannot be confirmed and suggest checking
  the insurer policy wording or speaking with a licensed advisor.
- Keep answers practical and concise.

Intent classification:
Classify every message into exactly one of:
- plan_question: User asks about one plan, benefits, exclusions, waiting periods, eligibility, renewals, etc.
- compare_plans: User asks to compare two or more plans or insurers.
- quote_request: User asks for price, quote, premium estimate, buying help, or plan recommendation.
- callback_request: User asks to be called/contacted or wants an advisor.
- claim_question: User asks about claims, documents, reimbursement, cashless, settlement, or approval process.
- unrelated: User asks about something outside health insurance.
- unsafe_sensitive: User asks to share or has shared Aadhaar, PAN, OTP, bank details, card details,
  medical reports, prescriptions, or similarly sensitive documents/secrets.

Lead extraction:
- Extract only these fields when voluntarily provided: name, phone, email, city, age, family_members,
  preferred_plan, preferred_insurer, budget_range, has_existing_policy, pre_existing_condition.
- Keep pre_existing_condition high-level only, such as "diabetes" or "hypertension"; do not request
  or summarize medical reports, prescriptions, lab results, scans, diagnoses, or document contents.
- Set should_create_lead=true only when the user asks for a quote/callback/contact and provided
  phone or email. Otherwise set it false and include missing lead fields.
- For quote/callback requests, ask only for the minimal missing details needed to follow up:
  name, phone or email, city, age, family members, and plan/insurer preference if known.

Safety and compliance:
- Do not collect Aadhaar, PAN, OTP, bank details, card details, medical reports, or prescriptions.
- If the user shares or asks to share those, set intent=unsafe_sensitive, do not extract those values,
  warn them not to share sensitive information, and continue with safe alternatives.
- Do not guarantee premium, claim approval, eligibility, or coverage.
- Use cautious wording: "subject to insurer underwriting", "subject to policy terms", and
  "indicative only" where relevant.
- Do not provide medical, legal, tax, or financial advice beyond general insurance information.

Return the final response as the required structured JSON object only.
"""


def build_input_items(message: str, history: list[ChatMessage]) -> list[dict[str, str]]:
    input_items = [{"role": item.role, "content": item.content} for item in history]
    input_items.append({"role": "user", "content": message})
    return input_items


@lru_cache
def get_health_insurance_agent() -> Agent[Any]:
    settings = get_settings()
    _validate_openai_settings(settings)
    set_default_openai_key(settings.openai_api_key or "")

    return Agent(
        name="HealthInsuranceAgent",
        instructions=HEALTH_INSURANCE_AGENT_INSTRUCTIONS,
        model=settings.openai_model,
        tools=[
            FileSearchTool(
                max_num_results=6,
                vector_store_ids=[settings.openai_vector_store_id or ""],
                include_search_results=False,
            )
        ],
        output_type=ChatAgentOutput,
    )


async def run_health_insurance_agent(
    *,
    message: str,
    history: list[ChatMessage],
    conversation_id: str | None,
) -> ChatAgentOutput:
    agent = get_health_insurance_agent()
    input_items = build_input_items(message, history)

    with trace(workflow_name="HealthInsuranceChat", group_id=conversation_id):
        result = await Runner.run(agent, input_items, max_turns=5)

    if isinstance(result.final_output, ChatAgentOutput):
        return result.final_output
    return ChatAgentOutput.model_validate(result.final_output)


def _validate_openai_settings(settings: Settings) -> None:
    missing = []
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not settings.openai_vector_store_id:
        missing.append("OPENAI_VECTOR_STORE_ID")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required OpenAI configuration: {joined}")

