"""
Assurance Ledger —— 给 Agent Skill 算审计就绪度的账本产品

三段分离是结构上最关键的设计决策：
  ARS_d 设计态（只算 C，跑之前就能出）→ 链路八段产证据 → ARS_r 运行态（L+R+D+F，跑完出）→ 闸门

评分坐标：ARS = f(skill_version, model, data_profile)
"""

__version__ = "0.1.0"
__author__ = "openJiuwen Team"
