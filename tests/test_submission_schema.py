import pytest
from pydantic import ValidationError

from baseline_naive_rag import Submission, validate_payload


def valid_payload():
    return {
        "schema_version": "1.0",
        "round_id": "sample",
        "answers": [
            {
                "question_id": "Q1",
                "question": "Domanda di esempio?",
                "answer": "Risposta",
                "contexts": [
                    {
                        "rank": 1,
                        "document_id": "d1",
                        "content": "evidenza",
                    }
                ],
                "telemetry": {
                    "latency_ms": 10,
                    "declared_cost_eur": 0.0,
                    "model_calls": [
                        {
                            "provider": "openai",
                            "model": "gpt-4o-mini",
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "cached_input_tokens": 0,
                        }
                    ],
                },
            }
        ],
    }


def test_valid_submission():
    assert Submission.model_validate(valid_payload()).schema_version == "1.0"
    assert validate_payload(valid_payload()) == []


@pytest.mark.parametrize("field", ["confidence", "answer_type", "estimated_cost", "commit_hash", "repository_url"])
def test_forbidden_fields(field):
    payload = valid_payload()
    payload[field] = "forbidden"
    with pytest.raises(ValidationError):
        Submission.model_validate(payload)


def test_rank_must_be_contiguous():
    payload = valid_payload()
    payload["answers"][0]["contexts"][0]["rank"] = 2
    errors = validate_payload(payload)
    assert any("rank" in error for error in errors)


def test_legacy_context_fields_forbidden():
    payload = valid_payload()
    payload["answers"][0]["contexts"][0]["chunk_id"] = "c1"
    with pytest.raises(ValidationError):
        Submission.model_validate(payload)
