"""Downloads the embedding model for vector retrieval into models/ (ignored by git).

Pinned revision and SHA-256 hashes (docs/PROGRESS.md, step 8b). A file whose
hash differs is deleted and the script fails.
"""

import hashlib
import pathlib
import sys
import urllib.request

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL_DIRECTORY = REPOSITORY_ROOT / "models" / "all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
BASE_URL = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{REVISION}/"
FILES = {
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
}


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for remote_path, expected_hash in FILES.items():
        local_path = MODEL_DIRECTORY / pathlib.PurePosixPath(remote_path).name
        if not local_path.exists() or sha256_of(local_path) != expected_hash:
            print(f"downloading {remote_path}", flush=True)
            urllib.request.urlretrieve(BASE_URL + remote_path, local_path)
        if sha256_of(local_path) != expected_hash:
            local_path.unlink()
            print(f"error: {remote_path} does not match its pinned hash", file=sys.stderr)
            return 1
    print(f"model ready in {MODEL_DIRECTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
