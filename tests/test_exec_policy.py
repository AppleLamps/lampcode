from approval.gate import exec_policy_block_reason, needs_approval_prompt

from agent.config import Config
from agent.exec_policy import (
    ExecPolicyConfig,
    ExecPolicyMode,
    ExecPolicyRules,
    evaluate_command,
    match_rule,
    should_prompt_for_command,
)


def test_match_rule_glob() -> None:
    assert match_rule("git status --short", "git status*")
    assert not match_rule("git commit -m x", "git status*")


def test_deny_rule_blocks() -> None:
    policy = ExecPolicyConfig()
    result = evaluate_command("rm -rf /tmp/x", policy)
    assert result["decision"] == "deny"


def test_allow_rule_auto_approves() -> None:
    policy = ExecPolicyConfig()
    result = evaluate_command("git status", policy)
    assert result["decision"] == "allow"
    assert result["auto_approve"]


def test_untrusted_auto_approves_read_only() -> None:
    policy = ExecPolicyConfig(mode=ExecPolicyMode.UNTRUSTED)
    result = evaluate_command("dir", policy)
    assert result["auto_approve"]


def test_untrusted_prompts_unknown_command() -> None:
    policy = ExecPolicyConfig(mode=ExecPolicyMode.UNTRUSTED)
    assert should_prompt_for_command("powershell -Command Remove-Item x", policy)


def test_never_mode_no_prompt() -> None:
    policy = ExecPolicyConfig(mode=ExecPolicyMode.NEVER)
    assert not should_prompt_for_command("del /s /q foo", policy)


def test_needs_approval_untrusted_git_status() -> None:
    config = Config(
        cwd=".",
        model="test",
        openrouter_api_key="x",
        exec_policy=ExecPolicyConfig(mode=ExecPolicyMode.UNTRUSTED),
    )
    assert not needs_approval_prompt(
        "run_command", {"cmd": "git status"}, config
    )


def test_exec_policy_block_reason_deny() -> None:
    config = Config(
        cwd=".",
        model="test",
        openrouter_api_key="x",
        exec_policy=ExecPolicyConfig(
            rules=ExecPolicyRules(deny=["del /s *"]),
        ),
    )
    reason = exec_policy_block_reason("del /s /q foo", config)
    assert reason is not None
    assert "deny" in reason.lower()
