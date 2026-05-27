from agent.context import build_system_prompt
from agent.plan_mode import build_plan_system_append
from tools.patch import APPLY_PATCH_FORMAT_DOCS, APPLY_PATCH_PARAM_DESCRIPTION
from tools.registry import TOOL_REGISTRY


def test_system_prompt_includes_patch_format(tmp_path) -> None:
    prompt = build_system_prompt(tmp_path, None)
    assert "*** Begin Patch" in prompt
    assert "*** Update File:" in prompt
    assert APPLY_PATCH_FORMAT_DOCS in prompt
    assert "file_outline" in prompt


def test_system_prompt_windows_uses_search_repo(tmp_path, monkeypatch) -> None:
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    prompt = build_system_prompt(tmp_path, None)
    assert "`search_repo`" in prompt
    assert "search_files" not in prompt


def test_plan_mode_system_append_lists_tools_and_tag() -> None:
    append = build_plan_system_append(["read_file", "search_repo", "request_user_input"])
    assert "<proposed_plan>" in append
    assert "`read_file`" in append
    assert "plan mode" in append.lower()


def test_apply_patch_tool_param_description() -> None:
    schema = TOOL_REGISTRY["apply_patch"].schema
    patch_desc = schema["function"]["parameters"]["properties"]["patch"]["description"]
    assert patch_desc == APPLY_PATCH_PARAM_DESCRIPTION
    assert "*** Begin Patch" in patch_desc
