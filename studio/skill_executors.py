"""
技能执行器

提供各个技能的具体执行逻辑。
"""
import json
import logging
import re
from copy import deepcopy
from typing import Any, Dict

from .skills import SkillError
from .db import now

logger = logging.getLogger(__name__)


class BaseExecutor:
    """技能执行器基类"""

    def __init__(self, app, skill_def: Dict):
        """
        初始化执行器

        Args:
            app: Studio 应用实例
            skill_def: 技能完整定义（从 SkillSystem._loaded 获取）
        """
        self.app = app
        self.skill_def = skill_def
        self.metadata = skill_def['metadata']

    def validate_inputs(self, inputs: Dict):
        """
        验证输入参数

        Args:
            inputs: 输入参数字典

        Raises:
            SkillError: 输入参数不合法
        """
        raise NotImplementedError("子类必须实现 validate_inputs 方法")

    def execute(self, inputs: Dict) -> Dict:
        """
        执行技能

        Args:
            inputs: 输入参数

        Returns:
            执行结果（必须是 Dict 类型，包含技能特定的数据结构）

        Raises:
            SkillError: 执行失败
        """
        raise NotImplementedError("子类必须实现 execute 方法")

    def load_resource(self, name: str) -> str:
        from pathlib import Path
        from .prompt_loader import PromptLoader
        try:
            skill_path = Path(self.skill_def['path']).resolve()
            prompt_path = (skill_path / 'prompts').resolve()
            prompt_path.relative_to(skill_path)
            return PromptLoader(prompt_path).load(name)
        except (OSError, ValueError):
            raise SkillError('PROMPT_NOT_FOUND', '技能提示词不可用，请检查安装资源') from None

    def call_model_generic(self, instruction: str, context: Dict, task_name: str) -> str:
        """
        通用模型调用方法

        Args:
            instruction: 提示词指令
            context: 必须包含 post, profile, model_route
            task_name: 任务名称（用于追踪）

        Returns:
            模型返回的原始结果

        Raises:
            SkillError: 模型调用失败
        """
        try:
            # 获取冻结的模型路由
            model_route = context.get('model_route')
            if not model_route:
                raise SkillError('INVALID_INPUT', '缺少冻结的模型路由信息')

            # 构建 draft 对象，包含模型路由和任务来源追踪
            draft = {
                **context['post'],
                '_profile_snapshot': context.get('profile'),  # 允许 None
                '_model_task': task_name,
                '_model_route': deepcopy(model_route),
                '_writing_phase': task_name,
            }

            # 调用模型
            options = {'structured': True} if context.get('structured') else {}
            result = self.app.generate_draft(draft, instruction, **options)

            if not isinstance(result, str) or not result.strip():
                raise SkillError('MODEL_ERROR', '模型返回为空')

            limit = 200000 if task_name == 'body' else 100000 if task_name == 'review' else 40000 if task_name == 'topic' else 30000
            if len(result) > limit:
                raise SkillError('MODEL_ERROR', '模型输出过长，请重试')

            return result.strip()

        except SkillError:
            # 让我们自己的 SkillError 直接传播
            raise
        except Exception as e:
            # Do not log arbitrary provider payloads, URLs or credential values.
            logger.error("调用模型失败 (%s)", type(e).__name__)
            from .adapters import DeliveryError
            from .service import Problem
            from .sanitize import sanitize_message
            if isinstance(e, (DeliveryError, Problem)):
                raise SkillError('MODEL_ERROR', sanitize_message(str(e))) from None
            # 对用户返回脱敏后的错误消息
            raise SkillError('MODEL_ERROR', f'模型调用失败 ({type(e).__name__})，请检查模型连接后重试') from None


class OutlineGenerator(BaseExecutor):
    """
    提纲生成技能执行器

    基于素材和画像生成文章提纲。
    """

    def validate_inputs(self, inputs: Dict):
        """验证输入参数"""
        required = ['post', 'materials', 'model_route']
        for field in required:
            if field not in inputs:
                raise SkillError('INVALID_INPUT', f'缺少必需参数: {field}')

        # 验证 post
        post = inputs['post']
        if not isinstance(post, dict):
            raise SkillError('INVALID_INPUT', 'post 必须是字典')
        if 'id' not in post or 'title' not in post:
            raise SkillError('INVALID_INPUT', 'post 缺少必需字段')

        # 验证 materials
        materials = inputs['materials']
        if not isinstance(materials, list):
            raise SkillError('INVALID_INPUT', 'materials 必须是列表')

        # 验证 model_route
        model_route = inputs['model_route']
        if not isinstance(model_route, dict):
            raise SkillError('INVALID_INPUT', 'model_route 必须是字典')

        # 验证 profile（可选，允许 None）
        profile = inputs.get('profile')
        if profile is not None and not isinstance(profile, dict):
            raise SkillError('INVALID_INPUT', 'profile 必须是字典或 None')

    def prepare_context(self, inputs: Dict) -> Dict:
        """
        准备上下文

        Args:
            inputs: 输入参数

        Returns:
            准备好的上下文
        """
        post = inputs['post']
        materials = inputs['materials']
        profile = inputs.get('profile')  # 允许 None，保留 None 状态标识"无画像"
        instruction = inputs.get('instruction', '').strip()
        model_route = inputs['model_route']  # 冻结的模型路由（已在 validate_inputs 中验证）

        # 限制素材数量和长度
        MAX_CONTEXT_LENGTH = 180000

        selected_materials = MaterialExtractor(self.app, self.skill_def).execute({'materials': materials})['materials']

        # 计算总长度
        total_length = len(post.get('body', '')) + len(instruction)
        for material in selected_materials:
            total_length += len(material.get('body', ''))

        if total_length > MAX_CONTEXT_LENGTH:
            raise SkillError('INVALID_INPUT', '稿件和素材总量过大，请缩小范围后重试')

        return {
            'post': post,
            'materials': selected_materials,
            'profile': profile,  # 保持 None 或 dict
            'instruction': instruction,
            'model_route': model_route,
            'workflow_instruction': inputs.get('workflow_instruction', ''),
            'topic': deepcopy(inputs.get('topic')),
            'creation_request': deepcopy(inputs.get('creation_request')),
            'context_length': total_length
        }

    def load_prompt(self, context: Dict) -> str:
        """
        加载并渲染提示词

        Args:
            context: 上下文

        Returns:
            渲染后的提示词
        """
        # 读取提示词模板
        prompt_template = self.load_resource('outline')

        # 去除 frontmatter（如果有）
        if prompt_template.startswith('---\n'):
            end = prompt_template.find('\n---\n', 4)
            if end > 0:
                prompt_template = prompt_template[end + 5:].strip()

        # 渲染模板变量
        prompt_template = self._render_template(prompt_template, context)

        return prompt_template

    def _render_template(self, template: str, context: Dict) -> str:
        """
        渲染模板中的占位符

        Args:
            template: 模板字符串
            context: 上下文数据

        Returns:
            渲染后的字符串
        """
        # 准备渲染数据
        post = context.get('post', {})
        profile = context.get('profile') or {}  # 确保 profile 是字典，即使为 None
        materials = context.get('materials', [])

        # 构建材料内容文本
        materials_text = ""
        for i, material in enumerate(materials, 1):
            materials_text += f"\n### 素材 {i}: {material.get('title', '无标题')}\n"
            materials_text += material.get('body', '')[:5000]
            materials_text += "\n"

        # 准备替换字典 - 当 profile 为空时提供合理的默认值
        replacements = {
            '{profile.direction}': profile.get('direction', '通用方向'),
            '{profile.audience}': profile.get('audience', '通用受众'),
            '{profile.goal}': profile.get('goal', '未指定'),
            '{profile.style}': profile.get('style', '专业、清晰'),
            '{profile.preferences}': profile.get('preferences', '无特殊偏好'),
            '{profile_json}': json.dumps(profile, ensure_ascii=False, indent=2) if profile else '{}',
            '{post.title}': post.get('title', '未命名'),
            '{post_title}': post.get('title', '未命名'),
            '{requirements}': context.get('instruction', '无特殊要求'),
            '{requirements_text}': context.get('instruction', '无特殊要求'),
            '{materials_content}': materials_text or '无素材',
            '{materials_text}': materials_text or '无素材',
            '{recalled_memories}': json.dumps(profile.get('recalled_memories', []), ensure_ascii=False),
            '{memories_text}': json.dumps(profile.get('recalled_memories', []), ensure_ascii=False)
        }

        # One pass: placeholders embedded in source text are never reinterpreted.
        pattern = '|'.join(re.escape(key) for key in replacements)
        return re.sub(pattern, lambda match: str(replacements[match[0]]), template)

    def build_instruction(self, context: Dict) -> str:
        """
        构建完整指令

        Args:
            context: 上下文

        Returns:
            完整指令
        """
        # 基础提示词
        base_prompt = self.load_prompt(context)
        if context.get('workflow_instruction'):
            return base_prompt + '\n\n' + context['workflow_instruction']

        # 用户要求
        user_instruction = context['instruction'] or '生成文章提纲'

        # 组装完整指令
        instruction = (
            base_prompt + "\n\n"
            "【用户编辑要求】\n" + user_instruction + "\n\n"
            "【当前稿件信息】\n" + json.dumps({
                'id': context['post']['id'],
                'title': context['post']['title'],
                'body': context['post'].get('body', '')[:1000]  # 摘录
            }, ensure_ascii=False, indent=2) + "\n\n"
            "【参考素材】\n"
        )

        # 添加素材
        for i, material in enumerate(context['materials'], 1):
            instruction += f"\n### 素材 {i}: {material.get('title', '无标题')}\n"
            instruction += material.get('body', '')[:5000]  # 每个素材最多 5000 字
            instruction += "\n"

        return instruction

    def call_model(self, instruction: str, context: Dict) -> str:
        """
        调用模型生成提纲

        Args:
            instruction: 完整指令
            context: 上下文（必须包含 model_route）

        Returns:
            模型返回的原始结果
        """
        return self.call_model_generic(instruction, context, 'outline')

    def parse_result(self, raw_result: str) -> Dict:
        """
        解析模型返回

        Args:
            raw_result: 模型返回的原始结果

        Returns:
            解析后的结果
        """
        # 初始化 political_risk，避免变量作用域问题
        political_risk = None

        # 尝试解析 JSON 格式的响应
        try:
            # 首先尝试提取 markdown 代码块中的 JSON
            json_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', raw_result, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析整个响应
                json_str = raw_result.strip()

            # 解析 JSON
            parsed = json.loads(json_str)

            # 验证必需字段
            if not isinstance(parsed, dict):
                raise ValueError("JSON 根元素必须是对象")

            # Inspect the risk signal before validating other fields. Invalid
            # structure must never turn a blocked response into accepted text.
            political_risk = parsed.get('political_risk_warning')
            if political_risk or parsed.get('has_risk'):
                from .sanitize import sanitize_message
                warning = sanitize_message(str(political_risk or '模型返回风险标记'))[:500]
                raise SkillError('POLITICAL_RISK', '内容涉及政治敏感话题，已终止生成: ' + warning,
                                 {'warning': warning})

            # 提取 outline 字段
            outline = parsed.get('outline', '')
            if not isinstance(outline, str) or not outline.strip():
                # 如果 outline 字段缺失或为空，尝试从 sections 重建
                sections = parsed.get('sections', [])
                if sections:
                    outline = self._build_outline_from_sections(sections)
                else:
                    raise ValueError("JSON 缺少有效的 outline 或 sections 字段")

            # 返回解析后的结构化数据
            return {
                'outline': outline.strip(),
                'title_suggestions': parsed.get('title_suggestions', []),
                'sections': parsed.get('sections', []),
                'material_gaps': parsed.get('material_gaps', []),
                'key_points': parsed.get('key_points', []),
                'length': len(outline)
            }
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            if raw_result.lstrip().startswith(('{', '[', '```json')) or 'political_risk_warning' in raw_result or '"has_risk"' in raw_result:
                raise SkillError('INVALID_OUTPUT', '模型返回的结构化提纲无效，请重试') from None
            # 如果 JSON 解析失败，回退到文本提取
            logger.info("模型返回 Markdown 提纲，按文本解析")

            # 尝试移除示例部分（如果存在）
            cleaned_result = self._remove_examples(raw_result)

            title_suggestions = self.extract_titles(cleaned_result)
            sections = self.extract_sections(cleaned_result)

            # 返回文本提取结果（political_risk 在此路径中应为 None）
            return {
                'outline': cleaned_result,
                'title_suggestions': title_suggestions,
                'sections': sections,
                'material_gaps': [],
                'key_points': [],
                'length': len(cleaned_result)
            }

    def _build_outline_from_sections(self, sections: list) -> str:
        """
        从 sections 数组构建 outline 文本

        Args:
            sections: 章节列表

        Returns:
            outline 文本
        """
        outline_parts = ["# 文章提纲\n"]

        for i, section in enumerate(sections, 1):
            if not isinstance(section, dict):
                raise ValueError('章节必须是对象')
            title = section.get('title', f'章节{i}')
            points = section.get('points', [])
            estimated_words = section.get('estimated_words', 0)

            outline_parts.append(f"\n## {i}、{title}（约{estimated_words}字）\n")
            for point in points:
                outline_parts.append(f"- {point}\n")

        return ''.join(outline_parts)

    def _remove_examples(self, text: str) -> str:
        """
        移除文本中的示例部分

        Args:
            text: 原始文本

        Returns:
            移除示例后的文本
        """
        # 查找常见的示例标记
        example_markers = [
            '## 示例',
            '### 示例',
            '# 示例',
            '**示例输入**',
            '**示例输出**',
            '## Example',
            '### Example'
        ]

        # 找到第一个示例标记的位置
        min_pos = len(text)
        for marker in example_markers:
            pos = text.find(marker)
            if pos != -1 and pos < min_pos:
                min_pos = pos

        # 如果找到示例标记，截断文本
        if min_pos < len(text):
            return text[:min_pos].strip()

        return text.strip()

    def extract_titles(self, outline: str) -> list:
        """
        从提纲中提取标题建议

        Args:
            outline: 提纲内容

        Returns:
            标题列表
        """
        titles = []
        in_titles = False

        for line in outline.splitlines():
            text = line.strip().strip('*').strip()

            # 检测标题区域
            if re.search(r'(?:备选|候选|建议)标题', text) and len(text) < 40:
                in_titles = True
                continue

            # 直接匹配 "标题："
            direct = re.match(r'^(?:#{1,6}\s*)?(?:文章标题|已选标题|标题)[：:]\s*(.+)$', text)

            # 在标题区域内匹配列表项
            numbered = re.match(r'^(?:[-*]|\d+[.、)）])\s*(.+)$', text) if in_titles else None

            if direct or numbered:
                title = (direct or numbered).group(1).strip().strip('*').strip().strip('""')
                if title and len(title) <= 150 and title not in titles:
                    titles.append(title)
            elif text and not re.search(r'标题', text):
                in_titles = False

        return titles

    def extract_sections(self, outline: str) -> list:
        """
        从提纲中提取章节结构

        Args:
            outline: 提纲内容

        Returns:
            章节列表
        """
        sections = []

        for line in outline.splitlines():
            # 匹配 Markdown 标题
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()

                # 跳过标题建议区域
                if '标题' in title and level <= 2:
                    continue

                sections.append({
                    'level': level,
                    'title': title
                })

        return sections

    def execute(self, inputs: Dict) -> Dict:
        """执行提纲生成"""
        logger.info(f"开始执行技能: {self.metadata['name']}")

        # 1. 验证输入
        self.validate_inputs(inputs)

        # 2. 准备上下文
        context = self.prepare_context(inputs)

        # 3. 构建指令
        instruction = self.build_instruction(context)

        # 4. 调用模型
        raw_result = self.call_model(instruction, context)

        # 5. 解析结果（政治风险检查已在 parse_result 中处理）
        result = self.parse_result(raw_result)

        logger.info(f"技能执行成功: {self.metadata['name']}")

        return result


class DraftReviewer(BaseExecutor):
    """
    审稿建议技能执行器

    检查文章并提出改进建议。
    """

    def validate_inputs(self, inputs: Dict):
        """验证输入参数"""
        required = ['post', 'model_route']
        for field in required:
            if field not in inputs:
                raise SkillError('INVALID_INPUT', f'缺少必需参数: {field}')

        post = inputs['post']
        if not isinstance(post, dict):
            raise SkillError('INVALID_INPUT', 'post 必须是字典')

        if 'body' not in post or not post['body'].strip():
            raise SkillError('INVALID_INPUT', 'post.body 不能为空')

        # 验证 model_route
        model_route = inputs['model_route']
        if not isinstance(model_route, dict):
            raise SkillError('INVALID_INPUT', 'model_route 必须是字典')

        # profile 可选，允许 None
        profile = inputs.get('profile')
        if profile is not None and not isinstance(profile, dict):
            raise SkillError('INVALID_INPUT', 'profile 必须是字典或 None')

    def prepare_context(self, inputs: Dict) -> Dict:
        """
        准备审稿上下文

        Args:
            inputs: 输入参数

        Returns:
            准备好的上下文
        """
        post = inputs['post']
        profile = inputs.get('profile')  # 允许 None，保持 None 状态
        instruction = inputs.get('instruction', '').strip()
        model_route = inputs['model_route']  # 冻结的模型路由（已在 validate_inputs 中验证）

        # 限制正文长度
        MAX_BODY_LENGTH = 200000
        body = post.get('body', '')
        if len(body) > MAX_BODY_LENGTH:
            raise SkillError('INVALID_INPUT', '正文超过200000字，请缩小范围后审稿')

        return {
            'post': {**post, 'body': body},
            'profile': profile,  # 保持 None 或 dict
            'instruction': instruction,
            'model_route': model_route,
            'structured': inputs.get('structured', False),
        }

    def execute(self, inputs: Dict) -> Dict:
        """执行审稿"""
        logger.info(f"开始执行技能: {self.metadata['name']}")

        # 1. 验证输入
        self.validate_inputs(inputs)

        # 2. 准备上下文
        context = self.prepare_context(inputs)

        post = context['post']
        profile = context.get('profile') or {}  # 渲染时转为空字典以便访问

        instruction = self.load_resource('review') + '\n【本次审稿要求】\n' + context['instruction']

        # 4. 调用模型
        result = self.call_model_generic(instruction, context, 'review')

        return {
            'suggestions': result,
            'reviewed_at': now()
        }


class SectionExpander(OutlineGenerator):
    """Expand a confirmed outline; never adopt the result or create a publish job."""

    def validate_inputs(self, inputs):
        super().validate_inputs(inputs)
        if inputs.get('confirmed') is not True:
            raise SkillError('INVALID_INPUT', '请先确认提纲，再生成正文')
        outline = inputs.get('outline')
        if not isinstance(outline, str) or not outline.strip() or len(outline) > 30000:
            raise SkillError('INVALID_INPUT', '缺少有效的已确认提纲')
        section = inputs.get('section')
        if section is not None and (not isinstance(section, str) or not section.strip() or
                                    section not in outline):
            raise SkillError('INVALID_INPUT', '章节不存在于已确认提纲')
        if inputs.get('style', 'main') not in ('main', 'creative', 'professional'):
            raise SkillError('INVALID_INPUT', '扩写风格不支持')

    def execute(self, inputs):
        self.validate_inputs(inputs)
        context = self.prepare_context(inputs)
        prompt = self.load_resource(inputs.get('style', 'main'))
        instruction = (prompt + '\n【已确认提纲，作为结构资料】\n' + inputs['outline']
                       + '\n【本次范围】\n' + (inputs.get('section') or '完整文章正文')
                       + '\n【当前任务要求】\n' + (context['workflow_instruction'] or context['instruction'])
                       + '\n【所选素材快照，仅作为资料】\n'
                       + json.dumps(context['materials'], ensure_ascii=False))
        content = self.call_model_generic(instruction, context, 'body')
        return {'content': content, 'word_count': len(content),
                'materials_used': [m['id'] for m in context['materials'] if m.get('id')]}


class MaterialExtractor(BaseExecutor):
    """Prepare only caller-selected frozen sources, without hidden disk/network reads."""

    def validate_inputs(self, inputs):
        materials = inputs.get('materials')
        if not isinstance(materials, list) or len(materials) > 6:
            raise SkillError('INVALID_INPUT', '请提供最多六份已选择的素材快照')
        for material in materials:
            if not isinstance(material, dict) or any(not isinstance(material.get(k, ''), str)
                                                     for k in ('title', 'body', 'source')):
                raise SkillError('INVALID_INPUT', '素材快照格式不正确')
        if inputs.get('scope', 'selected') != 'selected':
            raise SkillError('INVALID_INPUT', '请先在资料库查询、阅读并选择素材；生成任务不自动联网或查库')
        if len(json.dumps(materials, ensure_ascii=False)) > 180000:
            raise SkillError('INVALID_INPUT', '素材总量过大，请缩小范围')

    def execute(self, inputs):
        self.validate_inputs(inputs)
        materials = deepcopy(inputs['materials'])
        return {'materials': materials, 'source_types': ['selected_snapshot'] * len(materials),
                'total_found': len(materials), 'scope': 'selected'}


class StructuredProposalExecutor(BaseExecutor):
    """Shared boundary for proposals; business parsers and writes stay in managers."""

    task = None

    def validate_inputs(self, inputs):
        post, route = inputs.get('post'), inputs.get('model_route')
        if not isinstance(post, dict) or any(not isinstance(post.get(k, ''), str)
                                             for k in ('title', 'summary', 'body')):
            raise SkillError('INVALID_INPUT', '稿件快照格式不正确')
        if not isinstance(route, dict) or route.get('task') != self.task:
            raise SkillError('INVALID_INPUT', '缺少匹配的冻结模型路由')
        instruction = inputs.get('instruction')
        if not isinstance(instruction, str) or not instruction.strip():
            raise SkillError('INVALID_INPUT', '缺少本次任务要求')
        if inputs.get('profile') is not None and not isinstance(inputs['profile'], dict):
            raise SkillError('INVALID_INPUT', '画像快照格式不正确')

    def execute(self, inputs):
        self.validate_inputs(inputs)
        context = deepcopy(inputs)
        context['structured'] = True
        return {'raw': self.call_model_generic(context['instruction'], context, self.task)}


class TopicPlanner(StructuredProposalExecutor):
    task = 'topic'


class MemoryExtractor(StructuredProposalExecutor):
    task = 'chat'


class PerformanceReviewer(StructuredProposalExecutor):
    task = 'review'


# 注册执行器映射
EXECUTORS = {
    'generate-outline': OutlineGenerator,
    'review-draft': DraftReviewer,
    'expand-section': SectionExpander,
    'extract-materials': MaterialExtractor,
    'plan-topics': TopicPlanner,
    'extract-memories': MemoryExtractor,
    'review-performance': PerformanceReviewer,
}


def get_executor(skill_name: str):
    """
    获取技能执行器类

    Args:
        skill_name: 技能名称

    Returns:
        执行器类

    Raises:
        KeyError: 技能没有对应的执行器
    """
    if skill_name not in EXECUTORS:
        raise KeyError(f"技能 {skill_name} 没有对应的执行器")

    return EXECUTORS[skill_name]
