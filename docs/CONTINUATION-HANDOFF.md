# VulnGate 整改续接记录（2026-09-29）

## 2026-10-01 增量：R02 健康检查与 backend 清理候选（最新断点）

独立审查已完成（同一轮，未再次派审）：确认 12 KB digest 截断、镜像引用
未纳入授权摘要、root capability/健康进程限制缺口、迟到容器创建清理不确定。
当前后续增量：digest 哈希完整序列化内容、授权绑定 backend 的镜像引用；
Linux 服务 drop ALL，健康进程经 setpriv 清除 bounding/inheritable/ambient
能力并设置 no_new_privs；健康命令在容器内部也套资源限额；容器 spec 额外
设置 core/fsize/nofile 上限。已启动但 ID 始终未知时，空列表不再当作清理成功，
保留 token/锁供后续重试；若迟到 ID 可见，再删除并确认消失。此保守状态可能
需要人工/后续恢复流程，不虚构“创建已经结束”。policy 升 service v5/S4 v17。
51 项聚焦测试、Ruff/mypy 通过，新增真实测试核验 CapEff/CapBnd/CapAmb/CapInh、
NoNewPrivs 及服务/健康进程 64 MiB file-size limit；真实结果仍待 CI。
镜像标签漂移/实际 resolved ID 固定和 crash 后恢复仍属 R06/R02 剩余工作。
本地新增反例后的 817 项全量（69.6s）通过，4 项 live skip；随后把 workspace
根路径统一映射成 `/workspace`（而非 `/workspace/.`），最后 51 项聚焦、
Ruff/mypy 通过。上游会把 chdir 参数原样设置为 PWD，因此前者是 Linux
fixture 的确定字符串不一致来源；仍须新 CI 实测，不能断言所有失败已解决。

`d87ee1f` 的 run `36751174798` 仍失败：Linux identity handshake 已通过，但
service exited=1，另一次容器就绪也失败；job `110009879180`。现增加仅 live
synthetic fixture 的有界 stderr 诊断以获取实际失败点；生产输出仍抑制。
Ubuntu 3.10 job `110009879100` 的 vault 篡改测试失败，是改 token 最后 base64
字符可能只改变未使用 padding bits，解码内容不变；反例改为真实翻转已认证
密文字节后重新编码，不更改/弱化 vault 认证。其余矩阵与 security 通过，
test 按门禁失败。不能以失败重跑或模拟通过宣布完整验收。

后续真实 CI `36750396860`：四组回归及 security 成功，但 real-isolation job
`110007230705` 的 bubblewrap 三个场景失败；真实容器就绪/健康超时/清理失败
重试场景通过。固定 test 按预期失败，未跳过失败用例。
上游 bubblewrap 的 info-fd 在 mount/chroot 完成前发出，PID 消息本身不足以
证明 sandbox 已就绪。新增只由 sandbox 内固定隔离 Python 启动器发出的
pipe 握手，收到后才打开 namespace/root/cwd；命令在此启动器关闭管道后 exec。
增加“仅 PID 消息不得打开上下文”的反例，40 项聚焦测试、Ruff/mypy 通过。
service policy 升 v4、S4 policy 升 v16；本增量仍需新 CI 证明真实 Linux 就绪。
失败日志只给出 PermissionError，因此该时序缺陷虽由上游源码证实，尚不能
断言是远端失败的唯一原因。独立审查仍未完成。

本文件所在提交继续 `5045c8c`，不重做已有整改。托管命令式健康检查不再
使用宿主 CommandRunner：Linux 从 bubblewrap info-fd 获取实际 sandbox 子 PID，
固定 namespace/root/cwd 句柄后进入同一 cgroup；容器通过本轮随机 label
核实 immutable container ID，再在该容器执行健康命令。缺少身份/nsenter
时拒绝执行，不回退宿主。workspace 绝对参数/环境路径映射为 `/workspace`；
宿主 launcher 不继承目标启动钩子。授权摘要纳入环境变量值；后端身份失败
不会被写成 enforced。停止须核实本轮容器消失，失败保留锁与重试状态。
S4 evidence policy 升 v15，service policy 升 v3，旧 checkpoint 不静默复用。

最终本地 Python 3.13 全量：813 项、70.6 秒，成功但 **4 项真实后端测试跳过**；
Ruff、mypy（6 个安全模块）、branch governance validator、diff-check 通过。
不能把上述结果当成真实 Linux/container 验收。本机为 macOS，Docker socket
仍不存在。新增 `tests/test_isolation_backend_live.py` 和专用 Ubuntu
`real-isolation` job：真实 namespace/cgroup/容器、宿主 canary 不可见、
cwd/env 映射、脱离 session 后代、健康超时整单元停止、清理失败保留锁与重试。
显式启用后工具缺失不得 skip；固定 `test` 汇总要求 regression 和
real-isolation 均 success，失败/取消/跳过反例已本地验证。

独立只读候选审查 `01a0f342-4284-7140-a466-5166c69c182f` 尚在运行，
未取得终审结果。此提交供 PR #8 远端验收，不是 fixed/validated 声明。
恢复时先读该审查结果及本提交 CI，处理实际失败后再验收；不要重启仍活着的
审查或仅因观察超时当作完成。R02 的 PoC/服务统一 backend、完整压力/断连
测试仍未完成，R01–R12 全目标继续 active；未合并、发布或重装插件。

已重新核实上一 HEAD `15723b9` 的 run `36658221586` 全部 workflow jobs 成功，
PR merge ref open CodeQL 告警为 0。main 保护读回：test/security required、
1 approval、enforce_admins=true；PR #8 仍 draft/review required。这些是旧 HEAD
证据，不得替代本候选的新增真实隔离验收。

```sh
git status --short --branch
gh pr view 8 --json headRefOid,isDraft,state,reviewDecision,statusCheckRollup
gh run list --branch Zer0Gate/vulngate-release-1.3.0 --limit 5
PYTHONPATH=scripts /tmp/vulngate-vault.bQGImB/bin/python -m unittest discover -s tests -t tests -q
```

## 2026-09-30 增量：R02 cgroup 启动/清理止损（最新断点）

`5045c8c97ac335f13650b5ba6c9dc868cf1333ea` 已提交推送至 PR #8 的
`Zer0Gate/vulngate-release-1.3.0`。该提交是 R02 的**部分整改**，不是完整隔离验收：
Linux 服务先启动仅能等待管道消息的受信门控进程，核验其 cgroup 成员身份后才
发送目标命令；预检与宿主侧 bubblewrap/container 客户端不再继承目标控制的
`BASH_ENV`/动态加载等环境。cgroup 使用每次启动独立目录、限额读回、
`cgroup.kill`、`cgroup.events` 清空确认及删除失败显式上报；S4 保留清理失败状态。
S4 证据策略升 v14，使旧 checkpoint 不按新隔离契约静默复用。

本地 800 项全量及最后 15 项聚焦测试通过，Ruff、mypy、compileall、
`git diff --check` 通过。测试覆盖 BASH_ENV 启动前触发、目标环境在附着后生效、
附着失败不执行目标、cgroup 清理失败与 PID 采样不确定、S4 清理失败回执。
这只是模拟 cgroup 验证：当前宿主为 macOS，Docker daemon socket 不存在，
尚无真实 Linux cgroup/container 压力和失败注入结果。PR 仍是草稿、需要审核；
本提交的 CI run `36658131331`（Linux/macOS 3.10/3.13、security、test）
已启动，记录时 Ubuntu 两组及 security 正在运行、macOS 两组排队；独立
CodeQL 仍须按新 HEAD 核验，不能沿用 `701189e` 的全绿结果。

**R02 下一步**：命令式健康检查在服务启动后仍可经宿主 runner 执行，未与服务
共用隔离边界；容器客户端/容器本体的停止与残留清理也需逐项证明。先封闭
健康检查的 backend 路径与目标环境注入，再做 Linux 真 cgroup、容器断连/派生
后代、清理失败压力测试；随后才可考虑 R02 validated。PoC 与服务统一 backend
仍未完成。不要把本部分提交、模拟测试或绿色 CI 误判为 R02 完成。

## 2026-09-30 增量：R05 raw vault（优先于以下历史断点）

普通证据补丁已提交推送为 `ed84fb2e74c4a77e1b26206e0e8118e1bb4df5dc`。
run `36629640121` 的 security、四组 Linux/macOS 回归与固定 `test` 汇总全部
成功；独立 CodeQL 仍失败，当前 open 告警 #7 为 S4 观测/效果数据流至 CLI
通用 JSON 输出（`cli/runtime.py`），不能宣布门禁通过或合并。PR #8 仍为草稿、待审核。

当前工作树在该提交之上继续 R05：新增 `memory/raw_vault.py`，Java/Shell
矩阵写入前显式 opt-in 保存原始 cells；默认关闭，私有文件、每条记录固定
1–30 天期限、过期拒读/范围清理、可选 Fernet 且配置/依赖/密钥失败不回退明文；
新增 `raw-vault purge-expired` 操作入口及文档。隔离环境实际安装
`cryptography==50.0.1` 后，含最终清理入口的 791 项全量（64.2s）、
Ruff/mypy 通过。本增量已推送为 `7f6a2099fa4fafb1fbfdd6066744319b9ecd9fb4`；
run `36631429940` 的 security、四组回归及 `test` 汇总全部成功，但独立
CodeQL 当时仍失败（#7）。后续 `1270221bdc86ac5bd2694f98be3ae818556877ff`
已推送：`matrix` CLI 仅输出固定执行状态/计数与有界预算回执，不复制含原始
marker 的完整 summary；792 项本地全量（63.8s）、Ruff/mypy 通过。
新 run `36632179141` 的 security、四组回归、`test` 汇总及独立 CodeQL
全部成功；对 PR merge ref 查询的 open CodeQL 告警列表为空。PR #8 仍为草稿、
review required，不应合并/发布。`docs/EVIDENCE-STORAGE.md`
说明 opt-in、权限、期限和清理限制。R05 仍为 partial；旧产物、自由文本、
审批日志和其余写入器未完成。R01–R12 总体剩余范围见本文件尾部和
`docs/REMEDIATION-PLAN.md`，不得因为本增量收缩目标。恢复时先查看最新
Git/PR/CI，从未完成的 R05 数据流审查继续，不重做 `ed84fb2`。

## 2026-09-30 增量：R05 普通持久化候选补丁（以实际 Git 为准）

已在上次 `d271f13` 交接断点后实施共用普通证据投影与私有原子写入，
覆盖 CheckpointStore、AutoCtx、Java/Shell matrix cells、ledger、S4 证据行及
CLI JSON 输出；S4 policy 升 v13。新专项 `tests/test_evidence_storage.py`。
此前全量 775 项中仅新增的真实 HTTP 读回断言失败（可选空谓词字段被强加），
现已修补。最终 782 项本地全量（Python 3.13，64.6s）、Ruff/mypy 通过；
独立审阅的异常正文、探针回复旁路与 staging 排障兼容性已修复并回归。
仍需核对本次补丁的提交推送及远端结果；不得以此关闭 R05。raw vault、保留期限、旧产物及剩余自由文本写入
路径仍未完成。详细契约见 EVIDENCE-STORAGE.md。以下旧 R05“尚未开始补丁”
仅是历史快照，不再代表当前状态。

## 最新交接：额度窗口 1790688162（优先于下方历史记录）

本窗口已交接：`resetsAt=1790688162`。应用最新读数五小时已用 95%、剩余 5%，
北京时间 2026-09-29 21:22:42 重置。自动提醒 id 为 `vulngate`，不要为此窗口
重复生成相同交接。未使用重置券，完整目标仍未完成，未暂停或标记 blocked。

### 已核实的当前状态

- 功能 HEAD：`d9d6193da1efeb5ed030738e7a7a623b335be3b0`，本地与远端一致。
- 分支 `Zer0Gate/vulngate-release-1.3.0`；交接前工作区干净。
- 远端 main 仍为 `ea23f63c96b11cfc52754185418c4e67f2829eb5`。
- PR #8 OPEN、草稿、未合并。本批修复未发布新版、未刷新实际插件缓存。
- 本轮没有编辑源码，没有运行中的测试/子代理；本次仅提交此交接文档。
- `d9d6193` 的 run `36543313262` 已全部 workflow jobs SUCCESS：
  Ubuntu 3.10/3.13、macOS 3.10/3.13、security、test。
- 独立 CodeQL check `109324423984` 为 FAILURE；当前 PR open alerts 剩四条：
  #2/#3 `autonomous/common.py:235/237` clear-text-storage；
  #4 `memory/ledger.py:324` clear-text-storage；
  #5 `cli/runtime.py:30` clear-text-logging。
- HTTP SSRF/TLS 两条告警已不在当前 open 列表；完整 R07 仍未完成。
- 已推送的新增提交：`6736d6e` Darwin 双基线，`d9d6193` HTTP 固定连接/TLS 信任。
  最新本地 HTTP 专项 21 项、全量 760 项通过，Ruff/mypy 通过。
  predicate 测试补 cleanup 后，最新全量不再出现此前 socket ResourceWarning。

### 精确执行断点：R05 调查中，尚未开始补丁

已检查的路径：

1. `tools/build.py` Java/Shell payload 包含 raw stdout/stderr/cmd，编译失败包含
   compile_error；两处 `_write_cells` 原始写入 cells.json，无统一安全序列化。
2. `memory/state.py` CheckpointStore 直接写 JSON/text；chmod 失败被忽略，
   临时名仅 pid，有同进程并发冲突风险。
3. `autonomous/common.py` write_artifact 直接写 text/json；ledger 的 Markdown/JSON
   和 cli/runtime._out 也需共享边界。redaction.py 只有正则，不是完整边界。
4. `parse_poc_claims` 复制任意 marker；`_cell_poc_claims` 还读取旧 observations。
   这些是非可信 claim，删 stdout 不足以消除敏感副本。

下一步：先完成边界和兼容性调查，再实现统一安全投影/私有原子写入及回归。
普通证据不能依赖正则识别任意隐私；raw vault 默认关闭，权限、期限、可选加密
不能是假实现或缺依赖时回退明文。compile_error/harness_error 不能脱敏成空值
而丢失失败语义；不要改坏 trusted observation/effect digest 或 provenance，
需要持久化前后 replay 一致性测试及显式版本/迁移。不可信输出的 cmd、claims、
异常文本副本也应纳入。使用唯一临时文件、0600/0700，权限失败不得静默忽略。

本轮已完整阅读 `codex-security:fix-finding` 及 artifact-storage.md。已发现
`multi_agent_v1__spawn_agent` 可用，但尚未启动：下一步按技能先派 fresh read-only
boundary/compatibility investigator（fork_turns=none）；补丁后做一次独立
bypass/regression review。源码/回归测试正常编辑，额外安全扫描证据走 managed
artifact 工具；未创建新扫描或安全 artifact。

恢复时先执行以下只读核对，再沿 R05 断点推进，不重做 Darwin/HTTP 修复：

```sh
git status --short --branch
git log -10 --oneline
gh pr view 8 --json headRefOid,isDraft,state,statusCheckRollup
gh api 'repos/Zer0Gate/vulngate/code-scanning/alerts?state=open&ref=refs/pull/8/merge&per_page=100' --jq '.[]|{number,rule:.rule.id,path:.most_recent_instance.location.path,line:.most_recent_instance.location.start_line}'
```

完整 R01–R12 范围及验证命令见下文与 REMEDIATION-PLAN.md；不得以四条告警消失
代替全目标完成。账本中待提交/旧 run 等描述需在下一次源码提交时按证据更新。
本次只更新交接文档，未修改账本或源码。

## 历史快照（以下旧 HEAD、旧失败与“下一步”不再代表当前状态）

本文件用于额度恢复或会话切换后继续工作，不是整改完成声明。
先核对实际 Git/CI 状态，再沿本文断点推进；不要从头重做已有提交。

## 续接增量：Darwin 基线修复

基于下文 CI 数据，runner 已改为测量 controller 和 native launcher 两个
VM 基线，取较大者加原有 4 GiB 余量；任一测量失败仍拒绝执行。
资源 policy 升为 v7，避免旧基线产物被当作新契约复用。46 项专项和 755 项全量
通过，已推送 `6736d6e`。远端 run `36542833962` 已全部 workflow jobs 成功，
包括 macOS 3.10/3.13、Linux 3.10/3.13 和 test 汇总，原兼容性失败已关闭。
继续时先查询最新 HEAD/CI，不再重复下文已实施的“下一步”修补。

HTTP observer 已继续实现固定连接目标、显式 TLS 1.2 下限、fixture 信任校验
和 CONNECT 异常清理，专项及 760 项本地全量已通过；边界及剩余 R07 工作见
HTTP-OBSERVER.md。
最终 CI/CodeQL 是否接受需读取最新提交结果，不能仅按源码判断已清零。

## 目标与授权

完成 [REMEDIATION-PLAN.md](REMEDIATION-PLAN.md) 的全部 R01–R12；
本地与 GitHub 同步，按门禁提交、推送、审核合并、发布和刷新实际插件。
用户已经授权这些实施操作和 GitHub branch protection/ruleset 配置。
不能绕过保护规则、把失败改为跳过，或把局部测试通过称作整体完成。

## 当前代码与交付

- 工作分支：`Zer0Gate/vulngate-release-1.3.0`。
- 记录时功能 HEAD：`1e40efdc9db458733074f6725b76a8116c316f97`，已推送。
- main 审阅基准：`ea23f63c96b11cfc52754185418c4e67f2829eb5`。
- PR：<https://github.com/Zer0Gate/vulngate/pull/8>，草稿，尚未合并。
- 尚未为这批整改发布新版本，也未刷新实际插件缓存。
- 本文件创建前工作区干净；`.hypothesis/` 为忽略的测试缓存。

已提交的工作（不应重复实现）：

| 提交 | 内容 |
| --- | --- |
| `cb86800` | 初始整改实现；标题声称 complete 过宽，不能据此认定全部完成 |
| `1d5f4fa` | 固定名 test 汇总，矩阵失败/取消/跳过时 fail closed |
| `e7ac233` | 每次实际 LLM 请求 reserve/settle；并发预算、未知计费保守处理 |
| `aba047a` | 修复安装 rollback trap；删除旧缓存版本指向新代码的 alias |
| `a8f39d4` | 内容摘要版本目录、安装事务日志、并发锁、恢复和激活后完整性校验 |
| `1e40efd` | 恢复 F821 门禁、修复残留未定义变量/类型导入、增加 Darwin 诊断 |

两项真实使用问题已有代码和回归：audit-exec/host guard 参数契约、
coverage rebuild 的 excluded；测试位于 `tests/test_hardening_controls.py`。
仍需从最终安装包直接执行验证，不能用临时 wrapper 代替验收。

## 测试与环境

- 安装专项 18 项通过；覆盖崩溃边界、并发、legacy 迁移、版本冲突、
  篡改、磁盘写入失败、错误缓存身份。详见 [INSTALLATION.md](INSTALLATION.md)。
- `1e40efd` 代码的全量本地测试 750 项通过（Python 3.13，63.3s）。
- Ruff（包含 F821）、关键模块 mypy、安装脚本 ShellCheck、插件 validator 通过。
- 既存 socket ResourceWarning 尚未修复，不隐去此事实。
- 本地 `/opt/local/bin/python3` 为带 dev 依赖的 Python 3.13。
  默认 `python3` 为 3.14，缺 Hypothesis；不要把缺包当作测试通过或代码失败。
- 插件 validator 还需要 PyYAML，ShellCheck 需要对应可执行程序；此前在临时
  venv 中安装 PyYAML 6.0.2 / shellcheck-py 0.10.0.1 验证，未更改全局 Python。

## 最新 CI 与精确断点

Run `36507376708`（HEAD `1e40efd`）已结束失败：Linux 3.10/3.13、
macOS 3.13 及 security job 成功；macOS 3.10 和 test 汇总失败。
security job 成功不等于独立 CodeQL check 没有告警，需单独读取 PR checks。

macOS 3.10 job `109211741791` 的新增诊断给出：

- Python 3.10.11 / arm64，controller VSZ = `420832804864` bytes；
- native bash / arm64，VSZ = `435299488` KiB；
- RLIMIT_AS soft/hard 均无限，拟设上限 = `425127772160` bytes；
- bash 本身 VM 映射大于拟设上限，随后 `ulimit -v` 报 Invalid argument。

因此不能再简单归因于“Python 版本不支持”或假设 x86/arm 架构不同。
下一步：在 `scripts/agent/sandbox/runner.py` 中同时测量 controller 与
实际 launcher 的 VM 基线，并验证上限设置仍 fail closed；保持资源契约诚实，
不把 RLIMIT_AS 的虚拟地址空间上限当作整棵进程树的内存硬配额。
补 launcher 基线较大/测量失败/异常输出的回归，真实远端矩阵确认修复。

恢复时可执行：

```sh
git status --short
git log -8 --oneline
gh pr view 8 --json headRefOid,isDraft,state,statusCheckRollup
gh run view 36507376708 --job 109211741791 --log
PYTHONPATH=scripts /opt/local/bin/python3 -m unittest discover -s tests -t tests -q
/opt/local/bin/python3 -m ruff check scripts hooks tests
/opt/local/bin/python3 -m mypy
```

CI job 正在运行时日志可能暂不可取；继续查询同一 run/job，不因此重启任务。
修改后正常 commit/push，核对远端 HEAD，按新 HEAD 查 CI。

## 后续未完成范围

- R01：CI 全绿、CodeQL 结果真正纳入门禁、审核合并。main 已配置需要 review，
  不要尝试作者自审或削弱规则；真正到审核阻塞时再请求合资格 reviewer。
- R02：服务与 PoC 统一 backend；目标执行前进入 cgroup；完整隔离单元清理；
  真实 Linux/container 压力与失败验收。现有 service_lifecycle 仍先 Popen 后 attach，
  cgroup.close 只尝试 rmdir。不能按类存在就说隔离完成。
- R03：已有本地预算修复，继续实际发送计数和交付验收。
- R04：源码安装器已验证；正式包真实激活、旧线程行为仍待最终验收。
- R05：统一安全持久化，raw vault 默认关、权限/期限/错误路径。
  已定位 common.write_artifact、CheckpointStore、ledger.write_round_artifacts、
  cli/runtime._out、build cells.json；当前通用写入仍可能含原始输出。
- R06：完整 Run Manifest 绑定及 resume 漂移拒绝；tool_identity 只是底层组件。
- R07：observer 来源、scope、predicate、controls 与效果归因验收。
- R08：F821 已闭环；真实 backend CI、逐风险模块分支覆盖及依赖审查仍待完成。
- R09：审核提交、不可变 tag、正式 Release assets/SBOM/checksum/provenance、干净安装。
- R10–R12：共享应用核心、真实能力矩阵、正/负/未知/恶意自报端到端与性能基准。

CodeQL 既有六条告警：common.py 两处输出、runtime.py 日志、ledger.py 写入、
http_observer.py TLS 最低版本及请求转发地址。逐条核验实际来源与边界后修复，
不直接抑制。HTTP observer 仍按请求解析 host/port 转发；应评估固定已验证
数字 loopback 目标、TLS 最低版本，以及 CONNECT 清理与截断 predicate 完整性。

## 最终安装注意事项

实际刷新遵循 plugin-creator 的 cachebuster + CLI reinstall 流程。不要在当前
审计线程依赖旧插件路径时随意激活新缓存；Codex 可能移除旧缓存。
安装器不重建旧路径指向新代码的别名。最终启用验证后新建线程使用。
源码提交、PR 合并、Release 发布、实际插件安装是四个不同状态，逐项提供证据。
