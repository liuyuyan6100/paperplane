# 技能协议 1.1

每个技能目录包含UTF-8的SKILL.md，YAML frontmatter必填name、version、description、category（非空字符串）。
name为小写字母、数字及连字符；enabled与deprecated为布尔值，默认true/false。
dependencies、tags只作为元数据，不代表已有依赖解析器。重复name只注册按路径排序后的第一项，并记录告警。

SkillSystem(app, skills_dir=None)扫描本地资源；app必须提供config及现有Studio接口。
enabled(name)、get_metadata(name)、get_skill_info(name)、list_skills(category=None, enabled_only=True)、reload_skills()供内部使用。
execute(name, **inputs)成功返回{success:true,data:结果,error:null,metadata:{skill,version,category,duration_ms,timestamp}}。
执行失败返回success:false、data:null、error:{code,message,details}。不存在或未启用的技能抛ValueError。
执行器实现BaseExecutor.execute(inputs)并注册到EXECUTORS；不会根据Markdown或模型输出加载任意Python代码。

所有模型技能必须传入冻结model_route。调用复用Studio.generate_draft的连接校验、任务来源记录和临时失败重试。
已有任务出错不绕过校验或再调用旧实现。提纲风险信号先于结构校验，非法结构化输出拒绝接收。

资源路径限定在对应目录内；提示词变量单次替换，素材里的占位符不二次展开。
技能不负责采用、送稿、公开发表、删除或新增账号凭据。持久化、并发和版本保护由既有Manager负责。

候选类技能plan-topics、extract-memories、review-performance共用StructuredProposalExecutor，输入为post、profile、model_route、instruction，返回{raw:模型原始文本}。模型任务分别沿用topic、chat、review，不新增配置项。现有业务解析器负责候选数量、原句和证据键校验，成功执行技能不等于业务结果已通过或已保存。
studio.skills.generate_structured在调用前选择技能或兼容实现，技能报错后不切换实现。任务快照和模型来源归属保持原业务约定；经验提炼为同步请求，冻结本次模型路由，不冒充持久化队列任务。
