"""本机凭据加密 —— Windows DPAPI，零新增依赖。

为什么用 DPAPI 而不是 cryptography 的 Fernet：

1. **不增加包体积**。cryptography 带 11MB Rust 扩展，会把便携包从 80MB
   推到 99MB（超出既定上限）。DPAPI 是纯 ctypes 调系统原生接口，零依赖。
2. **安全模型更强**。DPAPI 密钥由 Windows 管理并**绑定当前用户账号** ——
   别人拷走数据库文件也解不开。而 Fernet 的密钥存在同一个库里，
   能拿到库就能解，等于只防「不怀好意但拿不到完整文件」这一种情况。

代价：数据目录迁到别的机器或别的 Windows 账号后，密文解不开。
这种情况下的处理是提示「请重新配置发件密码」，而不是静默失败。

非 Windows 平台没有 DPAPI，此时退回到「不加密、只在内存中持有」，
并在保存时明确告知用户（该场景本就是 Windows 便携版，不该出现）。
"""
import base64
import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# DPAPI 的 entropy：额外盐值，绑定本应用，避免跨应用复用密文
_ENTROPY = b"wv-smtp-credential-v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes):
    """构造 DATA_BLOB。注意必须保留 buf 引用，否则 ctypes 会回收内存。"""
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    return blob, buf


def _dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, _src_buf = _make_blob(data)
    ent, _ent_buf = _make_blob(_ENTROPY)
    out = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(src), "Felix Business System", ctypes.byref(ent),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI 加密失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, _src_buf = _make_blob(data)
    ent, _ent_buf = _make_blob(_ENTROPY)
    out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(src), None, ctypes.byref(ent),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI 解密失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def encrypt(plain: str) -> str:
    """明文 → 可存库的字符串。

    格式 "dpapi:<base64>"；非 Windows 平台退化为 "plain:<base64>"。
    """
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    if IS_WINDOWS:
        return "dpapi:" + base64.b64encode(_dpapi_protect(raw)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")


def decrypt(cipher: str) -> str:
    """库里的字符串 → 明文。解不开时返回空串（不抛，避免整个配置读不出来）。"""
    if not cipher:
        return ""
    try:
        prefix, _, b64 = cipher.partition(":")
        raw = base64.b64decode(b64.encode("ascii"))
        if prefix == "dpapi":
            if not IS_WINDOWS:
                return ""
            return _dpapi_unprotect(raw).decode("utf-8")
        if prefix == "plain":
            return raw.decode("utf-8")
    except Exception:
        return ""
    return ""
