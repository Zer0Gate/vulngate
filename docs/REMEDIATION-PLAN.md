# 后续整改与交付账本

实现基准：`cb86800abedef6e599d699389f2db14bf586d3cf`。
用户审阅基准：`ea23f63c96b11cfc52754185418c4e67f2829eb5`。
授权范围：实施 R01–R12，提交、推送、按门禁合并、发布和刷新插件。

状态仅沿证据推进：planned → implemented → validated → merged → released → installed。
一个阶段成功不代表后续阶段成功；每项关闭需记录 commit、测试及适用远端证据。

| ID | 工作包与验收要求 | 状态 / 证据 |
| --- | --- | --- |
| R01 | 新 PR；稳定 test/security 门禁；矩阵失败/取消/跳过阻断；审核合并 | partial：PR #8；1270221 的四组回归、test/security 及独立 CodeQL 均通过，open 告警为空；PR 仍为草稿、review required，合并/发布未完成 |
| R02 | 服务与 PoC 统一隔离生命周期；启动前限制；容器/cgroup 清理；真实环境压力与失败测试 | partial：源码 `f9836f0` 已推送，817 项本地通过（4 live skip）；run `36752932751` 四组矩阵/security/test 及独立 CodeQL 全绿，real-isolation job 真跑 4 项无 skip，验证健康隔离/限额/capabilities 与超时/清理失败重试。前两轮真实失败已修补并复验。PoC/服务统一 backend、observer 对接、非 root 变体、完整压力/断连/迟到创建和崩溃恢复未完成，不能关闭整个 R02 |
| R03 | 每次请求预留调用/token；网络与空响应 retry；并发；未知计费；deadline；成本 unknown | validated-source-tests：12 项专项、731 项当时全量通过；源码已包含在 f0b3819 的四组远端回归成功结果中。此证据是逻辑/回归测试，不虚构真实 provider 的计费测量或发布状态 |
| R04 | 安装事务、故障恢复、并发；不可变工具快照；旧线程不静默混版 | validated-source-tests：内容摘要版本目录、并发锁、持久化事务/恢复、启用后树校验；18 项专项和 748 项当时全量通过，源码已包含在 f0b3819 的四组远端回归成功结果中。正式包实际激活及旧线程行为仍待验收；见 INSTALLATION.md |
| R05 | 普通证据安全序列化；raw vault 默认关、权限与期限；错误路径敏感信息测试 | partial：普通 JSON/矩阵/CLI/账本共用投影与私有原子写入已推送，S4 policy v13；raw vault 默认关、1–30 天独立期限、0700/0600、可选 Fernet、显式清理及 CLI 固定状态回执均已推送；1270221 的远端矩阵、CodeQL 通过。旧产物、剩余自由文本及真包验收未完成，见 EVIDENCE-STORAGE.md |
| R06 | Run Manifest；树/配置/目标/镜像/policy 身份绑定；续跑漂移拒绝 | partial：源码 fc429d5，pipeline/autonomous 共用 create-once Manifest 与 checkpoint 父身份门禁，实际源码根/字节、显式 PoC/JAR、配置、工具树、已声明 Java 可执行文件、镜像 ID/policy 绑定；852 项本地成功，4 live skip，Ruff/mypy(7 模块)通过。一轮独立审阅的具体问题已加回归并修补。远端 run 36760403261 四矩阵/security/test/独立 CodeQL 全绿，真实隔离 4 项无 skip，open alerts=0；直接 matrix、cells/convergence/receipts、report 全链与校验到使用冻结仍未完成；见 RUN-IDENTITY.md |
| R07 | claim/observer/scope/predicate/controls；目标自写数据、无关 PID、截断不误确认或排除 | partial：HTTP 连接固定声明的数字 loopback、Host 由声明生成、TLS>=1.2 且校验 fixture 信任；异常连接清理。完整 observer 来源/归因/截断验收仍待完成 |
| R08 | 真 backend CI；风险模块分项 branch coverage；F821；依赖审查；失败样本验证 | partial：F821 已恢复；Ubuntu real-isolation 与固定 test 聚合门禁已实际执行，两次失败确实阻断，f9836f0 的 4 项真实 backend 验收通过；分项覆盖与依赖审查仍未完成 |
| R09 | 审核提交的不可变 tag；Release 归档/SBOM/checksum/provenance；干净安装 | planned |
| R10 | 三入口共享应用服务；build 等按职责拆分；同 fixture 结论一致 | planned |
| R11 | installed/probed/enforced 能力分层；语言/分析/observer/backend 矩阵；文案校准 | planned |
| R12 | 真漏洞/真负样本/未知/恶意自报；固定身份；误确认/误排除/pending/资源指标 | planned |

## 依赖与迁移

M0：R01。M1：R02 止损 + R03/R04/R05 和必需 R08/R12 最小端到端回归。
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

- 最新断点：源码 `aa235d7` 的 run `36787893737` 四矩阵、security/test 全部成功；
  real-isolation `110133584964` 真跑 4 项、8.022s、无 skip，独立 CodeQL SUCCESS，
  PR merge ref open alerts=0。本批嵌套限额兼容性通过远端验收；PR 仍草稿/审核待办，
  没有合并、发布或安装。状态文档后续新 HEAD 的 CI 需另外核对。
- 上一断点：bc7c077 的 run `36787181163` macOS 3.10 健康检查失败、test 正确阻断，
  其余三矩阵/real-isolation/security 通过；历史全绿不能代表当前 HEAD。确定性本地反例
  证实独立测量的 NPROC/AS 基线下降会让嵌套启动器尝试提高继承硬上限。共享启动器改为
  只取更严格上限，resource policy v8；854 项本地通过（4 live skip），Ruff/mypy 等通过。
  后续源码已按上条远端验收；Docker 曾失败的创建/身份/清理根因、R02 其余验收及 R06 下一层未关闭。
- PR #8：`https://github.com/Zer0Gate/vulngate/pull/8`，草稿，尚未合并/发布。
- `1d5f4fa` 的 CI run `36484103241`：Linux 3.10/3.13、macOS 3.13 和
  security job 通过；macOS 3.10 因 POSIX address-space preflight 被系统拒绝失败，
  固定名 test 汇总按预期失败。此结果证明汇总对真实失败关闭，但不证明全部矩阵通过。
- 同一 run 的 CodeQL 附加检查报告六条告警：common.py 两处产物写入、runtime.py
  日志输出、ledger.py 写入、http_observer.py TLS 版本及转发地址。纳入 R05/R08，
  需按来源和实际边界核验修复，不抑制告警以代替整改。
- 本地默认 python3 已变为 3.14，未装 Hypothesis；完整验收使用依赖齐全的
  `/opt/local/bin/python3`（3.13），不把环境缺包记成通过或代码失败。
- 安装事务本地验证：18 项专项覆盖中断恢复、并发、旧目录迁移、版本冲突、
  篡改、磁盘写入失败和错误缓存身份；748 项全量通过（66.2s），有既存 socket
  ResourceWarning，未将其隐去。测试使用临时目录和模拟 Codex，不代表生产插件
  已更新，也不证明真实断电/磁盘硬件的持久性。
- 安装事务提交 `a8f39d4` 已推送，远端 run `36507119356` 正在验收。
- F821 检查发现 build.py 残留未定义 `ev` 与两处类型导入缺失。移除任意自由
  文本证据作为 residual 副作用的旧分支、修复导入；11 项专项及 750 项全量通过。
  为 macOS 3.10 的 `ulimit -v` 失败增加 controller/native launcher 只读诊断；
  暂不凭猜测改变限额或忽略失败。
- Run `36507376708` 已结束：Linux 3.10/3.13、macOS 3.13 成功；macOS 3.10
  及 test 失败。诊断确认同为 arm64 时 bash VSZ（435299488 KiB）仍大于旧
  controller 派生上限（425127772160 bytes）。已实施 controller/launcher 双基线
  取最大值修复并将 resource policy 升为 v7；余量不变、测量失败仍关闭执行。
  46 项专项、755 项全量（64.5s）、Ruff/mypy 通过，远端验收待新提交运行；
  完整隔离 R02 尚未完成。
- `6736d6e` 的 run `36542833962` 全部 workflow jobs 通过，包括此前失败的
  macOS 3.10。此证据关闭该兼容性失败，不代表完整隔离或独立 CodeQL 无告警。
- HTTP observer 固定声明目标/Host、TLS 最低版本与 fixture 信任、异常清理：
  新增五项边界测试，包含真实 HTTPS fixture 的专项及本地全量 760 项通过
  （69.9s）。代码已待提交；CodeQL 需按新 HEAD 单独验收。
- R05 普通持久化：增加结构化投影和私有原子写入，覆盖 Java/Shell matrix、
  checkpoint、自治产物、账本与 CLI JSON；S4 policy v13。合成秘密、失败真值、
  可信 HTTP 观测读回、回执、续跑、回放包的测试及 782 项全量通过（Python 3.13，
  64.6s），Ruff/mypy 通过。独立审阅指出的异常正文和探针回复旁路已修复并回归；
  staging 原始诊断须显式授权加 --show-raw-output。此项提交后仍须核对远端
  macOS/Linux 矩阵与独立 CodeQL；raw vault 和剩余写入器未完成，不能关闭 R05。
- `ed84fb2` 的 CI run `36629640121`：security、四组 Linux/macOS 回归与
  `test` 汇总均成功；独立 CodeQL 仍失败，当前 open 告警 #7 指向
  `cli/runtime.py` 的通用 JSON 输出（源自 S4 观测/效果汇总），须处理真实敏感数据流，不以
  忽略告警代替整改。R05 raw vault 专项（含加密依赖实际安装）、最终 791 项
  全量（64.2s）、Ruff、mypy 已在后续本地改动上通过；新 HEAD 的远端
  矩阵和 CodeQL 尚未验收，不能计为远端通过。
- `7f6a209` 的 run `36631429940`：security、四组 Linux/macOS 回归和
  固定 `test` 汇总全绿；独立 CodeQL 仍有 #7。已在本地把 `matrix` CLI
  改成固定执行状态/计数回执，不再把完整观测摘要送往通用 JSON 出口；
  792 项本地全量（63.8s）、Ruff/mypy 通过；后续提交需以新 HEAD 验证
  CodeQL 真正通过。
- `1270221` 的 run `36632179141`：security、四组 Linux/macOS 回归、
  固定 `test` 汇总和独立 CodeQL 全部通过；PR merge ref 的 open 告警为空。
  PR 仍为草稿、review required；这个结果不证明 R05 全部写入器安全，也不
  替代审核、R02–R12 验收或正式发布。
