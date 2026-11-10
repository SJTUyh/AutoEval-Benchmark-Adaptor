import pytest

from autoeval_adaptor.config import ParamReader, parse_request
from autoeval_adaptor.errors import ConfigError


def test_parse_request_ok():
    request = parse_request(
        {"tool": "ais_bench", "subcommand": "llm_accuracy", "params": {"abbr": "x"}}
    )
    assert request.tool == "ais_bench"
    assert request.subcommand == "llm_accuracy"
    assert request.params == {"abbr": "x"}


def test_top_level_extra_key_rejected():
    with pytest.raises(ConfigError, match="顶层只允许"):
        parse_request(
            {
                "tool": "ais_bench",
                "subcommand": "llm_accuracy",
                "params": {},
                "work_dir": "/tmp/x",
            }
        )


def test_missing_top_level_key_rejected():
    with pytest.raises(ConfigError, match="缺少顶层字段: params"):
        parse_request({"tool": "ais_bench", "subcommand": "llm_accuracy"})


def test_params_must_be_object():
    with pytest.raises(ConfigError, match="params 必须是 JSON 对象"):
        parse_request({"tool": "ais_bench", "subcommand": "x", "params": []})


def test_param_reader_reports_unknown_and_type_errors():
    reader = ParamReader({"unknown": 1})
    with pytest.raises(ConfigError, match="未知参数: unknown"):
        reader.finish()

    reader = ParamReader({"host_port": "8080"})
    with pytest.raises(ConfigError, match="期望整数"):
        reader.take("host_port", kind="int")

    reader = ParamReader({})
    with pytest.raises(ConfigError, match="缺少必填参数: abbr"):
        reader.take("abbr", required=True)
