"""Realistic messy hiring-post fixtures for parse/dedupe tests."""

MESSY_POSTS = {
    "emoji_plain_email": (
        "🚀 We're hiring a Backend Engineer (Python/FastAPI) in Bangalore! "
        "3+ years exp. Skills: PostgreSQL, Redis, Docker. "
        "Mail your CV to priya.sharma@acme.io #hiring #opentowork"
    ),
    "obfuscated_at_dot": (
        "Looking for Software Engineers — React OR Node. Remote-friendly (India). "
        "Please email jane [at] brightlabs [dot] com with your resume. "
        "Also open: DevOps Engineer role if that fits better."
    ),
    "obfuscated_parens": (
        "Hiring fullstack! Send applications to talent(at)novaapps(dot)io today."
    ),
    "no_email_dm_only": (
        "🔥 We are looking for a Python Developer | 2-4 YOE | Hybrid Mumbai. "
        "Apply via LinkedIn DM or careers portal — no email listed here."
    ),
    "multiple_emails": (
        "Roles: (1) Backend Engineer (2) Data Engineer. "
        "Backend -> eng-backend@corp.example; "
        "Data -> data-hiring@corp.example. "
        "Prefer first role for senior candidates."
    ),
    "uppercase_at": (
        "Please send your CV to HIRING AT MEGACORP DOT CO"
    ),
}
