import json

import pytest

from autoeval_adaptor.errors import ConfigError
from autoeval_adaptor.registry import registered_adapters
from autoeval_adaptor.template import load_template, merge_params


@pytest.mark.parametrize("tool,subcommand", registered_adapters())
def test_every_adapter_has_template(tool, subcommand):
    loaded = load_template(tool, subcommand)
    assert loaded.params


@pytest.mark.parametrize(
    "tool,subcommand,required",
    [
        (
            "ais_bench",
            "llm_accuracy",
            ["abbr", "model_type", "host_ip", "host_port", "work_dir"],
        ),
        (
            "ais_bench",
            "harbor_agent",
            ["abbr", "model_names", "api_base", "work_dir"],
        ),
    ],
)
def test_builtin_template_exists(tool, subcommand, required):
    loaded = load_template(tool, subcommand)
    assert loaded.source == f"builtin:{tool}/{subcommand}"
    for key in required:
        assert key in loaded.params


def test_merge_params_user_wins_and_dicts_merge():
    template = {
        "abbr": "template",
        "generation_kwargs": {"temperature": 0.1, "top_p": 0.9},
        "datasets": ["template_dataset"],
    }
    user = {
        "abbr": "user",
        "generation_kwargs": {"temperature": 1.0},
        "datasets": ["user_dataset"],
    }

    merged = merge_params(template, user)

    assert merged["abbr"] == "user"
    assert merged["generation_kwargs"] == {"temperature": 1.0, "top_p": 0.9}
    assert merged["datasets"] == ["user_dataset"]
    # 不修改入参
    assert template["generation_kwargs"]["temperature"] == 0.1


def test_template_tool_mismatch_rejected(tmp_path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {"tool": "other", "subcommand": "llm_accuracy", "params": {"abbr": "x"}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="不匹配"):
        load_template("ais_bench", "llm_accuracy", template)


def test_missing_template_rejected():
    with pytest.raises(ConfigError, match="未找到内置配置模板"):
        load_template("unknown_tool", "unknown_subcommand")
