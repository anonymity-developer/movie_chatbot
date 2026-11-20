"""Shared GMS client for structured planner, worker, and diagnostic decisions."""

import json
import os
import re

from openai import OpenAI, OpenAIError
from pydantic import ValidationError


GMS_CLAUDE_URL = "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1"
GMS_CLAUDE_MODEL = "claude-haiku-5-5"


class PlannerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def complete(schema, instruction, data):
    key = os.getenv("GMS_API_KEY")
    if not key:
        raise PlannerError("MODEL_CONFIG_MISSING", "서버의 GMS_API_KEY 설정을 확인해 주세요.")
    try:
        with OpenAI(
            api_key=key,
            base_url=GMS_CLAUDE_URL,
            default_headers={"x-api-key": key},
            timeout=12,
            max_retries=0,
        ) as client:
            # The GMS Claude chat endpoint rejects OpenAI's json_object response format.
            response = client.chat.completions.create(
                model=GMS_CLAUDE_MODEL,
                messages=[
                    {"role": "system", "content": (
                        instruction + "\n아래 스키마에 맞는 JSON 객체 하나만 반환하세요. 마크다운과 설명은 쓰지 마세요."
                        "\nJSON 스키마: " + json.dumps(schema.model_json_schema(), ensure_ascii=False)
                    )},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                ],
            )
        content = (response.choices[0].message.content or "").strip()
        fenced_json = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.IGNORECASE | re.DOTALL)
        return schema.model_validate_json(fenced_json[1] if fenced_json else content)
    except (OpenAIError, ValidationError, IndexError, ValueError):
        raise PlannerError("MODEL_UNAVAILABLE", "GMS 응답을 처리하지 못했습니다. 서버 설정을 확인해 주세요.") from None
