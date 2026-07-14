import os
from typing import Any

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash-lite",
    "openai": "gpt-4o-mini",
    "claude": "claude-3-5-haiku-latest",
    "groq": "llama-3.3-70b-versatile",
}


def get_llm(
    provider: str | None = None,
    model_name: str | None = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> Any:
    if not provider:
        provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    else:
        provider = provider.strip().lower()

    if not model_name:
        model_name = os.getenv("LLM_MODEL", "").strip()
        if not model_name:
            model_name = DEFAULT_MODELS.get(provider, "gemini-2.5-flash-lite")

    if provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "Google Gemini dependencies are missing. "
                "Please run: pip install langchain-google-genai"
            )

        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key

        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            **kwargs,
        )

    elif provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "OpenAI dependencies are missing. "
                "Please run: pip install langchain-openai"
            )

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            **kwargs,
        )

    elif provider == "claude":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "Anthropic Claude dependencies are missing. "
                "Please run: pip install langchain-anthropic"
            )

        return ChatAnthropic(
            model_name=model_name,
            temperature=temperature,
            **kwargs,
        )

    elif provider == "groq":
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            raise ImportError(
                "Groq dependencies are missing. "
                "Please run: pip install langchain-groq"
            )

        return ChatGroq(
            model_name=model_name,
            temperature=temperature,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            "Supported providers: gemini | openai | claude | groq"
        )
