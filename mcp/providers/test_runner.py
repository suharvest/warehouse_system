"""Provider 连通性测试执行器

动态加载用户上传的 Provider 文件，分两级运行测试：
- Level 1（只读）：resolve_name / query_stock / search / get_today_statistics
- Level 2（写操作）：stock_in / stock_out

测试结果包含每个方法的通过状态、延迟和错误信息。
"""

import importlib.util
import logging
import time
from types import ModuleType
from typing import Optional

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")


def _load_module_from_file(filepath: str) -> ModuleType:
    """从文件路径动态加载 Python 模块。

    Args:
        filepath: Provider .py 文件的绝对路径。

    Returns:
        已执行的模块对象。

    Raises:
        ImportError: 模块加载失败。
    """
    spec = importlib.util.spec_from_file_location("_provider_under_test", filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为文件创建模块 spec: {filepath}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def load_provider_from_file(filepath: str, config: dict) -> BaseProvider:
    """从文件路径加载 Provider 并实例化。

    动态扫描模块中所有 BaseProvider 子类，取第一个实例化后返回。
    本函数被 run_level1_tests / run_level2_tests 以及动态注册系统共用。

    Args:
        filepath: Provider .py 文件的绝对路径。
        config:   传递给 Provider.__init__ 的配置字典。

    Returns:
        初始化后的 BaseProvider 实例。

    Raises:
        ImportError: 文件无法加载。
        ValueError:  文件中未找到 BaseProvider 子类。
    """
    mod = _load_module_from_file(filepath)

    # 扫描模块所有属性，找到第一个 BaseProvider 子类
    provider_cls: Optional[type[BaseProvider]] = None
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

    logger.debug(f"动态加载 Provider: {provider_cls.__name__} from {filepath}")
    return provider_cls(config)


def _run_single_test(provider: BaseProvider, method_name: str, args: tuple) -> dict:
    """执行单个方法调用，捕获异常并计算耗时。

    Args:
        provider:    Provider 实例。
        method_name: 要调用的方法名。
        args:        位置参数元组。

    Returns:
        {"passed": bool, "latency_ms": float, "error": str | None}
    """
    method = getattr(provider, method_name)
    start = time.perf_counter()
    try:
        result = method(*args)
        latency_ms = (time.perf_counter() - start) * 1000
        # 检查返回值是否为 dict
        if not isinstance(result, dict):
            return {
                "passed": False,
                "latency_ms": round(latency_ms, 2),
                "error": f"返回值类型错误，期望 dict，实际 {type(result).__name__}",
            }
        return {"passed": True, "latency_ms": round(latency_ms, 2), "error": None, "_result": result}
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "passed": False,
            "latency_ms": round(latency_ms, 2),
            "error": f"{type(e).__name__}: {e}",
        }


def _check_keys(test_result: dict, required_keys: set[str], method_name: str) -> dict:
    """在 _run_single_test 的基础上校验返回字典包含必要 key。

    修改 test_result（in-place 兼容），弹出内部 _result 字段。
    """
    inner = test_result.pop("_result", None)
    if not test_result["passed"]:
        return test_result
    if inner is None:
        return test_result

    missing = required_keys - set(inner.keys())
    if missing:
        test_result["passed"] = False
        test_result["error"] = (
            f"返回值缺少必要字段: {', '.join(sorted(missing))}"
        )
    return test_result


def run_level1_tests(filepath: str, config: dict) -> dict:
    """运行 Level 1 只读测试（4 个方法）。

    Args:
        filepath: Provider .py 文件的绝对路径。
        config:   Provider 初始化配置。

    Returns:
        {
            "level": 1,
            "results": {method_name: {"passed", "latency_ms", "error"}},
            "all_passed": bool,
        }
    """
    try:
        provider = load_provider_from_file(filepath, config)
    except Exception as e:
        # 加载失败，所有测试均标记为失败
        error_msg = f"Provider 加载失败: {type(e).__name__}: {e}"
        results = {
            m: {"passed": False, "latency_ms": 0.0, "error": error_msg}
            for m in ["resolve_name", "query_stock", "search", "get_today_statistics"]
        }
        return {"level": 1, "results": results, "all_passed": False}

    results: dict = {}

    # resolve_name("test", "material") → 需要 best_match, confident
    r = _run_single_test(provider, "resolve_name", ("test", "material"))
    results["resolve_name"] = _check_keys(r, {"best_match", "confident"}, "resolve_name")

    # query_stock("test") → 需要 success
    r = _run_single_test(provider, "query_stock", ("test",))
    results["query_stock"] = _check_keys(r, {"success"}, "query_stock")

    # search("test", "material", None, None, None, False) → 需要 success, items
    r = _run_single_test(provider, "search", ("test", "material", None, None, None, False))
    results["search"] = _check_keys(r, {"success", "items"}, "search")

    # get_today_statistics() → 需要 success, statistics
    r = _run_single_test(provider, "get_today_statistics", ())
    results["get_today_statistics"] = _check_keys(r, {"success", "statistics"}, "get_today_statistics")

    all_passed = all(v["passed"] for v in results.values())
    return {"level": 1, "results": results, "all_passed": all_passed}


def run_level2_tests(filepath: str, config: dict) -> dict:
    """运行 Level 2 写操作测试（2 个方法）。

    Args:
        filepath: Provider .py 文件的绝对路径。
        config:   Provider 初始化配置。

    Returns:
        {
            "level": 2,
            "results": {method_name: {"passed", "latency_ms", "error"}},
            "all_passed": bool,
        }
    """
    try:
        provider = load_provider_from_file(filepath, config)
    except Exception as e:
        error_msg = f"Provider 加载失败: {type(e).__name__}: {e}"
        results = {
            m: {"passed": False, "latency_ms": 0.0, "error": error_msg}
            for m in ["stock_in", "stock_out"]
        }
        return {"level": 2, "results": results, "all_passed": False}

    results: dict = {}

    # stock_in("test_item", 1, "API connectivity test", "system", False) → 需要 success
    r = _run_single_test(
        provider, "stock_in",
        ("test_item", 1, "API connectivity test", "system", False)
    )
    results["stock_in"] = _check_keys(r, {"success"}, "stock_in")

    # stock_out("test_item", 1, "API connectivity test", "system", False) → 需要 success
    r = _run_single_test(
        provider, "stock_out",
        ("test_item", 1, "API connectivity test", "system", False)
    )
    results["stock_out"] = _check_keys(r, {"success"}, "stock_out")

    all_passed = all(v["passed"] for v in results.values())
    return {"level": 2, "results": results, "all_passed": all_passed}
