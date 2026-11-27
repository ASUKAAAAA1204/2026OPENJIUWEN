# TemisGuard 每日备份与签名检查点

备份由 `scripts/backup_db.py` 完成：先校验账本哈希链，再用 SQLite 在线备份复制
整库；配置了检查点私钥时默认生成 `.checkpoint.json`。恢复演练与导出一致，
都只验证签名与摘要，不修改原始字节。

## 1 准备密钥

```bash
python scripts/init_checkpoint_key.py --config configs/platform.toml
```

- 私钥 `data/keys/checkpoint-private.pem` 只留在受控主机或离线介质；
- 公钥 `data/keys/checkpoint-public.pem` 可随备份分发，用于独立校验；
- 已有私钥时脚本拒绝覆盖；轮换必须显式 `--rotate`，旧私钥先备份为 `.bak`；
- Windows 的 ACL 检查是 best-effort，POSIX 要求私钥权限 0600。

## 2 备份

```bash
# 自动用配置中的私钥签名
python scripts/backup_db.py --config configs/platform.toml

# 显式关闭检查点（不建议用于生产）
python scripts/backup_db.py --config configs/platform.toml --no-checkpoint

# 指定目标与显式私钥
python scripts/backup_db.py --config configs/platform.toml \
  --destination /secure/backups/platform-$(date +%Y%m%d).db \
  --checkpoint-key /secure/keys/checkpoint-private.pem
```

产物：

```text
data/backups/platform-<stamp>.db
data/backups/platform-<stamp>.db.checkpoint.json
```

备份与私钥必须分开存放；拿到私钥才能伪造检查点，只有备份没有检查点则无法
发现尾部截断。

## 3 校验

```bash
python scripts/verify_ledger.py --database data/backups/platform-<stamp>.db \
  --checkpoint data/backups/platform-<stamp>.db.checkpoint.json \
  --public-key data/keys/checkpoint-public.pem
```

- 退出码：0 PASS、3 FAIL、4 缺少或不适用；
- 检查项：签名有效、`count`/`head` 与库内链一致；
- 哈希链只证明内容一致，不证明来源身份，也不抵御同时控制私钥与管理员的攻击。

## 4 定时调度

### 4.1 cron

```cron
17 3 * * * cd /srv/temisguard && .venv/bin/python scripts/backup_db.py \
  --config configs/platform.toml >> /var/log/temisguard/backup.log 2>&1
```

### 4.2 systemd timer

`/etc/systemd/system/temisguard-backup.service`

```ini
[Unit]
Description=TemisGuard backup

[Service]
Type=oneshot
WorkingDirectory=/srv/temisguard
ExecStart=/srv/temisguard/.venv/bin/python scripts/backup_db.py --config configs/platform.toml
```

`/etc/systemd/system/temisguard-backup.timer`

```ini
[Unit]
Description=Daily TemisGuard backup

[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

### 4.3 Windows 任务计划

```powershell
schtasks /Create /TN "TemisGuard Backup" /SC DAILY /ST 03:17 /RU SYSTEM `
  /TR "E:\Dev_Project\2026OPENJIUWEN\.venv\Scripts\python.exe E:\Dev_Project\2026OPENJIUWEN\scripts\backup_db.py --config E:\Dev_Project\2026OPENJIUWEN\configs\platform.toml"
```

## 5 恢复演练

每次演练把结果写入 `outputs/backups/drills/<stamp>.json`，至少记录：备份路径、
检查点路径、`verify_ledger` 退出码、事件数与链头、耗时。演练不覆盖原库：

```bash
python scripts/verify_ledger.py --database <backup.db> \
  --checkpoint <backup.db.checkpoint.json> --public-key <public.pem>
```

只有校验为 PASS 的备份才允许进入恢复流程；恢复前先复制当前库，失败可回滚。
