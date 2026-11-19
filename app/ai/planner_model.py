"""Shared GMS client for structured planner, worker, and diagnostic decisions."""

import json
import os

from openai import OpenAI, OpenAIError
from pydantic import ValidationError
from app.config import GMS_OPENAI_URL


class PlannerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def complete(schema, instruction, data):
    key = os.getenv("GMS_API_KEY")
    base = os.getenv("GMS_BASE_URL") or GMS_OPENAI_URL
    model = os.getenv("GMS_CHAT_MODEL") or "gpt-5.4-nano"
    if not key:
        raise PlannerError("MODEL_CONFIG_MISSING", "서버의 GMS_API_KEY 설정을 확인해 주세요.")
    try:
        with OpenAI(api_key=key, base_url=base, timeout=12, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model, response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": instruction + "\nJSON 스키마: " + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                ],
            )
        return schema.model_validate_json(response.choices[0].message.content or "")
    except (OpenAIError, ValidationError, IndexError, ValueError):
        raise PlannerError("MODEL_UNAVAILABLE", "GMS 응답을 처리하지 못했습니다. 서버 설정을 확인해 주세요.") from None
