SYSTEM_PROMPT = (
    "You are an expert B2B sales copywriter specialising in digital marketing "
    "services for local businesses. Write concise, persuasive, and empathetic "
    "cold outreach messages. Always output valid JSON only — no markdown, no extra text."
)

# ── Email pitch (cold outreach to the business inbox) ────────────────────────
EMAIL_PROMPT_TEMPLATE = """Write a personalised cold outreach sales pitch email for a potential client.

CLIENT DETAILS:
- Business Name: {name}
- Category: {category}
- City: {city}
- Website: {url}
- Contact Email: {contact_email}

WEBSITE AUDIT RESULTS:
- Overall Score: {total}/100  (Grade: {grade})
- Performance score: {perf}/25
- SEO score: {seo}/25
- Accessibility score: {a11y}/20
- Security score: {sec}/15
- Content score: {content}/15

TOP ISSUES FOUND:
{issues}

OUTPUT FORMAT (strict JSON, no markdown):
{{
  "pitch_subject": "<compelling email subject line>",
  "pitch_hook": "<one powerful opening sentence that hooks the reader>",
  "pitch_body": "<full professional email body, 150-220 words, conversational tone, ends with a soft CTA>",
  "key_pain_points": ["<pain point 1>", "<pain point 2>", "<pain point 3>"]
}}

Rules:
- Address the email to {contact_name} if a name is provided, otherwise to the {name} team
- Reference specific issues and metrics from the audit — be concrete
- Tone: professional but warm, not pushy
- The CTA should ask for a 15-minute call
- Do NOT mention competitor names
"""

# ── LinkedIn DM pitch (personalised to a specific contact) ───────────────────
LINKEDIN_PROMPT_TEMPLATE = """Write a personalised LinkedIn direct message pitch for a specific contact at a potential client company.

CONTACT DETAILS:
- Contact Name: {contact_name}
- Job Title: {contact_title}
- Company: {name}
- Category: {category}
- City: {city}
- Website: {url}

WEBSITE AUDIT RESULTS:
- Overall Score: {total}/100  (Grade: {grade})
- Performance score: {perf}/25
- SEO score: {seo}/25
- Accessibility score: {a11y}/20
- Security score: {sec}/15
- Content score: {content}/15

TOP ISSUES FOUND:
{issues}

OUTPUT FORMAT (strict JSON, no markdown):
{{
  "pitch_subject": null,
  "pitch_hook": "<one-sentence opener referencing their role and the company>",
  "pitch_body": "<LinkedIn DM body: 80-130 words, peer-to-peer tone, no corporate fluff, ends with a soft open-ended question not a hard CTA>",
  "key_pain_points": ["<pain point 1>", "<pain point 2>", "<pain point 3>"]
}}

Rules:
- Address {contact_name} by FIRST NAME only (informal but respectful)
- Reference their job title ({contact_title}) naturally — e.g. "as someone overseeing the marketing side..."
- Keep it SHORT — LinkedIn DMs must feel human, not templated
- Reference 1-2 specific audit issues maximum — don't dump the full list
- End with a low-pressure question (e.g. "Would this be worth a quick chat?")
- Do NOT mention competitor names
- Do NOT use words like "synergy", "leverage", "game-changer"
"""

# ── LinkedIn DM Suggested Reply (based on full chat history) ─────────────────
SUGGESTED_REPLY_PROMPT_TEMPLATE = """You are a top-performing B2B sales development representative responding to a client's LinkedIn message.

CONTACT DETAILS:
- Contact Name: {contact_name}
- Company: {company_name}

INITIAL PITCH CONTEXT:
- Original Pitch: {pitch_context}

FULL CHAT HISTORY (in chronological order):
{chat_history_text}

TASK:
Analyze the conversation trajectory above. Count the number of prior exchanges — if there are already multiple back-and-forth messages, this is an ongoing conversation and you must NOT open with a greeting like "Hi {contact_name},". Instead, reply naturally as if continuing the chat thread. Only use a greeting on the very first reply (when the chat history has only the initial pitch and one inbound message). Generate a professional 1-3 sentence LinkedIn DM reply to {contact_name}'s latest message.

OUTPUT FORMAT (strict JSON, no markdown):
{{
  "suggested_reply": "<1-3 sentence natural, peer-to-peer LinkedIn DM follow-up response>"
}}

Rules:
- ONLY greet {contact_name} by name (e.g. "Hi {contact_name},") if this is the very first reply in the conversation. For all subsequent messages, skip the greeting entirely and dive straight into the response.
- Directly answer or acknowledge their latest message while taking the conversation forward
- Keep it brief (20-60 words), human, and helpful — no hard sales push or pushiness
- Sound like a real colleague/consultant sending a quick message
"""

# Backwards-compatible alias (existing code that imports HUMAN_PROMPT_TEMPLATE still works)
HUMAN_PROMPT_TEMPLATE = EMAIL_PROMPT_TEMPLATE

