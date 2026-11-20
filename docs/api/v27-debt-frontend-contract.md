# v2.7 债务前端接口草案

状态：前端适配已实现，后端HTTP接口尚未实现或确认；没有真实联调通过的证据。条目字段和消解方法对齐src/assurance_ledger/engine/v27/models.py、debt.py，HTTP封装为本草案新增。

GET /api/v27/debts建议返回{source:'ledger',can_resolve:boolean,items:DebtItem[]}。DebtItem字段为debt_id/run_id/decision_id/mark_type/original_severity/display_severity/weight/status/cause/provenance/opened_seq/resolved，见ui/src/components/workbench/debts.ts。服务端继续负责确定性debt_id、账本哈希及权限范围验证。前端按(owner run_id,decision_id,mark_type)去重：完全相同的重复引用保留一份，同键冲突或同ID不同所有权拒绝汇总；同决策多类型分别计条，按所属运行分列。

第53轮补充运行元数据 `runs: DebtRun[] | null`。每项包含 `run_id`、`parent_run_id`（根为null）、`review_threshold`（非负有限数字，与引擎允许0/小数一致）、`review_status`（queued/not_queued，服务端权威快照）、`final_reading`（null或`{total,count}`，结束时该运行自身的债务权重与未消解条数）。返回数组时必须覆盖所有条目的所属运行及完整祖先，可包含没有债务的运行；运行ID唯一，无循环，委派最多3层。结束读数是历史事实，不随当前消解重算；总权重应介于条数和条数的3倍之间。

运行卡片分列自身当前债务、全部后代当前债务及两者汇总；页面顶部仅按原始所有权求和，不能把父级汇总值与子级值再次相加。复核阈值按运行自身债务判断；达到阈值而快照未确认入队时提示刷新核对，不能在浏览器伪造入队。消解成功不擅自移除服务端复核队列记录。`runs` 缺失或null可读取原始条目，但必须明确关系/复核/结束读数未知；不得从运行ID名称猜关系。部分关系、循环、越深、所有权缺失或无效读数拒绝汇总。

未消解权重按原始等级high3/mid2/low1，不能改用下游呈现等级。UNKNOWN_DEPENDENCY保持原始等级。provenance为来源集合，不代表有序传播路径，界面不得自行画成顺序链。

POST /api/v27/debts/{id}/resolve建议请求：method、evidence_ref{evidence_id,content_hash}、regate_decision_id（仅SPEC_MISMATCH，否则null）、expected_opened_seq、request_id；Idempotency-Key与request_id一致。返回更新后的单个DebtItem。服务端必须核验证据内容/归属和权限、状态、幂等，并将消解事件追加到账本。

三类method严格对应：NO_EVIDENCE=supplement_evidence；SOURCE_CONFLICT=unify_sources；SPEC_MISMATCH=correct_params_and_regate。SPEC_MISMATCH的regate_success必须由服务端关联重新过闸决策核验后返回true，其他类型为null。前端不提交一个用户可任意勾选的真实regate_success事实。返回resolved包含seq、method、evidence_ref、regate_success，seq必须晚于opened_seq；原类型、原等级、权重、原因、来源、所有权与原始序号不得改写。

401/403/404/409/422分别显示会话、权限、不存在、冲突、证据或重判失败；其他服务/网络错误保留原债务并停止继续提交，要求刷新确认后重新核对。加载和提交12秒超时、离开或切来源取消旧请求；错误不切换到演示数据。身份与CSRF方案待认证契约冻结，不能将前端按钮禁用当作服务端授权。

演示只执行表单校验与内存投影，任意格式正确的示例指纹不构成真实证据核验。样例保留3条原始登记，后续消解追加较大seq的投影信息；刷新恢复同会话状态，整页刷新重置。前端不是账本持久化介质。

证据：tests/debts.spec.ts和e2e/debts.spec.ts。截图docs/reviews/ui/quality-round-5来自合成样例；网络成功/失败流程使用Playwright拦截，不是实际后端。测试覆盖去重、原始权重、方法/重新过闸约束、追加历史、原始条目不变、422拒绝、返回数据篡改拒绝、刷新重新核对及四档布局。

第三十八轮：债务消解提交状态同步至工作台，等待期间阻止主导航、新建任务及历史导航切换，提示保留当前页面；失败后解锁导航但不解除债务或结果未知的重新核对要求。关闭浏览器不能撤销已提交操作，服务端幂等与查账仍必要。

第57轮：证据标识、内容指纹或重新过闸决策标识修改时立即清除旧范围确认。原确认不能授权修改后的证据范围；表单校验与提交适配器仍共同检查confirmed，不依赖按钮状态。
