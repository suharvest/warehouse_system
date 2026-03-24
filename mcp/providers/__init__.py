"""WMS Provider 自动发现与加载

扫描 providers/ 目录下所有 .py 文件，自动注册 BaseProvider 子类。
新增 Provider 只需放一个 .py 文件、设 PROVIDER_NAME，无需手动注册。
"""

import importlib
import logging
import os
import pkgutil

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")

_registry: dict[str, type[BaseProvider]] = {}


def _discover():
    """扫描当前包目录，注册所有 BaseProvider 子类。"""
    pkg_dir = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([pkg_dir]):
        if module_name == "base":
            continue
        try:
            mod = importlib.import_module(f".{module_name}", __package__)
        except Exception as e:
            logger.warning(f"加载 provider 模块 '{module_name}' 失败: {e}")
            continue

        for attr_name in dir(mod):
            cls = getattr(mod, attr_name)
            if (
                isinstance(cls, type)
                and issubclass(cls, BaseProvider)
                and cls is not BaseProvider
            ):
                name = getattr(cls, "PROVIDER_NAME", None) or module_name
                _registry[name] = cls
                logger.debug(f"注册 provider: {name} -> {cls.__name__}")


_discover()


def load_provider(config: dict) -> BaseProvider:
    """根据 config 中的 provider 字段加载对应 Provider 实例。

    Args:
        config: 从 config.yml 加载的完整配置字典。
                provider 字段可选，默认 "default"。

    Returns:
        初始化后的 Provider 实例。

    Raises:
        ValueError: provider 名称未注册。
    """
    name = config.get("provider", "default")

    if name not in _registry:
        available = ", ".join(sorted(_registry.keys()))
        raise ValueError(f"未知的 provider '{name}'。可用: {available}")

    provider_cls = _registry[name]
    logger.info(f"使用 provider: {name} ({provider_cls.__name__})")
    return provider_cls(config)
