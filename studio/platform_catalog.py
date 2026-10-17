"""PaperPlane's platform directory; connection, implementation and verification differ."""
import hashlib
import json

from .db import dumps, now


def platform(id, name, *, group='existing', kind='manual', config=None, aliases=(),
             formats=('Markdown',), images='manual', draft=False, publish=False,
             repurpose=True, recipe='', bridge=False):
    return dict(id=id, name=name, group=group, kind=kind, config=config or {},
                default_enabled=group == 'existing', aliases=list(aliases),
                formats=list(formats), images=images, draft=draft, publish=publish,
                repurpose=repurpose, recipe=recipe or '面向目标平台的技术读者，组织独立标题、导语与完整正文；保留事实、有效代码与来源，不编造亲测、图片或平台送稿结果。',
                bridge=bridge)


CATALOG = [
    platform('local', '本地专栏', kind='local', images='local', publish=True, repurpose=False),
    platform('wechat', '微信公众号', config={'editor_url':'https://mp.weixin.qq.com/'},
             aliases=('公众号','微信'), formats=('Markdown','HTML'), images='upload', draft=True, repurpose=False),
    platform('csdn', 'CSDN', config={'editor_url':'https://editor.csdn.net/md/'}, draft=True, bridge=True, images='upload',
             recipe='面向中文技术读者，保留有效代码和技术细节；移除微信公众号扫码、二维码及关注推广话术。署名和博客入口仅依据本次画像或母稿已有信息，不硬编码作者。'),
    platform('juejin', '掘金', config={'editor_url':'https://juejin.cn/editor/drafts/new?v=2'}, draft=True, bridge=True, images='upload',
             recipe='面向中文开发者，独立组织技术标题、导语、结构和标签，保留代码与真实证据，不机械复制母稿。摘要必须控制在100字以内，标点、空格及英文字符也计数，优先使用完整短句。'),
    platform('devto', 'DEV Community', kind='devto', config={'publish':False}, aliases=('DEV','dev.to'), images='public-url', draft=True, publish=True,
             recipe='标题、摘要和正文必须为英文适配，保持技术含义，代码不要翻译；最多4个纯英文字母或数字标签，每个最多30字。保留有效来源和图片引用，不伪造公开图片地址。'),
    platform('xiaohongshu', '小红书', config={'editor_url':'https://creator.xiaohongshu.com/'}, formats=('图集','短配文'), draft=True, images='upload',
             recipe='生成独立中文短标题（1至20字，标点、空格、数字计入；不得直接沿用母稿长标题或系列编号）、短配文与话题。规划3至12页图集，第一页为封面，之后每页一个要点，最后总结；各页独立于母稿能讲清内容。不要夸张Hook或预测流量。图集计划不代表实际图片已生成，不添加二维码或博客外链。'),
    platform('zhihu', '知乎', group='next', config={'editor_url':'https://zhuanlan.zhihu.com/write'},
             formats=('Markdown','HTML'), draft=True, bridge=True, images='upload',
             recipe='面向知乎读者，围绕一个明确问题组织论点、技术证据与结论；保留来源、代码和必要配图。不编造亲测经历，不添加无关推广。'),
    platform('cnblogs', '博客园', group='next', config={'editor_url':'https://i.cnblogs.com/posts/edit'},
             draft=True, bridge=True, images='upload',
             recipe='面向开发者写完整技术文章，保留Markdown代码块、复现步骤与来源。只保留已有事实，不虚构测试结果；标题直接说明技术问题，摘要概括结论。'),
    platform('oschina', '开源中国', group='next', aliases=('OSC',), config={'editor_url':'https://my.oschina.net/'},
             draft=True, bridge=True, images='upload',
             recipe='面向开源开发者，围绕项目用途、实际部署步骤、关键代码和已知限制组织完整Markdown文章。清楚区分亲测与文档研究，保留项目及来源链接，不编造性能数据、开源许可或亲测经历。'),
    platform('segmentfault', 'SegmentFault', group='next', aliases=('思否',), config={'editor_url':'https://segmentfault.com/write'},
             draft=True, bridge=True, images='upload',
             recipe='围绕具体开发问题组织技术文章：说明问题和环境、给出可复现步骤与关键代码、解释取舍和结论。保留来源和限制，清楚区分亲测与研究，不编造运行结果。'),
    platform('51cto','51CTO',group='next',config={'editor_url':'https://blog.51cto.com/blogger/publish'},
             draft=True,bridge=True,images='upload',
             recipe='面向工程实践读者，交代环境与前提、部署步骤、配置和验证方法、常见故障与结论。保留代码和来源，不编造测试结果，不把计划写成已上线。'),
    platform('toutiao','头条号',group='later',aliases=('今日头条',),config={'editor_url':'https://mp.toutiao.com/profile_v4/graphic/publish'},formats=('HTML',),draft=True,bridge=True,images='upload'),
    platform('bilibili-article','B站专栏',group='later',aliases=('哔哩哔哩专栏',),config={'editor_url':'https://member.bilibili.com/platform/upload/text'},formats=('HTML',),draft=True,bridge=True,images='upload'),
    platform('jianshu','简书',group='later',config={'editor_url':'https://www.jianshu.com/writer'},formats=('Markdown','HTML'),draft=True,bridge=True,images='upload'),
    platform('weibo','微博',group='optional',formats=('头条文章','HTML'),config={'editor_url':'https://card.weibo.com/article/v5/editor'},draft=True,bridge=True,images='upload',recipe='改编为微博头条文章，组织独立标题、导语与完整图文正文。保留事实来源，不编造经历；此渠道保存头条文章草稿，普通短微博不在此发送。'),
]
BY_ID = {p['id']: p for p in CATALOG}
REPURPOSE = {p['id']: p['name'] for p in CATALOG if p['repurpose']}
RECIPES = {p['id']: p['recipe'] for p in CATALOG if p['repurpose']}
ALIASES = {a.lower(): p['id'] for p in CATALOG for a in (p['id'],p['name'],*p['aliases'])}
BRIDGE_PLATFORMS = {p['id'] for p in CATALOG if p['bridge']}
LOGIN_ONLY_PLATFORMS = set()
DESKTOP_LOGIN_PLATFORMS = BRIDGE_PLATFORMS | LOGIN_ONLY_PLATFORMS | {'devto'}
RETIRED_PLATFORMS = {'wordpress','typecho'}


def enabled_repurpose(app):
    return [p['id'] for p in app.channels() if p['enabled'] and p['repurpose']]


def credential_fingerprint(app, channel):
    if channel in BRIDGE_PLATFORMS and app.publishing_bridge.browser:
        return app.publishing_bridge.fingerprint(channel)
    if channel == 'xiaohongshu':
        return app.xhs_drafts.fingerprint()
    keys = {'wechat':('WECHAT_APP_ID','WECHAT_APP_SECRET'), 'devto':('DEVTO_API_KEY',)}.get(channel, ('PUBLISH_BRIDGE_TOKEN',))
    return hashlib.sha256(dumps([app.config.get(k) for k in keys]).encode()).hexdigest()


def record_success(app, db, channel, result, fingerprint, job_id):
    """Only called after an automatic adapter returns a successful receipt, never manual history."""
    if result.get('status') not in ('draft','published') or not result.get('external_id'):
        return
    operation = 'draft' if result['status'] == 'draft' else 'publish'
    key = f'platform-verification:{channel}:{operation}'
    value = dict(status='verified', at=now(), job_id=job_id, fingerprint=fingerprint)
    db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,dumps(value)))


def decorate(app, rows):
    checks = {r['key'].split(':',1)[1]:json.loads(r['value']) for r in app.db.rows("SELECT key,value FROM settings WHERE key LIKE 'channel-check:%'")}
    proofs = {r['key']: json.loads(r['value']) for r in app.db.rows("SELECT key,value FROM settings WHERE key LIKE 'platform-verification:%'")}
    bridge = app.publishing_bridge.status()
    for row in rows:
        p = BY_ID.get(row['id'])
        if not p:
            row.update(repurpose=False, auto_send=False, verification={}, group='optional', formats=[], images='manual', draft=False, publish=False)
            continue
        row.update({k:p[k] for k in ('group','formats','images','draft','publish','repurpose','bridge')})
        verification = {}
        for operation in ('draft','publish'):
            proof = proofs.get(f"platform-verification:{row['id']}:{operation}", {})
            verified = bool(proof.get('status') == 'verified' and proof.get('job_id') and proof.get('fingerprint') == credential_fingerprint(app,row['id']))
            verification[operation] = {'status':'verified' if verified else 'unverified', 'at':proof.get('at','') if verified else '', 'job_id':proof.get('job_id','') if verified else ''}
        row['verification'] = verification
        row['connection_check'] = checks.get(row['id'])
        row['account'] = '本机' if row['kind']=='local' else ('扩展已连接，平台账号发送前核验' if bridge['connected'] else '扩展未连接') if p['bridge'] else ('凭据已配置，非登录核验' if row['ready'] else '待配置') if row['kind'] in ('wechat','devto') else '手动登录，尚未接入账号核验'
        if row['id'] in DESKTOP_LOGIN_PLATFORMS and bridge['desktop']:
            account = bridge['accounts'].get(row['id'])
            row['login_state'] = bridge.get('account_states', {}).get(row['id'], 'unknown')
            row['account_name'] = account['name'] if account else ''
            row['account'] = (('已登录 ' if row['login_state']=='verified' else '已记住账号，待核验 ') + account['name']) if account else '未核验登录'
        row['desktop_login'] = bool(row['id'] in DESKTOP_LOGIN_PLATFORMS and bridge['desktop'])
        if row['id'] == 'devto' and bridge['desktop']:
            row['images'] = 'upload'
            row['account'] = 'API凭据' + ('已配置' if row['ready'] else '未配置') + '；图片登录：' + row['account']
        row['login_only'] = row['id'] in LOGIN_ONLY_PLATFORMS
        row['login_busy'] = bool(bridge.get('busy'))
        if row['id'] == 'xiaohongshu':
            xhs = app.xhs_drafts.status()
            row['login_state'] = xhs['account_state']
            row['account_name'] = (xhs.get('account') or {}).get('name', '')
            row['account'] = ('已登录 ' + xhs['account']['name']) if xhs['logged_in'] else ('已记住账号，待核验 ' + xhs['account']['name']) if xhs.get('account') else '首次送稿时在弹窗登录' if xhs['available'] else '需要 Windows 安装版的本机窗口'
        row['send_operation'] = 'draft' if p['bridge'] or row['id'] in ('wechat','xiaohongshu') or row['id']=='devto' and not row['config'].get('publish') else 'publish'
        op = row['send_operation']
        row['delivery_implemented'] = row['kind'] in ('bridge','wechat','devto','local','xhs-browser') and p[op]
        row['auto_send'] = bool(row['enabled'] and row['ready'] and row['delivery_implemented'] and verification[op]['status']=='verified' and (not p['bridge'] or bridge['connected'] or (bridge['desktop'] and bridge['accounts'].get(row['id']))))
        if row['id'] == 'xiaohongshu':
            row['auto_send'] = row['auto_send'] and xhs['logged_in']
        row['capability_label'] = '本地保存' if row['kind']=='local' else '可自动发送' if row['auto_send'] else '已有适配 · 待发送验通' if (p['draft'] or p['publish']) else '手动发布包 · 自动发送待接入'
    return rows


def chat_context(app):
    return '平台目录（唯一能力依据；有适配不等于验通，改编不代表发送）：\n' + '\n'.join(
        f"{p['id']} / {p['name']}：{'已启用' if p['enabled'] else '未启用，先到添加平台启用'}；格式{','.join(p['formats'])}；{p['capability_label']}；账号{p.get('account','待接入')}"
        for p in app.channels())
