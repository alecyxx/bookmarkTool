"""密码哈希与验证（BM-V1-201/205）。

- 使用 Argon2id；
- 参数集中配置（PH = 中等安全参数）；
- 登录成功时允许按当前参数透明升级 Hash（rehash 语义由调用方处理）。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# 时间成本/内存成本/并行度：单用户低频认证场景采用偏保守参数
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    """生成 Argon2id 哈希。口令为空由业务层拒绝，不在此处理。"""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证口令。任何哈希异常（损坏/算法不符）一律视为验证失败。"""
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """哈希参数落后于当前配置时返回 True（登录成功后应升级存储）。"""
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False
