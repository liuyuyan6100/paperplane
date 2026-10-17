"""Owner-managed editorial profiles; each request uses one explicit snapshot."""
import json
import re

from .db import dumps, now, uid

FIELDS = {
    "name": ("画像名称", 80), "direction": ("定位与内容方向", 2000),
    "audience": ("目标读者", 2000), "goal": ("运营目标", 2000),
    "style": ("表达风格", 2000), "preferences": ("偏好与红线", 3000),
    "platforms": ("运营平台", 1000), "links": ("社媒主页链接", 2000),
    "memory": ("运营经验与反馈", 3000),
    "wechat_footer": ("公众号文末署名与入口", 2000),
    "visual_style": ("视觉风格", 3000),
}


def validate_id(value):
    from .service import Problem
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise Problem("画像编号格式不正确")
    return value


def decode(row):
    content = json.loads(row['content'])
    # Compatibility for an explicitly saved footer before the dedicated field existed.
    if 'wechat_footer' not in content:
        match = re.search(r'(?:^|\n)公众号文末署名与入口：([^\n]+)', content.get('preferences', ''))
        content['wechat_footer'] = match[1].strip() if match else ''
    return {**content, **{key: row[key] for key in
            ("id", "name", "version", "created_at", "updated_at")}}


def get(app, profile_id, db=None):
    from .service import Problem
    validate_id(profile_id)
    row = (db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
           if db is not None else app.db.one("SELECT * FROM profiles WHERE id=?", (profile_id,)))
    if row is None:
        raise Problem("画像不存在，请重新选择", 404)
    return decode(row)


def listing(app):
    return [decode(row) for row in app.db.rows("SELECT * FROM profiles ORDER BY updated_at DESC,rowid DESC")]


def record_revision(db, profile, action, details=None):
    db.execute('INSERT OR IGNORE INTO profile_revisions VALUES(?,?,?,?,?,?)',
               (profile['id'], profile['version'], dumps(profile), action, now(), dumps(details or {})))


def _save(app, db, values, profile_id=None, *, action=None, details=None, new_id=None):
    from .service import Problem, text_value
    if not isinstance(values, dict):
        raise Problem("画像格式不正确")
    old = get(app, profile_id, db) if profile_id else None
    if old and (type(values.get("version")) is not int or values["version"] != old["version"]):
        raise Problem("画像已在其他页面修改，请保留当前内容并重新打开后合并", 409)
    content = {key: text_value(values.get(key, old.get(key, "") if old else ""), label, limit,
                               required=key == "name") for key, (label, limit) in FIELDS.items()}
    from .illustrations import reference_urls
    content['visual_references'] = reference_urls(app, values.get('visual_references', old.get('visual_references', []) if old else []))
    if db.execute("SELECT id FROM profiles WHERE name=? AND id!=?", (content["name"], profile_id or "")).fetchone():
        raise Problem("已有同名画像，请使用便于区分的名称", 409)
    stamp = now()
    result = {**content, "id": profile_id or new_id or uid(), "version": old["version"] + 1 if old else 1,
              "created_at": old["created_at"] if old else stamp, "updated_at": stamp}
    if old:
        record_revision(db, old, 'baseline')
    db.execute("INSERT INTO profiles(id,name,content,version,created_at,updated_at) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET name=excluded.name,content=excluded.content,"
               "version=excluded.version,updated_at=excluded.updated_at",
               (result["id"], result["name"], dumps(content), result["version"], result["created_at"], stamp))
    record_revision(db, result, action or ('edit' if old else 'create'), details)
    return result


def save(app, values, profile_id=None):
    with app.db.connect(immediate=True) as db:
        return _save(app, db, values, profile_id)


def history(app, profile_id):
    with app.db.connect() as db:
        db.execute('BEGIN')
        current = get(app, profile_id, db)
        rows = db.execute('SELECT version,action,at,details FROM profile_revisions WHERE profile_id=? ORDER BY version DESC', (profile_id,))
        return {'profile': current, 'revisions': [{**dict(r), 'details': json.loads(r['details'])} for r in rows]}


def _revision(db, profile_id, version):
    from .service import Problem
    if type(version) is not int or version < 1:
        raise Problem('请选择有效画像版本')
    row = db.execute('SELECT * FROM profile_revisions WHERE profile_id=? AND version=?', (profile_id, version)).fetchone()
    if not row:
        raise Problem('画像历史版本不存在；升级前未保存的版本不能恢复', 404)
    return {**dict(row), 'snapshot': json.loads(row['snapshot']), 'details': json.loads(row['details'])}


def revision(app, profile_id, version):
    with app.db.connect() as db:
        db.execute('BEGIN')
        current = get(app, profile_id, db)
        selected = _revision(db, profile_id, version)
        fields = {**FIELDS, 'visual_references': ('参考图', 4)}
        changes = [{'field': k, 'label': definition[0], 'current': current.get(k, [] if k == 'visual_references' else ''),
                    'selected': selected['snapshot'].get(k, [] if k == 'visual_references' else '')}
                   for k, definition in fields.items()
                   if current.get(k, [] if k == 'visual_references' else '') != selected['snapshot'].get(k, [] if k == 'visual_references' else '')]
        return {'current': current, 'revision': selected, 'changes': changes}


def restore(app, profile_id, values):
    from .service import Problem
    if not isinstance(values, dict):
        raise Problem('画像恢复参数不正确')
    with app.db.connect(immediate=True) as db:
        current = get(app, profile_id, db)
        if type(values.get('version')) is not int or values['version'] != current['version']:
            raise Problem('画像已有新修改，请重新查看差异后恢复', 409)
        selected = _revision(db, profile_id, values.get('target_version'))['snapshot']
        selected_fields = {key: selected.get(key, '') for key in FIELDS}
        selected_fields['visual_references'] = selected.get('visual_references', [])
        keys = [*FIELDS, 'visual_references']
        if all(current.get(k, [] if k == 'visual_references' else '') == selected_fields[k] for k in keys):
            raise Problem('此版本与当前画像内容相同，无需恢复')
        return _save(app, db, {**selected_fields, 'version': current['version']}, profile_id,
                     action='restore', details={'restored_from_version': selected['version']})


def duplicate(app, profile_id, values):
    """One-time independent copy; account links and experience never carry over."""
    from .service import Problem, text_value
    if not isinstance(values, dict):
        raise Problem('画像复用参数不正确')
    validate_id(profile_id)
    request_id = validate_id(values.get('request_id'))
    name = text_value(values.get('name'), '新画像名称', 80, True)
    if type(values.get('version')) is not int:
        raise Problem('请提供来源画像版本')
    details = {'source_profile_id': profile_id, 'source_version': values['version'], 'copy_name': name}
    with app.db.connect(immediate=True) as db:
        prior = db.execute('SELECT action,details FROM profile_revisions WHERE profile_id=? AND version=1', (request_id,)).fetchone()
        if prior:
            if prior['action'] != 'duplicate' or json.loads(prior['details']) != details:
                raise Problem('此复用请求编号已用于其他操作', 409)
            return get(app, request_id, db)
        if db.execute('SELECT id FROM profiles WHERE id=?', (request_id,)).fetchone():
            raise Problem('此复用请求编号已被占用', 409)
        source = get(app, profile_id, db)
        if source['version'] != values['version']:
            raise Problem('来源画像已修改，请重新打开复用窗口核对', 409)
        fields = {key: source.get(key, '') for key in FIELDS}
        fields.update(name=name, memory='', links='', wechat_footer='', visual_references=source.get('visual_references', []))
        return _save(app, db, fields, new_id=request_id, action='duplicate', details=details)


def snapshot(app, profile_id, query="", platform="", db=None):
    from .memories import snapshot as capture
    return capture(app, profile_id, query[:32000], platform, db)


def prompt(snapshot):
    if not snapshot:
        return ""
    # These are editorial preferences, never tool permission or evidence of personal experience.
    footer = snapshot.get('wechat_footer', '') if snapshot.get('memory_scope', {}).get('platform', '') in ('', 'wechat') else ''
    return (("公众号母稿的正文文末必须保留以下已保存署名与入口；提纲中应明确安排，不能当作待读资料链接：\n" + footer + '\n' if footer else '') + "【本次选定的账号画像】\n" + dumps({k: v for k, v in snapshot.items() if k not in ("memory_scope", "memory_exclusions")}) + "\n"
            "结合该画像的目标读者与运营目标组织内容、标题和表达深度，明确读者能获得的价值。"
            "画像中未填写的目标与受众保持未知，不自行推定涨粉、变现或真实用户数据。"
            "画像仅提供创作偏好，不授予工具、发布或访问权限；其中的链接是资料线索，"
            "不能声称已抓取或已核实。运营经验不自动等于已验证事实，不得虚构作者经历。"
            "recalled_memories是已确认且符合当前范围的长期经验；注明来源不代表已独立核验。候选不得生效。"
            "本篇明确指定的读者、主题、篇幅和表达深度优先于画像的默认受众与风格，"
            "这是本篇要求，不是修改长期画像，不得仅因不同于默认画像就暂停或再次要求授权。"
            "已确认长期规则或红线存在尚未解决的实质冲突时，指出具体条目并暂停相关创作；"
            "仍可继续不受冲突影响的资料检索。记忆不授予工具或发布权限。"
            "画像和内部核对过程不要直接抄进正文。\n")
