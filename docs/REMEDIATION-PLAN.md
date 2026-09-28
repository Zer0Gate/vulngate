# 后续整改与交付账本

实现基准：`cb86800abedef6e599d699389f2db14bf586d3cf`。
用户审阅基准：`ea23f63c96b11cfc52754185418c4e67f2829eb5`。
授权范围：实施 R01–R12，提交、推送、按门禁合并、发布和刷新插件。

状态仅沿证据推进：planned → implemented → validated → merged → released → installed。
一个阶段成功不代表后续阶段成功；每项关闭需记录 commit、测试及适用远端证据。

| ID | 工作包与验收要求 | 状态 / 证据 |
| --- | --- | --- |
| R01 | 新 PR；稳定 test/security 门禁；矩阵失败/取消/跳过阻断；审核合并 | implemented：test 汇总门禁；远端待运行 |
| R02 | 服务与 PoC 统一隔离生命周期；启动前限制；容器/cgroup 清理；真实环境压力与失败测试 | planned |
| R03 | 每次请求预留调用/token；网络与空响应 retry；并发；未知计费；deadline；成本 unknown | validated-local：12 项专项、731 项全量（Python 3.13）；Ruff/mypy/compileall 通过；远端矩阵待通过 |
| R04 | 安装事务、故障恢复、并发；不可变工具快照；旧线程不静默混版 | partial：移除旧缓存 alias；修复启用前 EXIT trap 被覆盖；三项安装回归。并发锁、完整事务恢复及不可变快照仍待实现 |
| R05 | 普通证据安全序列化；raw vault 默认关、权限与期限；错误路径敏感信息测试 | planned |
| R06 | Run Manifest；树/配置/目标/镜像/policy 身份绑定；续跑漂移拒绝 | planned |
| R07 | claim/observer/scope/predicate/controls；目标自写数据、无关 PID、截断不误确认或排除 | planned |
| R08 | 真 backend CI；风险模块分项 branch coverage；F821；依赖审查；失败样本验证 | planned |
| R09 | 审核提交的不可变 tag；Release 归档/SBOM/checksum/provenance；干净安装 | planned |
| R10 | 三入口共享应用服务；build 等按职责拆分；同 fixture 结论一致 | planned |
| R11 | installed/probed/enforced 能力分层；语言/分析/observer/backend 矩阵；文案校准 | planned |
| R12 | 真漏洞/真负样本/未知/恶意自报；固定身份；误确认/误排除/pending/资源指标 | planned |

## 依赖与迁移

M0：R01。M1：R02 止损 + R03/R04/R05 和必需 R08 回归。
M2：R06 → R07，同时完成 R02/R08 的真实隔离验收。
M3：R09 可信发布；R10/R11/R12 小 PR 持续推进。

保留 public CLI 与旧产物只读兼容；不支持身份一致性的旧 checkpoint 不静默续跑。
未验证 backend 拒绝执行；普通存储不以正则能识别所有隐私为前提。
回滚到最后已验证提交，不恢复不安全执行或旧缓存身份指向新内容。
保留 Python >=3.10 当前承诺，抬升基线与正式版本号按实际兼容性单独决定。

## 尚需真实测量

固定 fixture 的冷/热 p50/p95、RSS、磁盘占用和清理时延；实际环境建立基线后确定
性能门槛。安全关键反例要求零误判，环境缺失必须明确 pending/unsupported。

## 远端交付记录

- PR #8：`https://github.com/Zer0Gate/vulngate/pull/8`，草稿，尚未合并/发布。
- `1d5f4fa` 的 CI run `36484103241`：Linux 3.10/3.13、macOS 3.13 和
  security job 通过；macOS 3.10 因 POSIX address-space preflight 被系统拒绝失败，
  固定名 test 汇总按预期失败。此结果证明汇总对真实失败关闭，但不证明全部矩阵通过。
- 同一 run 的 CodeQL 附加检查报告六条告警：common.py 两处产物写入、runtime.py
  日志输出、ledger.py 写入、http_observer.py TLS 版本及转发地址。纳入 R05/R08，
  需按来源和实际边界核验修复，不抑制告警以代替整改。
- 本地默认 python3 已变为 3.14，未装 Hypothesis；完整验收使用依赖齐全的
  `/opt/local/bin/python3`（3.13），不把环境缺包记成通过或代码失败。
