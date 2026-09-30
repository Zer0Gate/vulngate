# Run Manifest 与续跑身份（R06 增量）

配置 pipeline 和 autonomous round 在读取 S1–S8 checkpoint 前，使用同一
`RunManifest.collect` / `bind_round` 边界。首轮原子创建
`state/<target>/round-NN/S0/run-manifest.json`；同一轮不允许覆盖该记录。
不支持身份绑定的旧轮次保留只读检查，不会被静默升级为可执行的当前证据。
`--force` 只重跑同一身份下的阶段，不能解除身份不匹配。

## 本批实际覆盖

- 插件版本、Git revision（非 Git 安装明确为 `non-git`）、plugin.json 原始字节摘要。
- 实际工具内容摘要：开发 checkout 与安装器使用同一 PAYLOAD 定义；安装树
  必须通过 content-manifest 完整校验，失败不回退开发模式。
- Python/OS、policy 版本、完整操作员配置和入口执行选项摘要。
- 目标源码 revision 加真实字节快照，包括 dirty/untracked 文件和可执行
  缓存；不把相同 HEAD、大小或 mtime 当成内容不变的证明。
- 显式 JAR/deps、历史 build artifacts 和反馈输入的摘要。
- 实际调用者的源码根（pipeline workspace / autonomous prepared target），
  显式操作员 PoC 源目录，以及共用选择器解析出的 Java/javac 字节摘要。
- 启用服务的本地容器 image ID（只 inspect，不 pull/launch）；禁用服务不
  要求存在容器引擎。backend 字段当前是请求值，不是 enforced 能力证明。
- checkpoint 保存父 `manifest_sha256`，恢复前检查全部已完成阶段；未绑定
  或父身份不符即拒绝。源码/工具哈希消费 round 的既有 WorkBudget。

Manifest 只保存摘要及版本标识，不保存原始配置/环境秘密。0600 文件、0700
目录与目录句柄 I/O 沿用 EvidenceStore；并发首次绑定只有一个完整记录获胜。
JSON Schema 为 `schemas/run-manifest.json`，运行时仍只依赖标准库。

隐式 workspace-root 审计排除控制器拥有的 `state/ledger/reports/poc` 输出目录
及 Git 元数据。若这些名字实际是目标源码，必须显式配置 `source_dirs`，此时
不会按输出目录忽略。源码 symlink/特殊文件目前拒绝，而非跟随未知范围。

pipeline 的调度/丰富候选只在本轮配置副本中变化，原操作员配置保持不变，
以免本轮生成候选污染下一次身份比较。不同入口的执行选项并不默认等价；
切换入口/身份需新轮次，不把已有预算、身份或历史证据改写成另一轮。
自动学习的 `api_hint` 保存为 S1 派生指导，不再回写操作员配置；同一身份
续跑可读回该指导。identity 拒绝由两个 CLI 以非零退出码传达。

## 明确尚未完成

整个 R06 仍为 partial，不能用上述 checkpoint 门禁推断下列边界已关闭：

- 直接 stage/helper 和 matrix CLI 的完整执行身份入口。
- 原始 cells、fallback/sequential convergence、parallel challenge/receipt 的
  父身份和产物字节绑定；普通 dict/list 形状目前保持兼容，不随意插入字段。
- S4/S8/report/ledger 的全链父身份；旧产物的非创建只读入口仍需逐一审计。
- 镜像解析结果到实际 launch 的固定 image ID，以及工具/源码在校验到使用
  期间的 immutable snapshot/漂移门禁；一次 hash 不等于执行中冻结输入。
- 实际选用 backend/运行时依赖身份与 enforced 证据，不把请求值当能力证明。
- 配置中尚未出现的派生 runtime lane，以及 JDK 动态库/系统 wrapper 真正选择
  的实现等完整依赖闭包。已绑定可执行文件字节不等于整个工具链已冻结。
- 正式安装包携带可验证源码 commit 的 provenance（非 Git 安装不能虚构 commit）。

身份摘要不是签名或独立 observer。目标自写/可篡改 evidence 仍需 R07 的来源
与归因验证；这里不会允许 stdout 自报升级为确认或排除。
