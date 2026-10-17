import io
import json
import re
import zipfile
from pathlib import Path


def validate(value):
    from .service import Problem, text_value
    if not isinstance(value, dict):
        raise Problem("小红书图文格式不正确")
    result = {key: text_value(value.get(key, ""), label, limit) for key, label, limit in (("title", "小红书标题", 100), ("caption", "小红书文案", 5000))}
    photos = value.get("photos", [])
    if not isinstance(photos, list) or len(photos) > 18 or any(not isinstance(p, str) or not re.fullmatch(r"/media/[a-f0-9]{32}\.(png|jpg|gif|webp)", p) for p in photos):
        raise Problem("小红书图片需从本地素材库选择，最多 18 张")
    if len(set(photos)) != len(photos):
        raise Problem("请勿重复选择同一张图片")
    pages = value.get("pages", [])
    if not isinstance(pages, list) or len(pages) > 12 or any(not isinstance(p, dict) for p in pages):
        raise Problem("图集分页格式不正确")
    if pages:
        pages = [{k: text_value(p.get(k, ""), "图集分页", limit, True)
                  for k, limit in (("title", 40), ("text", 220), ("visual", 1000))} for p in pages]
    # Keep legacy records unchanged when they have no pagination plan.
    return {**result, "photos": photos, **({"pages": pages} if pages else {})}


def package(job, data_dir):
    from .service import Problem
    note = validate(job['snapshot'].get('xhs_delivery') or job["snapshot"].get("xhs", {}))
    if not note["photos"]:
        raise Problem("小红书发布包没有图片")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        names = []
        for i, src in enumerate(note["photos"], 1):
            path = Path(data_dir) / "media" / Path(src).name
            if not path.is_file():
                raise Problem("发布图片缺失，请恢复原图后重新下载")
            name = f"images/{i:02d}{'-cover' if i == 1 else ''}{path.suffix}"
            z.write(path, name)
            names.append(name)
        z.writestr("title.txt", note["title"])
        z.writestr("caption.txt", note["caption"])
        z.writestr("manifest.json", json.dumps({"title": note["title"], "photos": names, "post_version": job["snapshot"]["version"]}, ensure_ascii=False, indent=2))
        z.writestr("README.txt", "小红书图文发布包\n1. 打开 https://creator.xiaohongshu.com/ 并登录。\n2. 创建图文笔记，按 images 文件编号顺序上传，01 为封面。\n3. 粘贴 title.txt 与 caption.txt，确认话题、裁切和平台提示。\n4. 在小红书实际发表，再回到工作台登记链接。\n排期只会生成待办，不会自动在小红书发表。\n")
    return out.getvalue()
