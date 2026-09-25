# ============================================================
# AI RESPONSE
# ============================================================
#
# Groq chat completion + system prompt carried over verbatim from
# the reference MQTT server.py (model, temperature 0.2, max_tokens
# 150, training-context grounding).

import logging

from . import config

logger = logging.getLogger("WS_AI_SERVER")

SYSTEM_PROMPT = """
You are an AI assistant for a merchant using a payment Soundbox.

Your job is to understand the merchant's spoken question and provide
a short, useful response.

Soundbox LED status:

- Solid Green:
  Device is turning on.

- Blinking Green:
  SIM is not detected.

- Blinking Blue:
  Device is searching for network.

- Solid Blue:
  Network connected, MQTT connected, device is ready.

- Blinking Red:
  Device is not connected.

Important behavior:

1. Keep responses short because the response may be played through
   a Soundbox.
2. Give practical troubleshooting steps.
3. Do not give unnecessary technical details.
4. If the merchant says a transaction is not being announced,
   first check network connectivity and LED status.
5. If the merchant provides an LED status, use it to guide the
   troubleshooting.
6. Do not invent device status that was not provided.
7. If the question is unclear, ask one short clarification question.
8. Prefer responses that can be spoken naturally.

Example:

Merchant:
"My transaction happened but Soundbox did not speak."

Response:
"Please check the Soundbox LED. If it is solid blue, the network
and MQTT connection are ready. Please tell me the current LED status."

Example:

Merchant:
"The blue light is blinking."

Response:
"The Soundbox is searching for a network. Please wait for the
light to become solid blue."
"""


def generate_ai_response(user_text, extra_context=None):
    """
    Send transcribed text to Groq LLM.

    extra_context: optional grounding text (from a partial training-data
    match) appended to the system prompt so the model stays consistent
    with previously reviewed answers instead of inventing one.
    """

    logger.info(
        "Sending text to AI: %s",
        user_text
    )

    system_content = SYSTEM_PROMPT
    if extra_context:
        system_content = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Relevant known context for this situation:\n{extra_context}"
        )

    completion = config.groq_client.chat.completions.create(

        model=config.AI_MODEL,

        messages=[
            {
                "role": "system",
                "content": system_content
            },
            {
                "role": "user",
                "content": user_text
            }
        ],

        temperature=0.2,

        max_tokens=150
    )

    response = completion.choices[0].message.content

    if response is None:
        return ""

    return response.strip()
