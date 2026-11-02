"""Generate structured document content through Amazon Bedrock Converse."""

import json

from backend.documents.errors import DocumentError
from backend.documents.models import LANGUAGES, GenerationInput, TailoredCv
from backend.documents.prompts import SYSTEM


def tool_schema(model_id):
    schema = TailoredCv.model_json_schema()
    if "amazon.nova-" not in model_id:
        return schema

    # Nova accepts only type/properties/required at the tool schema root.
    # Inline Pydantic's local references so no root $defs are needed.
    definitions = schema.get("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(definitions[value["$ref"].removeprefix("#/$defs/")])
        return {
            key: inline(item)
            for key, item in value.items()
            if key not in {"$defs", "title", "additionalProperties"}
        }

    return {key: inline(schema[key]) for key in ("type", "properties", "required")}


class BedrockGenerator:
    def __init__(self, client):
        self.client = client

    def __call__(self, request: GenerationInput, previous=None):
        # Profile contact details are not model inputs (the letter header is rendered locally).
        # The CV source is sent as written, so its own header does reach the model.
        facts = request.profile.model_dump(
            mode="json", exclude={"user_id", "email", "phone", "full_name", "links"}
        )
        prompt = {
            "cv_latex": request.cv_latex,
            "candidate_facts": facts,
            "job": request.job.model_dump(mode="json"),
            "output_language": next(
                language.label for language in LANGUAGES if language.id == request.language
            ),
        }
        if request.system_prompt:
            prompt["candidate_standing_instructions"] = request.system_prompt.text
        if request.instructions:
            prompt["candidate_comments"] = request.instructions
        if request.previous_version:
            prompt["previous_version"] = request.previous_version.model_dump(mode="json")
        if previous:
            prompt["previous_attempt"] = previous
        response = self.client.converse(
            modelId=request.model_id,
            system=[{"text": SYSTEM}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            inferenceConfig={"maxTokens": 6000 if "amazon.nova-" in request.model_id else 16000},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "deliver_documents",
                            "description": "Provide the rewritten CV body, the list of changes "
                            "and the two letters.",
                            "inputSchema": {"json": tool_schema(request.model_id)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "deliver_documents"}},
            },
        )

        reason = response.get("stopReason")
        if reason != "tool_use":
            known_reasons = {
                "end_turn",
                "max_tokens",
                "stop_sequence",
                "guardrail_intervened",
                "content_filtered",
                "malformed_model_output",
                "malformed_tool_use",
                "model_context_window_exceeded",
            }
            safe_reason = reason if reason in known_reasons else "unknown"
            raise DocumentError(
                "Bedrock did not return complete structured documents; stop_reason=" + safe_reason
            )
        blocks = response["output"]["message"]["content"]
        outputs = [block["toolUse"] for block in blocks if "toolUse" in block]
        if len(outputs) != 1 or outputs[0].get("name") != "deliver_documents":
            raise DocumentError(
                "Bedrock returned an unexpected tool name or number of tool results"
            )

        content = TailoredCv.model_validate(outputs[0]["input"])
        return content, response.get("usage", {})
