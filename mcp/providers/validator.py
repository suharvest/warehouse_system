"""Provider 安全扫描与结构验证

上传的第三方 Provider .py 文件在加载前必须通过本模块校验：
1. AST 安全扫描：禁止危险模块导入和危险函数调用
2. 结构验证：必须包含 BaseProvider 子类，并实现全部 6 个抽象方法
"""

import ast
import os
from typing import Optional

# ── 安全规则 ──

# 禁止导入的危险模块
_BLOCKED_IMPORTS = {
    "os", "subprocess", "sys", "shutil", "socket",
    "ctypes", "code", "codeop",
}

# 允许导入的白名单模块（不在此列表也可能允许，但黑名单优先）
_ALLOWED_IMPORTS = {
    "requests", "json", "datetime", "logging", "hashlib",
    "hmac", "base64", "urllib", "time", "re", "typing",
    "abc", "dataclasses",
}

# 禁止调用/使用的危险内置
_BLOCKED_BUILTINS = {"eval", "exec", "__import__", "compile", "open"}

# 必须实现的 6 个核心方法（BaseProvider 中的 @abstractmethod）。
# 注意：BaseProvider 上的 query_batch / move_batch_location 是**可选扩展**，
# 提供了 not_implemented 默认实现，不在这里强制要求——第三方 provider 可以
# 不实现，调用时会返回结构化的 not_implemented 失败响应。
_REQUIRED_METHODS = {
    "resolve_name", "query_stock", "stock_in",
    "stock_out", "search", "get_today_statistics",
}

# 文件大小上限：100KB
_MAX_FILE_SIZE = 100 * 1024


def _check_imports(tree: ast.AST) -> list[str]:
    """扫描 import 语句，返回所有违规错误描述列表。"""
    errors = []
    for node in ast.walk(tree):
        # import xxx 形式
        if isinstance(node, ast.Import):
            for alias in node.names:
                # 取顶层模块名（如 os.path → os）
                top = alias.name.split(".")[0]
                if top in _BLOCKED_IMPORTS:
                    errors.append(f"禁止导入危险模块: '{alias.name}'")
        # from xxx import yyy 形式
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _BLOCKED_IMPORTS:
                    errors.append(f"禁止导入危险模块: '{node.module}'")
    return errors


def _check_dangerous_calls(tree: ast.AST) -> list[str]:
    """扫描函数调用，拒绝危险内置的直接调用。"""
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # 直接调用形式：eval(...)、exec(...)
            if isinstance(node.func, ast.Name):
                if node.func.id in _BLOCKED_BUILTINS:
                    errors.append(f"禁止使用危险函数: '{node.func.id}()'")
            # 属性调用形式：builtins.eval(...) 等（防绕过）
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in _BLOCKED_BUILTINS:
                    errors.append(f"禁止使用危险函数: '*.{node.func.attr}()'")
    return errors


def _find_provider_class(tree: ast.AST) -> tuple[Optional[ast.ClassDef], list[str]]:
    """在 AST 中查找继承自 BaseProvider 的类。

    返回 (class_node, errors)。
    若找到唯一合法类则 errors 为空，否则 class_node 为 None。
    """
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                # 处理 class Foo(BaseProvider) 和 class Foo(base.BaseProvider) 两种形式
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "BaseProvider":
                    candidates.append(node)
                    break

    if len(candidates) == 0:
        return None, ["未找到继承自 BaseProvider 的类"]
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        return None, [f"只允许定义一个 BaseProvider 子类，但找到多个: {names}"]

    return candidates[0], []


def _extract_provider_name(cls_node: ast.ClassDef) -> Optional[str]:
    """从类 AST 节点中提取 PROVIDER_NAME 字符串常量值。"""
    for node in ast.walk(cls_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PROVIDER_NAME":
                    # Python 3.8+ 用 ast.Constant；旧版用 ast.Str
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
                    # 兼容旧语法
                    if isinstance(node.value, ast.Str):  # type: ignore[attr-defined]
                        return node.value.s  # type: ignore[attr-defined]
    return None


def _extract_methods(cls_node: ast.ClassDef) -> list[str]:
    """返回类中直接定义的方法名列表（不递归内部类）。"""
    methods = []
    for node in cls_node.body:
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            methods.append(node.name)
    return methods


def validate_provider_file(filepath: str) -> dict:
    """对上传的 Provider .py 文件进行安全扫描和结构验证。

    Args:
        filepath: Provider 文件的绝对路径。

    Returns:
        {
            "valid": bool,
            "provider_name": str | None,   # PROVIDER_NAME 值
            "class_name": str | None,      # 类名
            "methods": list[str],          # 已找到的方法列表
            "errors": list[str],           # 所有错误描述（valid=False 时非空）
        }
    """
    errors: list[str] = []
    provider_name: Optional[str] = None
    class_name: Optional[str] = None
    found_methods: list[str] = []

    # ── 1. 文件大小检查 ──
    try:
        file_size = os.path.getsize(filepath)
    except OSError as e:
        return {
            "valid": False,
            "provider_name": None,
            "class_name": None,
            "methods": [],
            "errors": [f"无法读取文件: {e}"],
        }

    if file_size > _MAX_FILE_SIZE:
        errors.append(
            f"文件大小 {file_size} 字节超过上限 {_MAX_FILE_SIZE} 字节（100KB）"
        )

    # ── 2. 读取源码并解析 AST ──
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        return {
            "valid": False,
            "provider_name": None,
            "class_name": None,
            "methods": [],
            "errors": [f"读取文件失败: {e}"],
        }

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return {
            "valid": False,
            "provider_name": None,
            "class_name": None,
            "methods": [],
            "errors": [f"Python 语法错误: {e}"],
        }

    # ── 3. 安全扫描 ──
    errors.extend(_check_imports(tree))
    errors.extend(_check_dangerous_calls(tree))

    # ── 4. 结构验证 ──
    cls_node, struct_errors = _find_provider_class(tree)
    errors.extend(struct_errors)

    if cls_node is not None:
        class_name = cls_node.name

        # 检查 PROVIDER_NAME
        provider_name = _extract_provider_name(cls_node)
        if not provider_name:
            errors.append("类属性 PROVIDER_NAME 未设置或不是非空字符串")

        # 检查 6 个必须方法
        found_methods = _extract_methods(cls_node)
        missing = _REQUIRED_METHODS - set(found_methods)
        if missing:
            errors.append(
                f"缺少必须实现的方法: {', '.join(sorted(missing))}"
            )

    return {
        "valid": len(errors) == 0,
        "provider_name": provider_name,
        "class_name": class_name,
        "methods": found_methods,
        "errors": errors,
    }
