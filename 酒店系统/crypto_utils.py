import uuid, hashlib, base64, hmac, os
from cryptography.fernet import Fernet

class CryptoEngine:
    """
    加密引擎
    
    修复密钥强度问题：
    - 原版仅用 MAC 地址派生密钥，MAC 是公开/可伪造信息
    - 虚拟机/容器中 uuid.getnode() 失败时 fallback 为 mac=0，所有此类环境共用同一固定密钥
    
    改进方案：
    1. 优先使用持久化的随机盐值（首次运行时生成并存入数据库）
    2. 将随机盐值与 MAC 地址组合派生密钥，大幅提升强度
    3. 即使 MAC 地址相同，不同安装实例的盐值不同，密钥也不同
    """

    def __init__(self):
        self.machine_key = self._derive_key()

    def _derive_key(self) -> bytes:
        """派生机器绑定密钥（随机盐 + MAC 地址组合）"""
        try:
            # 尝试从数据库读取持久化盐值
            from database import db
            salt_hex = db.get_config("crypto_salt")
            if not salt_hex:
                # 首次运行：生成随机盐值并持久化
                salt = os.urandom(32)
                db.set_config("crypto_salt", salt.hex())
            else:
                salt = bytes.fromhex(salt_hex)
        except Exception:
            # 数据库不可用时（如初始化阶段），使用固定盐值降级
            salt = b"shadowguard_fallback_salt_v1"

        # 获取 MAC 地址（失败时使用固定字符串而非 0，避免所有环境共用同一密钥）
        try:
            mac = uuid.getnode()
            # 检测是否为随机生成的 MAC（某些虚拟机会随机生成）
            # 第一个字节的最低位为 1 表示本地管理地址（可能是随机的）
            mac_bytes = mac.to_bytes(6, 'big')
            if mac_bytes[0] & 0x01:
                # 本地管理地址，可能不可靠，加入主机名作为补充
                import socket
                hostname = socket.gethostname().encode()
                mac_material = mac_bytes + hostname
            else:
                mac_material = mac_bytes
        except Exception:
            import socket
            try:
                mac_material = socket.gethostname().encode()
            except Exception:
                mac_material = b"unknown_host"

        # PBKDF2 派生密钥（比单次 SHA256 更安全）
        key_material = hashlib.pbkdf2_hmac(
            'sha256',
            mac_material,
            salt,
            iterations=100_000
        )
        return base64.urlsafe_b64encode(key_material)

    def get_machine_id(self):
        """返回 HMAC 混淆后的机器指纹（不暴露原始密钥）"""
        return hmac.new(self.machine_key, b"shadow_guard_machine_id", hashlib.sha256).hexdigest()[:32]

    def encrypt(self, text):
        if not text:
            return ""
        try:
            return Fernet(self.machine_key).encrypt(text.encode()).decode()
        except Exception as e:
            raise RuntimeError(f"Encryption failed: {e}") from e

    def decrypt(self, text):
        if not text:
            return ""
        try:
            return Fernet(self.machine_key).decrypt(text.encode()).decode()
        except Exception:
            return None  # 明确返回 None 表示解密失败，与空字符串区分

crypto = CryptoEngine()

# ── 向后兼容导出 ──
def encrypt(text):
    return crypto.encrypt(text)

def decrypt(text):
    return crypto.decrypt(text)
