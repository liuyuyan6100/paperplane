"""Reusable process screenshots and clearly labelled illustrative diagrams."""
import json
import re

from .db import now


def metadata(values):
    from .service import Problem, text_value
    title = text_value(values.get("title", "未命名图片"), "图片名称", 150, True)
    collection = text_value(values.get("collection", ""), "问题 / 项目", 100)
    description = text_value(values.get("description", ""), "图片说明", 2000)
    step = values.get("step", 0)
    if type(step) is not int or not 0 <= step <= 9999:
        raise Problem("步骤编号需要为 0–9999 的整数")
    origin = values.get("origin", "upload")
    if origin not in ("upload", "ai-diagram", "imported", "text-card", "generated"):
        raise Problem("无效的图片来源")
    return {"title": title, "collection": collection, "description": description, "step": step, "origin": origin}


def index_existing(app):
    folder = app.db.data_dir / "media"
    if not folder.exists():
        return
    with app.db.connect() as db:
        for file in folder.iterdir():
            if file.is_file() and re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|gif|webp)", file.name):
                db.execute("INSERT OR IGNORE INTO assets(id,url,title,origin,created_at) VALUES(?,?,?,?,?)", (file.stem, "/media/" + file.name, "历史图片 " + file.stem[:8], "imported", now()))


def listing(app):
    rows = app.db.rows("SELECT * FROM assets ORDER BY collection,step,created_at")
    posts = app.db.rows("SELECT id,title,body,cover,xhs FROM posts")
    for asset in rows:
        asset["used_by"] = [{"id": p["id"], "title": p["title"]} for p in posts if asset["url"] in p["body"] or asset["url"] == p["cover"] or asset["url"] in json.loads(p["xhs"]).get("photos", [])]
    return {"assets": rows}


def update(app, asset_id, values):
    from .service import Problem
    with app.db.connect(immediate=True) as db:
        old = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not old:
            raise Problem("图片不存在", 404)
        if "archived" in values and type(values["archived"]) is not bool:
            raise Problem("归档状态无效")
        fields = metadata({**dict(old), **values, "origin": old["origin"]})
        db.execute("UPDATE assets SET title=?,collection=?,step=?,description=?,archived=? WHERE id=?", (fields["title"], fields["collection"], fields["step"], fields["description"], int(values.get("archived", old["archived"])), asset_id))
    return {"ok": True}


def diagram(app, post, instruction):
    from .service import Problem
    raw = app.generate_draft({**post, '_model_task': 'diagram'}, instruction + '\n请仅依据素材整理流程示意图。不得伪造实际操作结果或截图。只输出 JSON 对象（不要解释）：{"title":"不超过40字","steps":[{"title":"不超过24字","detail":"不超过80字"}]}。steps 为2到6步。没有实际结果时写待验证。')
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("title"), str) or not 1 <= len(result["title"]) <= 40:
            raise ValueError()
        steps = result.get("steps")
        if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
            raise ValueError()
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("title"), str) or not 1 <= len(step["title"]) <= 24 or not isinstance(step.get("detail"), str) or len(step["detail"]) > 80:
                raise ValueError()
        return {"title": result["title"], "steps": [{"title": s["title"], "detail": s["detail"]} for s in steps]}
    except (ValueError, TypeError):
        raise Problem("模型返回的流程格式不完整，请缩小主题后重试；未生成或保存图片", 502) from None
