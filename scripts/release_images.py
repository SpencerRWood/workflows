"""Validate declared matrix images; one release identity, no new output profile."""

import json
import re
from pathlib import Path


def image_matrix(document: str) -> list[dict]:
    images = json.loads(document)
    if not isinstance(images, list) or not 1 <= len(images) <= 16:
        raise ValueError("images must declare between one and sixteen images")
    names = set()
    for image in images:
        if not isinstance(image, dict) or set(image) != {"image_name", "dockerfile", "context"}:
            raise ValueError("each image must declare image_name, dockerfile and context")
        name = image["image_name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", name):
            raise ValueError("image_name must be a lowercase GHCR image name")
        if name in names:
            raise ValueError("duplicate image_name")
        names.add(name)
        for field in ("dockerfile", "context"):
            value = image[field]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
                raise ValueError("image paths must be repository-relative")
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or value.startswith("-"):
                raise ValueError("image paths must be repository-relative")
        if not Path(image["dockerfile"]).is_file() or not Path(image["context"]).is_dir():
            raise ValueError("declared Dockerfile or context is missing")
    return images
