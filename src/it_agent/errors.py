"""项目异常基类。"""

from __future__ import annotations


class AgentError(Exception):
    """所有可预期错误的基类，接口层统一转成错误响应。"""


class ConfigError(AgentError):
    """配置缺失或非法。"""


class SeedDataError(AgentError):
    """种子数据缺失或格式不合法。"""
