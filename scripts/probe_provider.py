"""Check configured model endpoints without printing credentials or error bodies."""

from __future__ import annotations

import argparse
import os

from openai import OpenAI

from app.config import Settings, openai_options


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("embedding", "chat"))
    args = parser.parse_args()
    settings = Settings.from_env()
    options = openai_options()
    if not options:
        raise SystemExit("GMS_API_KEY or OPENAI_API_KEY is missing")
    client = OpenAI(**options, timeout=10, max_retries=0)
    print(f"gateway_configured={'base_url' in options}", flush=True)
    try:
        if args.kind == "embedding":
            response = client.embeddings.create(model=settings.embedding_model, input=["movie indexing probe"])
            print(f"embedding_ok dimension={len(response.data[0].embedding)}")
        else:
            response = client.chat.completions.create(
                model=os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini"),
                messages=[{"role": "user", "content": "Reply with OK only."}],
                max_tokens=10,
            )
            print(f"chat_ok choices={len(response.choices)}")
    except Exception as exc:
        print(
            f"{args.kind}_error type={type(exc).__name__} "
            f"status={getattr(exc, 'status_code', None)}"
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
