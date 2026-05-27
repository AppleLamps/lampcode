from __future__ import annotations

from agent.user_input import normalize_user_input_questions


def test_normalize_single_question() -> None:
    specs = normalize_user_input_questions(
        {"question": "Pick one", "options": ["A", "B"]}
    )
    assert len(specs) == 1
    assert specs[0]["question"] == "Pick one"
    assert specs[0]["options"] == ["A", "B"]


def test_normalize_questions_batch() -> None:
    specs = normalize_user_input_questions(
        {
            "questions": [
                {"question": "First?", "options": ["yes", "no"]},
                {"question": "Second?", "allow_free_text": True},
            ]
        }
    )
    assert len(specs) == 2
    assert specs[1]["question"] == "Second?"
