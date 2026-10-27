"""Explicit host-user credential storage; never write plaintext secrets."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import tempfile


def atomic_write(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("设置文件不能是符号链接。")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.chmod(temporary, 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class LocalSecretStore:
    """DPAPI ciphertext on Windows; unsupported platforms keep keys in memory."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def supported(self) -> bool:
        return os.name == "nt" and hasattr(ctypes, "WinDLL")

    @property
    def name(self) -> str:
        return "Windows DPAPI（当前服务账户）" if self.supported else "仅服务内存"

    @staticmethod
    def _crypt(content: bytes, *, decrypt: bool) -> bytes:
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(content)
        incoming = Blob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        if decrypt:
            operation = crypt32.CryptUnprotectData
            operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
            arguments = (ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
        else:
            operation = crypt32.CryptProtectData
            operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
            arguments = (ctypes.byref(incoming), "SparkTutor Web credentials", None, None, None,
                         1, ctypes.byref(outgoing))
        operation.restype = wintypes.BOOL
        if not operation(*arguments):
            raise OSError("本机安全存储未能完成加密或解密。")
        try:
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))

    def load(self) -> dict:
        if not self.supported or not self.path.is_file():
            return {}
        if self.path.is_symlink():
            raise ValueError("密钥文件不能是符号链接。")
        content = self.path.read_bytes()
        if len(content) > 256 * 1024:
            raise ValueError("密钥文件格式无效。")
        result = json.loads(self._crypt(content, decrypt=True))
        if not isinstance(result, dict):
            raise ValueError("密钥文件格式无效。")
        return result

    def save(self, secrets: dict) -> bool:
        if not self.supported:
            return False
        content = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
        atomic_write(self.path, self._crypt(content, decrypt=False))
        return True

    def delete(self) -> None:
        if self.path.is_symlink():
            raise ValueError("密钥文件不能是符号链接。")
        self.path.unlink(missing_ok=True)
