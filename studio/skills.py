"""
纸飞机技能系统

提供技能的加载、管理和执行功能。
"""
import json
import logging
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 安装日志脱敏过滤器
from studio.sanitize import install_sanitizing_filter
install_sanitizing_filter()

logger = logging.getLogger(__name__)


class SkillError(Exception):
    """技能执行错误"""
    def __init__(self, code: str, message: str, details: Optional[Dict] = None):
        super().__init__(message)
        self.code = code
        from .sanitize import sanitize_message, sanitize_dict
        self.message = sanitize_message(message)
        self.details = sanitize_dict(details or {})
        self.args = (self.message,)


class SkillSystem:
    """
    技能系统

    负责技能的注册、加载和执行。
    """

    def __init__(self, app, skills_dir: Optional[Path] = None):
        """
        初始化技能系统

        Args:
            app: Studio 应用实例
            skills_dir: 技能目录路径，默认为 source/skills（可以是 str 或 Path）
        """
        self.app = app
        # 确保 skills_dir 是 Path 对象
        if skills_dir is None:
            self.skills_dir = Path(__file__).parent.parent / 'skills'
        elif isinstance(skills_dir, str):
            self.skills_dir = Path(skills_dir)
        else:
            self.skills_dir = skills_dir

        self._registry: Dict[str, Dict] = {}  # skill_name -> skill_info
        self._loaded: Dict[str, Dict] = {}    # skill_name -> full_skill_definition
        self._system_enabled = True  # 系统级启用状态

        # 检查配置是否禁用技能系统
        if not app.config.get('SKILLS_ENABLED', True):
            logger.info("技能系统已被配置禁用")
            self._system_enabled = False
            return

        # 扫描并注册所有技能
        try:
            self._scan_skills()
            logger.info(f"技能系统初始化完成，已加载 {len(self._registry)} 个技能")
        except Exception as e:
            logger.error(f"技能系统初始化失败，降级为禁用模式: {e}")
            self._system_enabled = False
            self._registry.clear()
            self._loaded.clear()

    def _scan_skills(self):
        """扫描技能目录，注册所有技能的元数据"""
        if not self.skills_dir.exists():
            logger.warning(f"技能目录不存在: {self.skills_dir}")
            return

        # 查找所有 SKILL.md 文件
        for skill_file in sorted(self.skills_dir.rglob('SKILL.md')):
            try:
                skill_file.resolve().relative_to(self.skills_dir.resolve())
                metadata = self._parse_skill_metadata(skill_file)
                if metadata:
                    skill_name = metadata['name']

                    # 检查是否重复
                    if skill_name in self._registry:
                        logger.warning(f"技能名称重复: {skill_name}，跳过 {skill_file}")
                        continue

                    # 注册技能
                    self._registry[skill_name] = {
                        'metadata': metadata,
                        'path': skill_file.parent,
                        'file': skill_file
                    }

                    logger.debug(f"注册技能: {skill_name} ({metadata.get('version', 'unknown')})")

            except Exception as e:
                logger.error(f"解析技能文件失败 {skill_file}: {e}")

    def _parse_skill_metadata(self, skill_file: Path) -> Optional[Dict]:
        """
        解析技能文件的 frontmatter 元数据

        Args:
            skill_file: SKILL.md 文件路径

        Returns:
            技能元数据字典，解析失败返回 None
        """
        try:
            content = skill_file.read_text(encoding='utf-8')

            # 查找 YAML frontmatter
            if not content.startswith('---\n'):
                logger.warning(f"技能文件缺少 frontmatter: {skill_file}")
                return None

            # 提取 frontmatter
            end = content.find('\n---\n', 4)
            if end == -1:
                logger.warning(f"技能文件 frontmatter 格式错误: {skill_file}")
                return None

            frontmatter = content[4:end]
            metadata = yaml.safe_load(frontmatter)
            if not isinstance(metadata, dict):
                return None

            # 验证必需字段
            required = ['name', 'version', 'description', 'category']
            for field in required:
                if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                    logger.warning(f"技能缺少必需字段 {field}: {skill_file}")
                    return None

            # 设置默认值
            metadata.setdefault('author', 'system')
            metadata.setdefault('dependencies', [])
            metadata.setdefault('tags', [])
            metadata.setdefault('enabled', True)
            metadata.setdefault('deprecated', False)
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', metadata['name']):
                return None
            if any(type(metadata[key]) is not bool for key in ('enabled', 'deprecated')):
                return None

            return metadata

        except yaml.YAMLError as e:
            logger.error(f"YAML 解析错误 {skill_file}: {e}")
            return None
        except Exception as e:
            logger.error(f"读取技能文件失败 {skill_file}: {e}")
            return None

    def enabled(self, skill_name: str) -> bool:
        """
        检查技能是否可用

        Args:
            skill_name: 技能名称

        Returns:
            技能是否已注册且启用
        """
        # 首先检查系统级是否启用
        if not self._system_enabled:
            return False

        if skill_name not in self._registry:
            return False

        metadata = self._registry[skill_name]['metadata']
        return metadata.get('enabled', True) and not metadata.get('deprecated', False)

    def get_metadata(self, skill_name: str) -> Optional[Dict]:
        """
        获取技能元数据

        Args:
            skill_name: 技能名称

        Returns:
            技能元数据，不存在返回 None
        """
        if skill_name not in self._registry:
            return None

        return deepcopy(self._registry[skill_name]['metadata'])

    def list_skills(self, category: Optional[str] = None,
                    enabled_only: bool = True) -> List[Dict]:
        """
        列出所有技能

        Args:
            category: 过滤分类，None 表示不过滤
            enabled_only: 是否只返回启用的技能

        Returns:
            技能元数据列表
        """
        skills = []
        if enabled_only and not self._system_enabled:
            return skills

        for skill_info in self._registry.values():
            metadata = skill_info['metadata']

            # 过滤分类
            if category and metadata.get('category') != category:
                continue

            # 过滤启用状态
            if enabled_only:
                if not metadata.get('enabled', True) or metadata.get('deprecated', False):
                    continue

            skills.append(deepcopy(metadata))

        return skills

    def execute(self, skill_name: str, **kwargs) -> Dict[str, Any]:
        """
        执行技能

        Args:
            skill_name: 技能名称
            **kwargs: 技能需要的输入参数

        Returns:
            标准响应对象:
            {
                'success': bool,
                'data': dict or None,
                'error': dict or None,
                'metadata': dict
            }

        Raises:
            ValueError: 技能不存在或未启用
        """
        # 检查技能是否存在
        if skill_name not in self._registry:
            raise ValueError(f"技能不存在: {skill_name}")

        # 检查技能是否启用
        if not self.enabled(skill_name):
            metadata = self._registry[skill_name]['metadata']
            if metadata.get('deprecated'):
                replacement = metadata.get('replacement', '未指定')
                raise ValueError(f"技能已废弃: {skill_name}，请使用 {replacement}")
            else:
                raise ValueError(f"技能未启用: {skill_name}")

        # 记录开始时间
        start_time = time.time()

        # 获取技能信息
        skill_info = self._registry[skill_name]
        metadata = skill_info['metadata']

        logger.info(f"执行技能: {skill_name} v{metadata['version']}")

        try:
            # 加载完整的技能定义（如果尚未加载）
            if skill_name not in self._loaded:
                self._load_skill(skill_name)

            # 执行技能
            result = self._execute_skill(skill_name, kwargs)

            # 计算执行时间
            duration_ms = int((time.time() - start_time) * 1000)

            # 构造成功响应
            response = {
                'success': True,
                'data': result,
                'error': None,
                'metadata': {
                    'skill': skill_name,
                    'version': metadata['version'],
                    'category': metadata['category'],
                    'duration_ms': duration_ms,
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                }
            }

            logger.info(f"技能执行成功: {skill_name}, 耗时 {duration_ms}ms")
            return response

        except SkillError as e:
            # 技能执行错误（SkillError 的 message 必须是已脱敏的安全消息）
            duration_ms = int((time.time() - start_time) * 1000)

            logger.error(f"技能执行失败: {skill_name}, 错误: {e.code} - {e.message}")

            return {
                'success': False,
                'data': None,
                'error': {
                    'code': e.code,
                    'message': e.message,
                    'details': e.details
                },
                'metadata': {
                    'skill': skill_name,
                    'version': metadata['version'],
                    'duration_ms': duration_ms,
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                }
            }

        except Exception as e:
            # 未预期的错误
            duration_ms = int((time.time() - start_time) * 1000)

            logger.error("技能执行异常: %s (%s)", skill_name, type(e).__name__)

            # 脱敏异常消息
            from studio.sanitize import sanitize_exception
            safe_message = sanitize_exception(e)

            return {
                'success': False,
                'data': None,
                'error': {
                    'code': 'INTERNAL_ERROR',
                    'message': safe_message,
                    'details': {}
                },
                'metadata': {
                    'skill': skill_name,
                    'version': metadata['version'],
                    'duration_ms': duration_ms,
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                }
            }

    def _load_skill(self, skill_name: str):
        """
        加载技能的完整定义

        Args:
            skill_name: 技能名称
        """
        skill_info = self._registry[skill_name]
        skill_file = skill_info['file']

        try:
            content = skill_file.read_text(encoding='utf-8')

            # 存储完整定义（包含元数据和正文）
            self._loaded[skill_name] = {
                'metadata': skill_info['metadata'],
                'content': content,
                'path': skill_info['path']
            }

            logger.debug(f"加载技能定义: {skill_name}")

        except Exception as e:
            logger.error("加载技能定义失败: %s (%s)", skill_name, type(e).__name__)
            raise SkillError('LOAD_ERROR', '无法加载技能定义') from None

    def _execute_skill(self, skill_name: str, inputs: Dict) -> Dict:
        """
        执行技能的核心逻辑

        Args:
            skill_name: 技能名称
            inputs: 输入参数

        Returns:
            执行结果
        """
        # 动态导入执行器模块
        from . import skill_executors
        try:
            executor_class = skill_executors.get_executor(skill_name)
        except KeyError:
            # 技能没有对应的执行器
            raise NotImplementedError(
                f"技能 {skill_name} 的执行器尚未实现。"
                f"当前已实现的技能: {', '.join(skill_executors.EXECUTORS.keys())}"
            )
        skill_def = self._loaded[skill_name]
        executor = executor_class(self.app, skill_def)
        return executor.execute(inputs)

    def reload_skills(self):
        """重新扫描并加载所有技能"""
        self._registry.clear()
        self._loaded.clear()
        self._scan_skills()
        logger.info(f"技能重新加载完成，已加载 {len(self._registry)} 个技能")

    def get_skill_info(self, skill_name: str) -> Optional[Dict]:
        """
        获取技能的完整信息

        Args:
            skill_name: 技能名称

        Returns:
            包含元数据和路径的完整信息，不存在返回 None
        """
        if skill_name not in self._registry:
            return None

        info = self._registry[skill_name].copy()
        info['path'] = str(info['path'])
        info['file'] = str(info['file'])

        return info


# 便捷函数，供其他模块使用
def generate_structured(app, skill_name, post, instruction):
    """Dispatch a prepared snapshot once; managers still validate and save results."""
    from .adapters import DeliveryError
    system = getattr(app, 'skill_system', None)
    if system and system.enabled(skill_name):
        response = system.execute(skill_name, post=post, instruction=instruction,
                                  profile=post.get('_profile_snapshot'),
                                  model_route=post.get('_model_route'))
        if not response['success']:
            raise DeliveryError(response['error']['message'])
        return response['data']['raw']
    # Compatibility is selected before execution, never after a failed call.
    return app.generate_draft(post, instruction, structured=True)


def init_skill_system(app):
    """
    初始化技能系统并挂载到 app

    Args:
        app: Studio 应用实例
    """
    if not hasattr(app, 'skill_system'):
        app.skill_system = SkillSystem(app)
        logger.info("技能系统已初始化")
    else:
        logger.debug("技能系统已存在，跳过初始化")
