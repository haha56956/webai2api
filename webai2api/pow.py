from __future__ import annotations

import base64
import ctypes
import fcntl
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("webai2api.pow")

MASK = 0xFFFFFFFFFFFFFFFF
RATE = 136

RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
PI = [0, 6, 12, 18, 24, 3, 9, 10, 16, 22, 1, 7, 13, 19, 20, 4, 5, 11, 17, 23, 2, 8, 14, 15, 21]
RHO = [0, 44, 43, 21, 14, 28, 20, 3, 45, 61, 1, 6, 25, 8, 18, 27, 36, 10, 15, 56, 62, 55, 39, 41, 2]


class PowError(RuntimeError):
    pass


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & MASK


def _keccak_f23(state: list[int]) -> None:
    a = list(state)
    for r in range(1, 24):
        c = [(a[i] ^ a[i + 5] ^ a[i + 10] ^ a[i + 15] ^ a[i + 20]) & MASK for i in range(5)]
        d = [(c[(i + 4) % 5] ^ _rotl(c[(i + 1) % 5], 1)) & MASK for i in range(5)]
        for i in range(5):
            for j in range(0, 25, 5):
                a[i + j] = (a[i + j] ^ d[i]) & MASK
        b = [_rotl(a[PI[i]], RHO[i]) for i in range(25)]
        for j in range(5):
            for i in range(5):
                a[j * 5 + i] = (
                    b[j * 5 + i] ^ ((~b[j * 5 + (i + 1) % 5] & MASK) & b[j * 5 + (i + 2) % 5])
                ) & MASK
        a[0] = (a[0] ^ RC[r]) & MASK
    state[:] = a


def deepseek_hash(data: bytes) -> bytes:
    state = [0] * 25
    offset = 0
    length = len(data)
    while offset + RATE <= length:
        for i in range(RATE // 8):
            state[i] ^= struct.unpack_from("<Q", data, offset + i * 8)[0]
        _keccak_f23(state)
        offset += RATE
    buffer = bytearray(RATE)
    remaining = length - offset
    buffer[:remaining] = data[offset:]
    buffer[remaining] ^= 0x06
    buffer[RATE - 1] ^= 0x80
    for i in range(RATE // 8):
        state[i] ^= struct.unpack_from("<Q", buffer, i * 8)[0]
    _keccak_f23(state)
    return b"".join(struct.pack("<Q", state[i]) for i in range(4))


def hash_hex(data: bytes) -> str:
    return deepseek_hash(data).hex()


def solve_pow_python(prefix: bytes, target: bytes, difficulty: int) -> Optional[int]:
    for nonce in range(max(0, int(difficulty))):
        if deepseek_hash(prefix + str(nonce).encode("ascii")) == target:
            return nonce
    return None


class NativePow:
    def __init__(self, source: Optional[Path] = None, cache_dir: Optional[Path] = None):
        self.source = source or (Path(__file__).parent / "native" / "pow.c")
        base_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "webai2api"
        self.cache_dir = cache_dir or base_cache
        self._lib: Optional[ctypes.CDLL] = None
        self._lock = threading.Lock()
        self._failed = False

    def _library_path(self) -> Path:
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()[:16]
        machine = os.uname().machine if hasattr(os, "uname") else "unknown"
        return self.cache_dir / f"libdspow-{digest}-{machine}.so"

    def _compile(self, target: Path) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if target.exists():
                return
            compilers = [os.environ.get("CC"), "cc", "gcc", "clang"]
            errors: list[str] = []
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            for compiler in compilers:
                if not compiler:
                    continue
                if not shutil.which(compiler):
                    continue
                cmd = [
                    compiler, "-O3", "-fPIC", "-shared", "-std=c99",
                    "-o", str(tmp), str(self.source),
                ]
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=60, check=False
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    errors.append(f"{compiler}: {exc}")
                    continue
                if result.returncode == 0 and tmp.exists():
                    tmp.replace(target)
                    return
                errors.append(f"{compiler}: {result.stderr.strip()[:300]}")
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise PowError("无法编译 PoW C 加速模块: " + " | ".join(errors))

    def get(self) -> Optional[ctypes.CDLL]:
        if self._failed:
            return None
        if self._lib is not None:
            return self._lib
        with self._lock:
            if self._lib is not None:
                return self._lib
            if self._failed:
                return None
            if not self.source.exists():
                log.warning("PoW C 源码不存在，使用纯 Python 求解")
                self._failed = True
                return None
            target = self._library_path()
            try:
                if not target.exists():
                    log.info("首次编译 DeepSeek PoW 加速模块: %s", target)
                    self._compile(target)
                lib = ctypes.CDLL(str(target))
                lib.dspow_solve.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_uint64,
                    ctypes.POINTER(ctypes.c_ubyte),
                    ctypes.c_uint64,
                    ctypes.POINTER(ctypes.c_uint64),
                ]
                lib.dspow_solve.restype = ctypes.c_int
                self._lib = lib
                return lib
            except Exception as exc:
                log.warning("PoW C 加速不可用，回退纯 Python: %s", exc)
                self._failed = True
                return None

    def solve(self, prefix: bytes, target: bytes, difficulty: int) -> Optional[int]:
        lib = self.get()
        if lib is None:
            return None
        target_array = (ctypes.c_ubyte * 32).from_buffer_copy(target)
        out = ctypes.c_uint64(0)
        status = lib.dspow_solve(
            prefix,
            len(prefix),
            target_array,
            int(difficulty),
            ctypes.byref(out),
        )
        if status == 1:
            return int(out.value)
        if status == 0:
            return None
        raise PowError(f"PoW C 求解器返回错误码 {status}")


_native = NativePow()


def solve_pow(
    salt: str,
    expire_at: int | str,
    challenge: str,
    difficulty: int,
    backend: str = "auto",
) -> int:
    prefix = f"{salt}_{expire_at}_".encode("utf-8")
    try:
        target = bytes.fromhex(challenge)
    except ValueError as exc:
        raise PowError("challenge 不是合法的十六进制字符串") from exc
    if len(target) != 32:
        raise PowError("challenge 必须是 32 字节 SHA3-256 摘要")
    difficulty = int(difficulty)
    if difficulty <= 0:
        raise PowError("difficulty 必须大于 0")
    backend = (backend or "auto").lower()
    if backend in ("auto", "c", "native"):
        answer = _native.solve(prefix, target, difficulty)
        if answer is not None:
            return answer
        if backend in ("c", "native"):
            raise PowError("C PoW 求解器不可用")
    log.info("使用纯 Python 求解 PoW（difficulty=%d），可能需要几十秒", difficulty)
    answer = solve_pow_python(prefix, target, difficulty)
    if answer is None:
        raise PowError(f"在 difficulty={difficulty} 范围内未找到 PoW 答案")
    return answer


def build_pow_response(challenge: dict, answer: int) -> str:
    response = {
        "algorithm": challenge.get("algorithm", "DeepSeekHashV1"),
        "challenge": challenge["challenge"],
        "salt": challenge["salt"],
        "answer": answer,
        "signature": challenge.get("signature", ""),
        "target_path": challenge.get("target_path", "/api/v0/chat/completion"),
    }
    raw = json.dumps(response, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")
