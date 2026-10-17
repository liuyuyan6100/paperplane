"""Resolve machine-local paths without tying projects to an agent workspace."""
import json
import os
from pathlib import Path


def default_data_dir(root):
    root = Path(root).resolve()
    configured = os.environ.get("PAPERPLANE_DATA_DIR")
    settings = root / ".paperplane-paths.json"
    if not configured and settings.is_file():
        configured = json.loads(settings.read_text(encoding="utf-8")).get("data_dir")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("本机数据目录配置必须使用绝对路径")
        return path.resolve()
    return root / "data"
