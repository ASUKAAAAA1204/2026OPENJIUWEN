# 第四十轮前端验收

## 范围

账本创建 POST 等待登记回执期间，将忙碌状态由 `RunCreatePanel` 经 `TaskWorkspace` 传递到 `WorkbenchShell`。此时工作台导航、新建任务、链接和浏览器历史跳转会保留当前工作区；成功回执后恢复导航。此保护只避免前端离开导致响应丢失，不改变服务端创建语义。

## 验证

- `npm run verify`：通过；39 个单测文件共 153 项、79 项浏览器测试、1 项生产性能测试。
- 类型检查、ESLint、Stylelint、契约校验、生产构建和体积预算通过。
- 新增延迟回执浏览器用例：请求提交后尝试主导航、新建任务和 `popstate`，确认保留工作台、任务标识及地址；登记成功后确认导航解锁。
- Playwright 路由提供合成回执，性能门禁期间后端 `127.0.0.1:8000` 不可用；不能把本轮结果视为真实服务联调。
- `run-create-navigation-1440.png`、`run-create-navigation-1024.png`、`run-create-navigation-390.png`、`run-create-navigation-320.png` 已逐张人工查看。通知在桌面宽度保持单行，窄屏正常换行且关闭按钮没有遮挡正文。

完整交付评分保持 30/100，尚未满足 95 分门槛。
