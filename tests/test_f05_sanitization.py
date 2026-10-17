"""
F05: 异常脱敏测试
验证敏感字段（如 Authorization、API Key 等）不会泄露到错误消息或日志中
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from studio.sanitize import sanitize_message, sanitize_exception, sanitize_dict
from studio.skills import SkillError


class TestF05Sanitization(unittest.TestCase):
    """测试异常和日志脱敏功能"""

    def test_sanitize_api_key(self):
        """测试 API Key 脱敏"""
        message = "Error: api_key=sk-" + "1234567890abcdefghijklmnopqrstuvwxyz"
        sanitized = sanitize_message(message)
        self.assertNotIn("sk-1234567890", sanitized)
        self.assertIn("api_key=***", sanitized)

    def test_sanitize_authorization_bearer(self):
        """测试 Authorization Bearer 脱敏"""
        message = 'Request failed: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
        sanitized = sanitize_message(message)
        self.assertNotIn("eyJhbGci", sanitized)
        self.assertIn("Authorization: Bearer ***", sanitized)

    def test_sanitize_authorization_header(self):
        """测试 Authorization 头脱敏"""
        message = "authorization=Basic dXNlcjpwYXNzd29yZA=="
        sanitized = sanitize_message(message)
        self.assertNotIn("dXNlcjpwYXNzd29yZA==", sanitized)
        self.assertIn("Authorization: Basic ***", sanitized)

    def test_sanitize_token(self):
        """测试 Token 脱敏"""
        message = "token=ghp_" + "1234567890abcdefghijklmnopqrstuvwxyz"
        sanitized = sanitize_message(message)
        self.assertNotIn("ghp_1234567890", sanitized)
        self.assertIn("token=***", sanitized)

    def test_sanitize_password(self):
        """测试密码脱敏"""
        message = "Login failed: password=MySecretPass123"
        sanitized = sanitize_message(message)
        self.assertNotIn("MySecretPass123", sanitized)
        self.assertIn("password=***", sanitized)

    def test_sanitize_exception_with_sensitive_data(self):
        """测试异常脱敏"""
        exc = Exception("API call failed: Authorization: Bearer sk-proj-abcdefg123456")
        safe_message = sanitize_exception(exc)
        self.assertNotIn("sk-proj-abcdefg", safe_message)
        self.assertIn("Exception:", safe_message)

    def test_sanitize_exception_long_message(self):
        """测试长异常消息被截断"""
        long_message = "Error: " + "x" * 300
        exc = Exception(long_message)
        safe_message = sanitize_exception(exc)
        # 长消息应该被简化
        self.assertLess(len(safe_message), 100)
        self.assertIn("Exception", safe_message)

    def test_sanitize_exception_with_traceback(self):
        """测试包含堆栈的异常被简化"""
        exc = Exception("Error\nTraceback line 1\nTraceback line 2")
        safe_message = sanitize_exception(exc)
        # 包含换行符的消息应该被简化
        self.assertNotIn("Traceback", safe_message)
        self.assertIn("Exception", safe_message)

    def test_sanitize_dict_with_sensitive_keys(self):
        """测试字典脱敏"""
        data = {
            'username': 'testuser',
            'password': 'secret123',
            'api_key': 'sk-1234567890',
            'token': 'ghp_abcdefg',
            'normal_field': 'normal_value'
        }
        sanitized = sanitize_dict(data)
        self.assertEqual(sanitized['username'], 'testuser')
        self.assertEqual(sanitized['password'], '***')
        self.assertEqual(sanitized['api_key'], '***')
        self.assertEqual(sanitized['token'], '***')
        self.assertEqual(sanitized['normal_field'], 'normal_value')

    def test_sanitize_nested_dict(self):
        """测试嵌套字典脱敏"""
        data = {
            'config': {
                'api_key': 'sk-1234',
                'timeout': 30
            },
            'headers': {
                'Authorization': 'Bearer token123'
            }
        }
        sanitized = sanitize_dict(data)
        self.assertEqual(sanitized['config']['api_key'], '***')
        self.assertEqual(sanitized['config']['timeout'], 30)
        self.assertEqual(sanitized['headers']['Authorization'], '***')

    @patch('studio.skill_executors.logger')
    def test_skill_executor_exception_sanitized(self, mock_logger):
        """测试技能执行器的异常脱敏"""
        from studio.skill_executors import OutlineGenerator
        from pathlib import Path

        # 创建模拟的 app 对象
        mock_app = Mock()
        mock_app.db = Mock()
        mock_app.skills_dir = Path(__file__).parent.parent / 'skills'

        # 模拟 generate_draft 方法抛出包含敏感信息的异常
        mock_app.generate_draft.side_effect = Exception("API Error: Authorization: Bearer sk-proj-sensitive123")

        # 创建完整的技能定义
        mock_skill_def = {
            'name': 'generate-outline',
            'version': '1.0.0',
            'description': 'Test skill',
            'metadata': {
                'version': '1.0.0',
                'author': 'test'
            },
            'path': Path(__file__).parent.parent / 'skills' / 'generate-outline'
        }

        executor = OutlineGenerator(mock_app, mock_skill_def)

        # 准备测试输入
        context = {
            'post': {'id': 1, 'title': 'Test', 'body': 'Test body'},
            'profile': {'name': 'Test User'},
            'model_route': {'model': 'test-model'}
        }
        instruction = "Generate outline"

        # 执行应该抛出 SkillError，消息已脱敏
        with self.assertRaises(SkillError) as context_err:
            executor.call_model(context, instruction)

        # 验证异常消息中不包含敏感信息
        error_message = str(context_err.exception)
        self.assertNotIn("sk-proj-sensitive123", error_message)
        self.assertIn("模型调用失败", error_message)

    @patch('studio.skills.logger')
    def test_skill_system_exception_sanitized(self, mock_logger):
        """测试技能系统的异常脱敏"""
        from studio.skills import SkillSystem
        from pathlib import Path

        # 创建模拟的 app 对象
        mock_app = Mock()
        mock_app.config = {'SKILLS_ENABLED': True}
        mock_app.skills_dir = Path(__file__).parent.parent / 'skills'

        skill_system = SkillSystem(mock_app)

        # 模拟 _execute_skill 方法抛出包含敏感信息的异常
        with patch.object(skill_system, '_execute_skill') as mock_execute:
            mock_execute.side_effect = Exception("Database error: password=supersecret")

            # 首先注册一个测试技能
            skill_system._registry['test-skill'] = {
                'metadata': {
                    'name': 'test-skill',
                    'version': '1.0.0',
                    'category': 'test',
                    'enabled': True
                }
            }

            result = skill_system.execute('test-skill')

            # 验证返回的错误消息已脱敏
            self.assertFalse(result['success'])
            self.assertNotIn("supersecret", result['error']['message'])
            self.assertIn("INTERNAL_ERROR", result['error']['code'])


if __name__ == '__main__':
    unittest.main()
