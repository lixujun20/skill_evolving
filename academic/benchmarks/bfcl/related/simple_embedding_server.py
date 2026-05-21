"""Small deterministic embedding endpoint for SkillX smoke/comparison runs.

This is not a substitute for the Qwen embedding service used by SkillX. It is
provided so the BFCL integration can run without SkillX's silent zero-vector
fallback when the local Qwen server is unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import re
from typing import Any, Dict, List

from fastapi import FastAPI
from pydantic import BaseModel


class EncodeRequest(BaseModel):
    texts: List[str] | None = None
    model: str | None = None


class OpenAIEmbeddingRequest(BaseModel):
    input: str | List[str]
    model: str | None = None


def _hash_embedding(text: str, dim: int) -> List[float]:
    vec = [0.0] * dim
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    if not tokens:
        tokens = [text.lower()]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        for offset in range(0, len(digest), 4):
            value = int.from_bytes(digest[offset : offset + 4], "little", signed=False)
            idx = value % dim
            sign = 1.0 if value & 1 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(item * item for item in vec)) or 1.0
    return [item / norm for item in vec]


def build_app(dim: int) -> FastAPI:
    app = FastAPI(title="Simple deterministic embedding service")

    @app.post("/encode")
    def encode(req: EncodeRequest) -> Dict[str, Any]:
        texts = req.texts or []
        return {
            "model": req.model or "hash-embedding",
            "embeddings": [_hash_embedding(text, dim) for text in texts],
        }

    @app.post("/v1/embeddings")
    def openai_embeddings(req: OpenAIEmbeddingRequest) -> Dict[str, Any]:
        texts = [req.input] if isinstance(req.input, str) else list(req.input)
        return {
            "object": "list",
            "model": req.model or "hash-embedding",
            "data": [
                {"object": "embedding", "index": idx, "embedding": _hash_embedding(text, dim)}
                for idx, text in enumerate(texts)
            ],
        }

    return app


app = build_app(dim=384)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic embedding server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--dim", type=int, default=384)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(build_app(dim=args.dim), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
