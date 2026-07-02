"""
Solid PMS — 统一异常体系

所有业务异常继承自 SolidError，便于上层统一 catch 和优雅降级。
"""


class SolidError(Exception):
    """Solid PMS 所有异常的基类。"""


class DatabaseError(SolidError):
    """数据库操作失败。"""


class ValidationError(SolidError):
    """输入校验失败（前端可用此做用户提示）。"""


class BusinessRuleError(SolidError):
    """业务规则冲突（如时间冲突、状态不允许操作）。"""


class InsufficientStockError(BusinessRuleError):
    """库存不足。"""


class LockError(SolidError):
    """门锁操作失败（发卡、读卡、初始化）。"""


class AuthenticationError(SolidError):
    """认证/授权失败。"""


class ConfigurationError(SolidError):
    """系统配置缺失或非法。"""


class CloudServiceError(SolidError):
    """云端服务通信失败。"""


class ImportError_(SolidError):
    """可选的第三方依赖未安装时抛此异常（避免直接 raise ImportError 导致冲突）。"""
    pass