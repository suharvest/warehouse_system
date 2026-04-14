"""WMS Provider 自动发现与加载

扫描 providers/ 目录下所有 .py 文件，自动注册 BaseProvider 子类。
新增 Provider 只需放一个 .py 文件、设 PROVIDER_NAME，无需手动注册。
用户上传的自定义 Provider 放入 custom/ 子目录，同样自动发现。
"""

import importlib
import importlib.util
import logging
import os
import pkgutil

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")

_registry: dict[str, type[BaseProvider]] = {}


def _register_module(mod, fallback_name: str):
    """扫描模块中的 BaseProvider 子类并写入 _registry。"""
    for attr_name in dir(mod):
        cls = getattr(mod, attr_name)
        if (
            isinstance(cls, type)
            and issubclass(cls, BaseProvider)
            and cls is not BaseProvider
        ):
            name = getattr(cls, "PROVIDER_NAME", None) or fallback_name
            _registry[name] = cls
            logger.debug(f"注册 provider: {name} -> {cls.__name__}")


def _discover():
    """扫描当前包目录及 custom/ 子目录，注册所有 BaseProvider 子类。"""
    pkg_dir = os.path.dirname(__file__)

    # ── 扫描包内置模块（validator / test_runner 等辅助模块跳过）──
    _skip_modules = {"base", "validator", "test_runner"}
    for _, module_name, _ in pkgutil.iter_modules([pkg_dir]):
        if module_name in _skip_modules:
            continue
        try:
            mod = importlib.import_module(f".{module_name}", __package__)
        except Exception as e:
            logger.warning(f"加载 provider 模块 '{module_name}' 失败: {e}")
            continue
        _register_module(mod, module_name)

    # ── 扫描 custom/ 子目录（文件式加载，不要求是包） ──
    custom_dir = os.path.join(pkg_dir, "custom")
    if not os.path.isdir(custom_dir):
        return

    for filename in sorted(os.listdir(custom_dir)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        filepath = os.path.join(custom_dir, filename)
        module_name = filename[:-3]  # 去掉 .py 后缀
        try:
            spec = importlib.util.spec_from_file_location(
                f"providers.custom.{module_name}", filepath
            )
            if spec is None or spec.loader is None:
                logger.warning(f"无法为自定义 provider 创建 spec: {filepath}")
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning(f"加载自定义 provider '{filename}' 失败: {e}")
            continue
        _register_module(mod, module_name)


_discover()


def register_provider_from_file(filepath: str) -> str:
    """从 .py 文件动态加载并注册 Provider。

    文件必须包含一个 BaseProvider 子类，且设置了有效的 PROVIDER_NAME。
    重复注册同名 Provider 时会覆盖旧注册。

    Args:
        filepath: Provider .py 文件的绝对路径。

    Returns:
        注册成功后的 provider_name（即 PROVIDER_NAME 值）。

    Raises:
        ValueError: 文件中未找到 BaseProvider 子类，或 PROVIDER_NAME 未设置。
        ImportError: 文件加载失败。
    """
    module_name = os.path.splitext(os.path.basename(filepath))[0]
    spec = importlib.util.spec_from_file_location(
        f"providers.dynamic.{module_name}", filepath
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为文件创建模块 spec: {filepath}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    # 找到 BaseProvider 子类
    provider_cls = None
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseProvider)
            and obj is not BaseProvider
        ):
            provider_cls = obj
            break

    if provider_cls is None:
        raise ValueError(f"文件 '{filepath}' 中未找到 BaseProvider 子类")

    provider_name = getattr(provider_cls, "PROVIDER_NAME", None)
    if not provider_name:
        raise ValueError(
            f"Provider 类 '{provider_cls.__name__}' 未设置有效的 PROVIDER_NAME"
        )

    _registry[provider_name] = provider_cls
    logger.info(f"动态注册 provider: {provider_name} -> {provider_cls.__name__} (from {filepath})")
    return provider_name


def unregister_provider(name: str) -> bool:
    """从注册表中移除指定名称的 Provider。

    Args:
        name: 要移除的 provider_name（对应 PROVIDER_NAME）。

    Returns:
        True 表示找到并移除，False 表示原本不存在。
    """
    if name in _registry:
        del _registry[name]
        logger.info(f"已注销 provider: {name}")
        return True
    return False


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
