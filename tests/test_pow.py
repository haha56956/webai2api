import unittest

from webai2api.crypto import aes128_cbc_encrypt, encrypt_block
from webai2api.pow import build_pow_response, deepseek_hash, hash_hex, solve_pow

POW_VECTORS = [
    ("b4904764f21e49261b7e8aa1cca16f32451d54ebcd65d13037083f0c4b4d31b0", "865fc16c1f6c7b648547", "1784018472563", 105512),
    ("162ae658ec2439f6928fd90100f31f34dc6980fac9ab4a8fdce00d0c6c8d8f62", "b8976b2c5d35e7e35226", "1785812947139", 77969),
    ("7a5e99100c816f6fab9c41ada2f51a9ccc29162577f10271c926e6552d834f1d", "39561318d48872a2681f", "1785813014617", 53262),
    ("d2715747e6ccc5f185f3cf191b9d89560a355e5e2c5e75dcba2ebe22cf04924c", "abb846a350721e06d61f", "1785813071975", 13419),
]


class PowTests(unittest.TestCase):
    def test_hash_vectors(self):
        for target, salt, expire_at, nonce in POW_VECTORS:
            data = f"{salt}_{expire_at}_".encode() + str(nonce).encode()
            self.assertEqual(hash_hex(data), target)
            self.assertEqual(deepseek_hash(data).hex(), target)

    def test_solve_vector(self):
        target, salt, expire_at, nonce = POW_VECTORS[3]
        self.assertEqual(solve_pow(salt, expire_at, target, nonce + 1), nonce)

    def test_pow_response(self):
        import base64
        import json

        challenge = {
            "algorithm": "DeepSeekHashV1",
            "challenge": POW_VECTORS[0][0],
            "salt": POW_VECTORS[0][1],
            "signature": "sig",
            "target_path": "/api/v0/chat/completion",
        }
        raw = base64.b64decode(build_pow_response(challenge, 105512))
        parsed = json.loads(raw)
        self.assertEqual(parsed["answer"], 105512)
        self.assertEqual(parsed["signature"], "sig")
        self.assertEqual(parsed["algorithm"], "DeepSeekHashV1")


class CryptoTests(unittest.TestCase):
    def test_aes_block_vector(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.assertEqual(
            encrypt_block(key, plaintext).hex(),
            "69c4e0d86a7b0430d8cdb78070b4c55a",
        )

    def test_aes_cbc_roundtrip_with_openssl_vector(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        iv = bytes(range(16))
        data = b"hello world, this is a test!"
        self.assertEqual(
            aes128_cbc_encrypt(key, iv, data).hex(),
            "ef8d76c860d5b1820730209d3d3ec24a5f2efd262b1d58b08511e836ca4d47f6",
        )
