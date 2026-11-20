# v2.7 配置快照前端契约草案

`GET /api/v27/settings`，同源凭据，只读。现有HTTP服务尚未实现，浏览器测试使用合成拦截响应。默认本地预览不包含认证身份、生效策略或工具配置；不授予权限。

结构定义于 `ui/src/components/workbench/settings.ts`：source=ledger、revision、identity、policy、whitelist、frameworks。

- identity：user_id、display_name、role、scope；没有已认证身份时null。role仅接受PRD五种角色；scope为服务端实际可见范围，客户端不得据此绕过每次请求授权。
- policy：version、64位小写SHA-256 content_hash、rules、history；未返回时null。规则区分Gate/Exec/risk，ASK含超时秒数和策略；高危/中危超时不得ALLOW。非ASK的超时字段为null。规则按决策类唯一；空规则不得解释为允许所有操作。history记录版本、操作人、含时区时间及原因。
- whitelist：版本、唯一决策类列表（最多11类）、唯一工具名及read_only/mixed/write能力与受控工作目录。未返回时null。此处只读，不修改真实执行边界。
- frameworks：openJiuwen、LangGraph、OpenAI Agents SDK各一项，含version、status verified/unverified/unavailable、checked_at及reason。verified必须有版本与核验时间，openJiuwen还需锁定0.1.20.post1。接入选择器不构成验证证据。

策略切换适配见 `docs/api/v27-policy-activation-frontend-contract.md`。配置快照仍只读；管理操作仅切换已发布版本指针，不编辑历史配置。

401提示会话失效；403提示无权限；404提示接口未提供；5xx和网络失败显式重试。12秒超时，刷新/来源切换取消旧请求，卸载后不更新。错误不会回退为演示成功或清空权限成功态。

界面展示PRD v2.7角色矩阵作为设计基准，明确区分它与当前账号授权。登录/退出尚需独立会话契约；不使用本地角色选择器伪造认证。生产服务必须在读取前脱敏、按角色隔离；返回的版本和哈希只是核对字段，不是客户端真实性认证。

已验：规则字段与危险超时、默认无身份、框架状态约束、实际范围渲染、403恢复、策略版本及变更记录、四档截图和严重/关键axe检查。真实认证、权限隔离、内容指纹真实性、框架真实联调仍未验收。
