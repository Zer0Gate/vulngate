# 普通证据存储（R05 进行中）

S4 PoC 与外部进程的 stdout/stderr、编译/运行错误、命令行和 marker 值均可能
由目标控制。`scripts/agent/memory/evidence_store.py` 在写入时复制并投影普通
JSON 产物：这些通道以非空占位符替换；空值保持空值，因此编译失败、沙箱失败等
仍阻止把 cell 判为成功。PoC claims 保留字段数量，不保留原始键和值。
观察器异常缺口仅保留会影响判定的固定代码；独立效果保留类型、状态和摘要，
移除可能含文件名、对象路径或异常正文的详情。未知对象不会用 `str()`/`repr()`
强制写入。普通 `error` 正文、失败的 runtime-lab `reason` 以及探针的
`agent_reply` 也会被隐藏，失败状态和错误类型仍保留。固定格式的回执缺件错误
可保留，以维持自动化调用者的错误判断。
从不可信 marker 派生的 summary `network_side_effects`、`parsed`、类名、
效果详情与实验跟踪文本只保留存在性/条目数，不把原文复制到普通输出。

配置驱动 CheckpointStore、自治 write_artifact、Java/Shell cells.json、账本、
报告摘要与 CLI JSON 输出已接入同一投影。读回 JSON 与实时 cell 的汇合使用
同一投影比较，但优先保留内存中的原始诊断，供当轮修复流程使用。可信 HTTP
观测保留验证所需的状态、摘要和来源字段；若敏感数据出现在 cell 身份字段，
写入拒绝而不会静默改写身份。S4 证据策略升至 v13，旧策略的阶段缓存需要重算。
`matrix` CLI 的 `candidates` 输出现只提供 `execution-state-counts-v1` 固定状态与
计数，不再把完整候选摘要和预算失败正文复制到终端 JSON；详情须从受控
矩阵产物进入后续判定流程，不能把终端回执当作漏洞确认。

文件写入在选定 workspace 内逐级通过目录描述符完成，拒绝路径逃逸与后代
符号链接；新目录权限 0700，新文件权限 0600；唯一临时文件、fsync、原子替换。
权限设置或写入失败会报错，不会继续报告保存成功。此隔离不抵御同 UID 或 root
宿主进程，也不能代替 PoC 沙箱。托管服务 backend 另将 `/workspace/state` 覆盖为
临时文件系统，并把宿主 `ledger/`、`reports/`、`poc/` 只读挂载；这是针对目标服务的
容器边界，不防护其他以同一宿主用户运行的进程。

CLI 的 staging-exec/staging-copy 默认输出隐藏远端 stdout/stderr，但保留退出码。
经过明确 `--authorized-staging` 的操作者可以额外使用 `--show-raw-output`，
将远端诊断写到终端 stderr 供现场排障；它不会成为漏洞证据，也不会写入普通
JSON。此选项可能把远端原始文本带入终端记录，使用者应按自己的日志策略处理。

S4 原始矩阵诊断另有 opt-in vault；未设置 `VULNGATE_RAW_VAULT` 时默认关闭，
普通 `cells.json` 仍只写安全投影。操作者可在启动矩阵进程前显式设置
`VULNGATE_RAW_VAULT=plain`，或设置 `VULNGATE_RAW_VAULT=fernet` 并提供
`VULNGATE_RAW_VAULT_KEY_FILE` 指向本人拥有的 0600 Fernet 密钥文件。
加密依赖可通过 `.[vault]` 安装；缺依赖、密钥无效、权限过宽及无效模式均报错，
不会回退明文。`VULNGATE_RAW_VAULT_DAYS` 可设为 1–30，默认 7。
每条记录独立写入 `state/<target>/round-N/S4/raw-vault/`，目录 0700、
文件 0600。文件名固定记录创建和过期时刻；缩短或延长当前配置均不会改变
旧记录的期限。读取已过期记录会被拒绝，写入新记录时自动清理同目标/轮次
已过期的记录。若没有后续写入，须按保留策略运行：

```sh
python3 scripts/agent_cli.py raw-vault purge-expired \
  --workspace <workspace> --target <target> --round <round>
```

清理不需密钥，因此密钥遗失时也可执行。该命令仅删除指定目标/轮次、
名称和期限格式有效的过期记录；不会遍历其他目标。**没有后台定时删除**，
严格物理删除需要操作者按周期调度该命令。vault 的读取入口目前是
`RawVault.read(record_name)` Python API，不会在普通 CLI 或证据报告里输出
原始内容。明文模式仅提供文件权限隔离，不抵御同 UID/root 进程；运行在共享
环境应选用加密模式，并妥善备份/轮换密钥。过期后不会因为换密钥或调整保留
天数恢复读取。

这是 R05 的部分实现，不是 R05 验收完成。特别仍需：

- 其余历史写入器、审批 JSONL、回放包及源码/自由文本报告逐项迁移与验收。
  正则脱敏只是附加层，不能证明任意业务文本、路径、源码片段或编码值安全。
- 已存在的旧原始产物不自动删除；须先确定留存/取证策略，再进行迁移与清理。
- 确认所有持久化与 CLI 错误路径、最终安装包和真后端回归；后续改动仍须
  维持当前 HEAD 的 CodeQL 通过状态。

专项测试：`tests/test_evidence_storage.py`；兼容性测试涵盖 S4 真实 HTTP 观测
读回、结论、spawn/receipt challenge、续跑与 replay pack。所有用例只使用合成
假秘密，不采集真实令牌。
