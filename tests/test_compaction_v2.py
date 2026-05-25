from agent.compaction import should_compact
from agent.config import Config
from agent.context import estimate_tokens
from agent.settings import CompactionSettings


def test_token_estimator_weights_tool_output_higher() -> None:
    base = [{"role": "user", "content": "a" * 400}]
    tool = [
        {"role": "user", "content": "a" * 400},
        {"role": "tool", "content": "b" * 400},
    ]
    assert estimate_tokens(tool) > estimate_tokens(base)


def test_token_estimator_weights_code_blocks() -> None:
    plain = [{"role": "system", "content": "x" * 400}]
    code = [{"role": "system", "content": "```python\ndef foo(): pass\n```" + "y" * 380}]
    assert estimate_tokens(code) >= estimate_tokens(plain)


def test_compaction_respects_enabled_flag() -> None:
    config = Config(
        cwd=".",
        model="test",
        context_window_tokens=1000,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=False, threshold=0.1),
    )
    messages = [{"role": "user", "content": "x" * 5000}]
    assert not should_compact(messages, config)


def test_compaction_config_threshold_override() -> None:
    config = Config(
        cwd=".",
        model="test",
        context_window_tokens=1000,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.5),
    )
    messages = [{"role": "user", "content": "x" * 2500}]
    assert should_compact(messages, config)
