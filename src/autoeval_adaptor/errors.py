class AdaptorError(Exception):
    """适配器异常基类，exit_code 作为 CLI 退出码。"""

    exit_code = 2


class ConfigError(AdaptorError):
    """统一 JSON 配置不合法。"""


class DiscoveryError(AdaptorError):
    """无法定位 AISBench 或其配置别名。"""


class ExecutionError(AdaptorError):
    """外部测评工具执行失败。"""

    exit_code = 3
