import hashlib
import os
import tempfile
from pathlib import Path

import httpx
import numpy as np

from app.clip_tokenizer import SimpleTokenizer

MODEL_NAME = "clip32"
DIMENSIONS = 512
_tokenizer = SimpleTokenizer()
_session = None
_image_model = None
_image_preprocess = None
_torch = None


def item_text(item: dict) -> str:
    return f"{item.get('title') or ''}. {item.get('brand') or ''}".strip()


def brief_text(brief: dict) -> str:
    value = brief.get("text")
    if isinstance(value, str) and value.strip():
        return value
    parts = []
    for value in brief.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(part) for part in value)
    return " ".join(part for part in parts if part.strip())


def _model_path() -> Path:
    local_path = os.getenv("QUENYA_CLIP_TEXT_PATH")
    if local_path:
        path = Path(local_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"CLIP text model not found: {path}")
        return path
    url = os.getenv("QUENYA_CLIP_TEXT_URL")
    expected = os.getenv("QUENYA_CLIP_TEXT_SHA256", "").lower()
    if not url or len(expected) != 64:
        raise RuntimeError("configure QUENYA_CLIP_TEXT_PATH or URL and SHA256")
    path = Path(tempfile.gettempdir()) / f"quenya-clip-text-{expected}.onnx"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        return path
    temporary = path.with_suffix(".download")
    digest = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
                digest.update(chunk)
    if digest.hexdigest() != expected:
        temporary.unlink(missing_ok=True)
        raise ValueError("CLIP text model SHA256 mismatch")
    temporary.replace(path)
    return path


def _get_session():
    global _session
    if _session is None:
        import onnxruntime

        _session = onnxruntime.InferenceSession(
            str(_model_path()), providers=["CPUExecutionProvider"]
        )
    return _session


def encode_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, DIMENSIONS), dtype=np.float32)
    tokens = _tokenizer(texts, context_length=77)
    outputs = _get_session().run(["emb"], {"ids": tokens})
    vectors = np.asarray(outputs[0], dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def encode_images(images: list) -> np.ndarray:
    if not images:
        return np.zeros((0, DIMENSIONS), dtype=np.float32)
    global _image_model, _image_preprocess, _torch
    if _image_model is None:
        import open_clip
        import torch

        torch.set_num_threads(os.cpu_count() or 1)
        _image_model, _, _image_preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k", device="cpu"
        )
        _image_model.eval()
        _torch = torch

    from PIL import Image, ImageOps

    tensors = []
    for image in images:
        if isinstance(image, (str, Path)):
            with Image.open(image) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        else:
            image = ImageOps.exif_transpose(image).convert("RGB")
        tensors.append(_image_preprocess(image))
    with _torch.no_grad():
        vectors = _image_model.encode_image(_torch.stack(tensors))
        vectors = _torch.nn.functional.normalize(vectors, dim=-1).cpu().numpy()
    return np.asarray(vectors, dtype=np.float32)


def embed_item(item: dict) -> np.ndarray:
    return encode_texts([item_text(item)])[0]


def embed_brief(brief: dict) -> np.ndarray:
    return encode_texts([brief_text(brief)])[0]


def persist_item_text(item: dict) -> None:
    from app import db

    vector = embed_item(item)
    db.store_item_embedding(item["id"], MODEL_NAME, None, vector, vector)


def persist_profile_brief(profile_id: str, brief: dict) -> None:
    from app import db

    text = brief_text(brief)
    vector = embed_brief(brief)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db.store_profile_embedding(profile_id, MODEL_NAME, vector, digest)
