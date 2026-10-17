"""
异常和日志脱敏工具
用于防止敏感信息（如 API 密钥、Authorization 头等）泄露到日志或错误消息中
"""
import re
import logging
import traceback
from typing import Any


# 敏感字段的正则模式
SENSITIVE_PATTERNS = [
    # API 密钥和令牌
    (r'api[_-]?key["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]{20,})', 'api_key=***'),
    (r'token["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_.-]{20,})', 'token=***'),
    (r'secret["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]{20,})', 'secret=***'),

    # Authorization 头 - Bearer
    (r'authorization["\']?\s*[:=]\s*["\']?bearer\s+([a-zA-Z0-9_.-]+)', 'Authorization: Bearer ***'),
    # Authorization 头 - Basic
    (r'authorization["\']?\s*[:=]\s*["\']?basic\s+([a-zA-Z0-9+/=]+)', 'Authorization: Basic ***'),
    # Authorization 头 - 其他
    (r'authorization["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_.-]{20,})', 'Authorization: ***'),

    # 密码
    (r'password["\']?\s*[:=]\s*["\']?([^\s"\']{6,})', 'password=***'),
    (r'passwd["\']?\s*[:=]\s*["\']?([^\s"\']{6,})', 'passwd=***'),

    # 其他敏感字段
    (r'access[_-]?token["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_.-]{20,})', 'access_token=***'),
    (r'refresh[_-]?token["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_.-]{20,})', 'refresh_token=***'),
]


def sanitize_message(message: str) -> str:
    """
    脱敏错误消息，移除可能包含的敏感信息

    Args:
        message: 原始错误消息

    Returns:
        脱敏后的消息
    """
    if not message:
        return message

    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

    return sanitized


def sanitize_exception(exc: Exception) -> str:
    """
    将异常转换为安全的用户可见消息

    Args:
        exc: 异常对象

    Returns:
        脱敏后的错误描述
    """
    exc_type = type(exc).__name__
    exc_message = str(exc)

    # 脱敏异常消息
    sanitized = sanitize_message(exc_message)

    # 如果消息很长或包含堆栈信息，只返回类型
    if len(sanitized) > 200 or '\n' in sanitized:
        return f'{exc_type} 错误'

    # 返回脱敏后的消息
    if sanitized:
        return f'{exc_type}: {sanitized}'
    else:
        return f'{exc_type} 错误'


def sanitize_dict(data: dict) -> dict:
    """
    脱敏字典中的敏感字段

    Args:
        data: 原始字典

    Returns:
        脱敏后的字典副本
    """
    if not isinstance(data, dict):
        return data

    result = {}
    sensitive_keys = {
        'password', 'passwd', 'secret', 'token', 'api_key', 'apikey',
        'authorization', 'auth', 'access_token', 'refresh_token'
    }

    for key, value in data.items():
        key_lower = key.lower()

        # 检查 key 是否包含敏感关键词
        is_sensitive = any(sens in key_lower for sens in sensitive_keys)

        if is_sensitive:
            result[key] = '***'
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value)
        elif isinstance(value, str):
            result[key] = sanitize_message(value)
        else:
            result[key] = value

    return result


def sanitize_traceback(tb_str: str) -> str:
    """
    脱敏 traceback 字符串中的敏感信息

    Args:
        tb_str: traceback 字符串

    Returns:
        脱敏后的 traceback
    """
    if not tb_str:
        return tb_str

    sanitized = tb_str
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

    return sanitized


class SanitizingFilter(logging.Filter):
    """
    日志过滤器，自动脱敏所有日志消息和异常信息
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤并脱敏日志记录

        Args:
            record: 日志记录

        Returns:
            始终返回 True（不过滤，只修改）
        """
        # 脱敏日志消息
        # Render first: credentials can be split across a format string and args.
        record.msg = sanitize_message(record.getMessage())
        record.args = ()

        # 脱敏异常信息
        if record.exc_info:
            # 格式化异常信息并脱敏
            if record.exc_text is None:
                record.exc_text = self.format_exception(record.exc_info)
            else:
                record.exc_text = sanitize_traceback(record.exc_text)
            # Custom handlers must not reformat the original sensitive exception.
            record.exc_info = None

        return True

    def format_exception(self, exc_info) -> str:
        """
        格式化并脱敏异常信息

        Args:
            exc_info: 异常信息元组 (type, value, traceback)

        Returns:
            脱敏后的异常字符串
        """
        # 格式化异常
        tb_lines = traceback.format_exception(*exc_info)
        tb_str = ''.join(tb_lines)

        # 脱敏
        return sanitize_traceback(tb_str)


def install_sanitizing_filter():
    """
    为根日志记录器安装脱敏过滤器
    """
    # Ancestor logger filters do not run for propagated records. Install on each
    # producer, including before handlers are added (e.g. pytest or desktop logs).
    for name in ('studio.skills', 'studio.skill_executors', 'studio.workflows',
                 'studio.prompt_loader'):
        producer = logging.getLogger(name)
        if not any(isinstance(f, SanitizingFilter) for f in producer.filters):
            producer.addFilter(SanitizingFilter())

