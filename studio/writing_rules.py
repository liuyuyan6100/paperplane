"""Phase-specific reliability instructions; never rewrite or hide model output."""
import re


def footer_satisfied(body, profile):
    footer = (profile or {}).get('wechat_footer', '').strip()
    if not footer:
        return True
    urls = re.findall(r'https?://[^\s<>\[\]()]+', footer)
    tail = body.rstrip()[-max(500, len(footer) + 50):]
    return all(url in tail for url in urls) if urls else footer in tail


def apply_footer(body, profile, platform=''):
    footer = (profile or {}).get('wechat_footer', '').strip()
    if platform not in ('', 'wechat') or not footer or footer_satisfied(body, profile):
        return body, False
    return body.rstrip() + '\n\n' + footer, True

BODY_RELIABILITY = (
    '根据给定资料写作，不编造事实、亲历、实测数据、引文或来源。'
    '正文面向读者：提纲里的待补证据清单、核对备注、写作流程和材料来源自述是编辑资料，不抄入正文。'
    '不要在正文添加[待核实]、【待核实】、TODO等编辑占位标记，'
    '也不要写“不是某次实测报告”等与读者理解无关的内部免责声明。'
    '无法支撑的细节应省略；确有必要的未验证判断，使用自然的限定表达紧邻该判断，不能把推测写成事实。'
    '若缺少支持核心结论的依据，应明确说明该结论目前无法确定，不能靠删去标记伪装成已验证。'
    '保留读者需要的真实来源、适用条件和局限；计划使用将来时，不声称已经完成事实核查或发表。'
)


def reliability_instruction(phase):
    if phase == 'body':
        return BODY_RELIABILITY
    return '根据给定资料写作，不编造事实、引文或数据；缺少依据用[待核实]标明。计划使用将来时，不声称已经发布内容。'
