# VulnGate 整改续接记录（2026-09-29）

## 2026-10-01 增量：嵌套硬资源限制单调收紧修复（源码远端已验收）

### 最新验收状态（优先于下方提交前候选记录）

源码 `aa235d7e9e6cc6cf763d8e656ffb1dd0638e433b` 已推送，run `36787893737`
COMPLETED/SUCCESS：macOS 3.10 `110133585114`、macOS 3.13 `110133585083`、
Ubuntu 3.10 `110133585154`、Ubuntu 3.13 `110133585192`、security `110133585163`、
test 聚合 `110134482087` 全部成功。真实隔离 `110133584964` 运行 4 项（8.022s），
OK、无 skip；独立 CodeQL SUCCESS，PR merge ref open alerts=0。关键 gated-service
三项本地连续重复 8 轮、24 项（28.174s）成功；这些重复测试不是硬件压力测量。
所有源码 jobs 与本地测试/watch 进程已终态。随后仅提交状态文档，文档新 HEAD CI
需单独查询，不能复用源码 HEAD 全绿结论。工作树干净、本地/远端/PR 源码一致。
main 保护读回 test/security required、1 approval、enforce_admins=true；PR 草稿/审核待办，
没有合并、发布或重装。此结果关闭本批嵌套限额兼容性验收，不关闭整个 R02/R06。

### 提交前候选记录（历史）

延续 bc7c077，不重做 R06 checkpoint。bc7c077 的 run `36787181163` 已终态 FAILURE：
macOS 3.10 job `110131281707` 在 `test_cgroup_gate_holds_target_until_attach_and_releases_it`
再次 health returncode=1/timed_out=false，test 聚合 `110132313125` 失败；real-isolation
`110131281526`、其余三矩阵和 security 通过。与前两次健康检查失败属于同一嵌套限额路径。

本地确定性反例：健康目标先测 NPROC，host launcher 随后基线下降，外层先设较小硬上限，
内层再请求较大上限，`ulimit: max user processes: cannot modify limit: Invalid argument`，
健康返回 1。新增嵌套 readback 与实际托管健康回归在旧源码上均失败；前者也复现 NOFILE
同类错误。CI 原日志未保留健康 stderr，不能声称已直接读到该 CI 进程的具体 ulimit 错误。

修复共享资源启动器：CPU/FSIZE/NOFILE/NPROC/AS 在设置 soft/hard 前取请求值与继承 hard
中的较小值，core 仍为 0；不提高原有上限，不忽略 setter 错误，无效读回仍 fail closed。
资源 policy 升为 `...core0-monotone-v8`，供 Run Manifest/policy 身份拒绝旧轮次复用。
回归读回六种 soft/hard，覆盖内层更宽/更严/相同及 NPROC、AS 两种动态基线下降；
仍断言使用 managed-service-backend、实际 cgroup attachment 和 complete 清理。

完整本地 854 项（75.100s）通过，4 项真实隔离在 macOS 明确 skip；49 项聚焦、
Ruff/mypy（7 模块）/compileall/diff-check/内嵌 bash 语法通过。源码候选尚待提交后
精确 HEAD 的四矩阵、real-isolation 和 CodeQL 验收，不使用旧 HEAD 成功替代。
之前 Docker 身份发现/未决创建清理失败的根因仍未知，此补丁不宣称关闭该缺口或整个 R02。

下一步先核对状态文档 HEAD CI，然后继续 R06 的 matrix/cells/receipt/report 身份链，
范围和兼容性要求见下方交接。没有合并、发布或插件刷新，完整整改目标 active。

## 2026-10-01 03:12 北京时间：额度交接与最新 CI 失败（当前断点）

本窗口已交接：`resetsAt=1790804622`。应用实时读数五小时已用 99%、
剩余 1%；北京时间重置时间 **2026-10-01 05:43:42**（UTC 09-30 21:43:42）。
读取的是 `rateLimitsByLimitId.codex.primary` 的 300 分钟窗口，不使用重置券。
同窗口后续 heartbeat 不重复提交交接；目标保持 active，不标 complete/blocked/paused。

交接后补记（北京时间 06:43）：交接提交 `86fbf237b63ff67ac4e58ec98af1e371cbcecf51`
已推送，run `36764354582` 全部终态、FAILURE。real-isolation `110054634023` SUCCESS；
macOS 3.10 `110054634276` 同一健康检查再次失败（852 项，112.440s，1 failure/4 skip），
test 聚合 `110055850433` FAILURE；其余三组回归/security SUCCESS。Docker 这次通过不
代表之前未决清理失败根因已解决。没有重跑操作或源码变更。下一步先读该 macOS job。
额度曾达到 100%，当时 resetsAt 临时读回为 `1790804623`（同一到期边界漂移一秒，
也视为已交接，不能据此重复交接）；随后自然重置，新窗口 `resetsAt=1790826136`
已用 1%、剩余 99%，该新窗口未达到交接阈值。未用重置券。本补记仍只修改交接文件，
其后产生的新文档 CI 按精确 HEAD 重新查询，不循环补记每次文档触发的 run。

### Git、提交与运行状态

交接前本地 HEAD、origin branch 与 PR #8 HEAD 一致：
`f27e0b9f487aff3ddda8a404a161c5409db8d622`（仅交接/计划文档）。
最近源码提交 `fc429d5f9663ab2837b68b848a38e66f12520898` 完成 R06 第一批
create-once Run Manifest/checkpoint 身份门禁；之前 R02 源码 `f9836f0`。
交接前工作树和 index 干净，本轮没有新增 cells/receipt 源码改动，没有未提交源码。
只提交推送本文件；交接提交 SHA 用 `git log -1 --format=%H -- docs/CONTINUATION-HANDOFF.md`
读取。PR #8 OPEN/DRAFT/REVIEW_REQUIRED，未合并、发布或刷新插件。

源码 fc429d5 的历史 run `36760403261` SUCCESS 及 852 项本地成功仍是有效历史
证据，但不能证明最新 HEAD 全绿。最新 f27e0b9 的 run **36761121625 FAILURE**：

| job | ID | 终态 |
| --- | --- | --- |
| security | 110043621892 | SUCCESS |
| Ubuntu Python 3.10 | 110043622336 | SUCCESS |
| Ubuntu Python 3.13 | 110043622236 | SUCCESS |
| macOS Python 3.13 | 110043622186 | SUCCESS |
| macOS Python 3.10 | 110043622374 | FAILURE |
| real-isolation | 110043622425 | FAILURE |
| test 聚合 | 110044856469 | FAILURE（正确阻断） |

独立 CodeQL SUCCESS。上述 run 所有 jobs 已终态，没有仍运行的旧验收 job。
只读调查 agent Bohr 已完成并关闭，没有运行中 agent 或本地长测。
交接文档推送会触发新的 CI，**其 run/job ID 尚未产生于本节写入时**；恢复时按
交接提交的精确 HEAD 查询新 run，不将旧 run 成功冒充新文档 HEAD 的验收。

### 未解决失败：先诊断，不能用重跑成功或放宽门禁替代根因

1. macOS 3.10：`test_service_lifecycle.ServiceLifecycleTests.test_managed_healthcheck_joins_service_cgroup_without_host_runner`
   在 `tests/test_service_lifecycle.py:256` ready=false。假 backend 已标 isolated/enforced，
   health returncode=1、timed_out=false、managed-service-backend，最终 healthcheck timeout，
   startup_timeout=3；backend/cgroup 清理 complete。852 项（95.655s），1 failure、4 live skip。
   需核对 fixture 就绪事件、健康子进程 stderr、资源限制和 deadline，尚未证明是超时预算问题。
2. real Docker：`tests.test_isolation_backend_live.LiveIsolationBackendTests.test_container_health_identity_and_detached_cleanup`
   在 :163/:126 ready=false；Docker 28.0.4，PermissionError，isolation-unverified/enforced=false，
   cleanup-incomplete、backend cleanup failed: OSError，并有 service.lock 未关闭警告。
   4 项（10.777s），1 failure、无 skip；bubblewrap 与另外两项通过。日志尚不足以区分
   创建失败、身份发现/ownership 错误或迟到创建。`ContainerBackend.after_start` 有 5 秒身份
   发现期限；异常类不能证明具体分支。先补有界合成 fixture/引擎诊断并保留未决清理语义。

本次仅核对失败日志，没有重跑 CI、没有修补或关闭 R02 稳定性缺口。

### R06 下一层调查已完成，但未实现

基线聚焦回归 `test_regressions.S4RegressionTests.test_persisted_cells_override_proxy_timeout_and_fallback_is_merged`
1 项通过（0.006s）：未绑定 persisted/fallback cells 仍会被汇合；这是缺口证据，不是修复证明。
checkpoint 校验不保护 `memory/state.py` 普通 artifact 读取；`tools/build.py` 两种 runner
发布 cells 与 `converge_s4_cells`、pipeline 的原始 cells/S6–S8 fallback、S7/S8 读取、
autonomous 新建未绑定 store、报告和 ledger 跳过发布均需统一验收边界。
receipt 当前仅检查 schema/candidate/nonce/path/size/hash/completed，不绑定父执行身份；
独立 CLI 的 PoC `--manifest` 没有完整目标配置，不能伪造身份或改变既有参数含义。

建议共享显式接受已验证父摘要的产物发布/读取接口；保留历史 inspect，与执行验收分离。
不能给 list/candidate-keyed map 硬加字段或自动升级旧产物；同轮多 PoC、Java/shell 修复、
partial cells、空矩阵要保留，稳定父身份之外仍需尝试/内容身份。receipt schema 若升级，
同步 `memory/evidence_store.py` nonce 白名单。pipeline/autonomous 身份拒绝 exit=2，
matrix deadline 到期 exit=3，receipt verify 契约失败 exit=2；inspect/missing exit=0。
当前 inspect 构造 store 会创建/chmod，不能声称已经严格零副作用。
autonomous S5/S6 resume 未回填 novelty/CVSS、后续 `r["cvss"]` 的 KeyError 风险和
S7 报告/S8 ledger 门禁差异只是静态推论，尚未动态验证，不是上述 CI 根因结论。

### 剩余 R01–R12（详细验收项仍见 REMEDIATION-PLAN.md）

- R01 partial：最新 test 不通过，PR 草稿/审核待办；禁止合并。
- R02 partial：本批历史健康隔离通过，但最新两项失败重新打开稳定性；统一 PoC/service
  backend、observer、rootless、压力/断连/迟到创建和崩溃恢复仍未完成。
- R03/R04 validated-source-tests：预算/安装事务源码回归已完成；真实 provider 计费、
  正式安装激活和旧线程验收未宣称完成。
- R05 partial：旧产物、剩余写入器/自由文本及正式包安全验收。
- R06 partial：第一批 checkpoint 门禁完成；上述 cells/receipt/report 全链、完整 runtime
  闭包/派生 lane、镜像实际 launch 固定、校验到使用冻结未完成。
- R07 partial：observer 来源/归因/截断/恶意自报控制未完成。
- R08 partial：真实失败确实阻断；分模块 branch coverage、依赖及 runner 迁移审查待办。
- R09 planned：审核/tag、Release 归档、SBOM/checksum/provenance、干净安装。
- R10 planned：三入口共享服务/build 按职责拆分。
- R11 planned：installed/probed/enforced 能力矩阵和文案。
- R12 planned：真实正负/未知/恶意自报 fixture 与误判、pending、资源指标。

### 恢复顺序与命令

先检查交接 HEAD 的 CI，定位上述两项失败并按风险验收；再继续 R06 第二层，不重做
已经提交验证的 checkpoint 第一批。不降低 fail-closed/cleanup 门槛，不把 macOS live skip
当作真实 backend 证明；补丁完成后完整测试、一次独立审阅和精确新 HEAD CI 才能验收。

```sh
git status --short --branch
git log -3 --oneline
gh pr view 8 --json headRefOid,isDraft,state,reviewDecision,statusCheckRollup
gh run list --branch Zer0Gate/vulngate-release-1.3.0 --limit 5 --json databaseId,headSha,status,conclusion
gh run view 36761121625 --json status,conclusion,jobs
gh api --allow-escape-sequences repos/Zer0Gate/vulngate/actions/jobs/110043622374/logs
gh api --allow-escape-sequences repos/Zer0Gate/vulngate/actions/jobs/110043622425/logs
PYTHONPATH=scripts:tests /tmp/vulngate-vault.bQGImB/bin/python -m unittest test_service_lifecycle.ServiceLifecycleTests.test_managed_healthcheck_joins_service_cgroup_without_host_runner -v
PYTHONPATH=scripts /tmp/vulngate-vault.bQGImB/bin/python -m unittest discover -s tests -t tests -q
```

真实测试须在配置好 backend 的 Linux CI 使用 `VULNGATE_LIVE_ISOLATION=1`，参考现有
workflow 的安装/运行步骤；本地虚拟环境若已失效，按开发依赖重建，不改源码绕过测试。

## 2026-10-01 增量：R06 Run Manifest/checkpoint 身份门禁

### 最新验收状态（优先于下方提交前候选记录）

源码 `fc429d5f9663ab2837b68b848a38e66f12520898` 已提交推送。
CI run `36760403261` 已完成 SUCCESS：四组 Linux/macOS Python 3.10/3.13、
security job `110041202971`、test 聚合 `110042461366` 全部成功；独立
CodeQL SUCCESS，PR merge ref open alerts=0。真实隔离 job `110041203169`
运行 4 项（6.4s）、OK、无 skip，确认共用引擎选择器未破坏现有真实 backend。
全部 jobs 均已终态，没有仍运行的源码验收 job 或审阅 agent。

本次随后只提交本交接和计划文档；文档 HEAD 的 CI 需另外查询，不能把源码
HEAD 的成功写成文档 HEAD 的已验证结果。源码/本地与远端 branch 保持一致；
PR 仍草稿/review required，没有合并、发布或插件刷新。
R06 整包仍 partial，以下列出的 cells/receipts/冻结边界是下一批真实工作，
不得把这个绿色候选扩大为完整整改目标完成。

CI 还报告固定 action 的 Node 20 被平台强制转 Node 24，以及 ubuntu-latest
将迁移的维护提示；纳入 R08 依赖/runner 兼容性后续验收，不抑制通知或改 tag
来冒充 SHA pin 更新。此次并无因此失败的 job。

### 提交前候选记录（历史）

延续已验证源码，不重做 R02。已确认上一文档 HEAD `f0b3819` 的 CI
`36753671936` 全部完成 SUCCESS；本批实现基线就是该提交。
本节与源码候选一同提交；精确候选 SHA 请以
`git log -1 --format=%H -- scripts/agent/orchestrator/run_identity.py` 读取。
本节记录时尚未提交/推送候选，因此没有本候选对应的运行中 CI/job，不用
上一 HEAD 的成功证明新增代码已远端验收。后续以精确新 HEAD 查询 run。

本批完成：create-once 私有 Manifest、操作员配置与实际源码根/字节身份、
显式 PoC/JAR/历史输入、部署 PAYLOAD 共用工具身份、已有安装树校验、
镜像 ID 与共用引擎选择、默认/已声明 Java/javac 字节身份、policy/版本。
pipeline 和 autonomous 在 checkpoint 读取前校验，并拒绝无身份旧轮次、
父摘要不匹配或身份漂移；`--force` 不能覆盖身份。保留旧 checkpoint
反序列化检查；缺失 artifact 的读取不创建 stage 目录。两个 CLI 身份拒绝
返回非零码。调度候选和学习 api_hint 保留为派生状态，不回写操作员配置。
文件/接口与限制见 `docs/RUN-IDENTITY.md` 和 `schemas/run-manifest.json`。

修复前真实控制器反例：改变 scope_constraints 后 `--stage S8` 仍执行；
修复后 source/config/tool/image/PoC/Java 漂移与 force、无绑定/错误绑定等
反例被拒绝，同一身份 stage-only/full resume、候选恢复和下游失效仍通过。
一轮独立候选审阅确认源码根猜测、PoC 漏绑定、引擎选择差异、Java 字节缺口、
学习配置漂移和 CLI 错误成功码；已逐项核对并新增回归。未重新派审。

最终本地：852 项（82.1s）成功，4 项 live 在 macOS 明确 skip；
Ruff、mypy（7 个安全模块）、compileall、diff-check 成功。
当前 main 保护读回仍为 test/security required、1 approval、enforce_admins=true；
PR #8 OPEN/DRAFT/REVIEW_REQUIRED。未合并、发布或重装插件。

R06 仍 **partial**：直接 stage/helper/matrix 入口、cells 与 fallback/sequential
convergence、parallel challenge/receipt、S4/S8/report/ledger 全链父身份未接入；
镜像 ID 到 launch 固定、实际 backend/完整 runtime 依赖闭包与派生 lane、
源码/工具校验到使用冻结也未完成。摘要不是 observer 来源证明，R07 不能省略。
R01–R12 其余状态仍见 REMEDIATION-PLAN；完整目标 active。

下一步先验收本候选新 HEAD 的 CI/CodeQL；再沿同一身份门禁接入 matrix
与 build.py 的两个 runner 发布/恢复路径及 `converge_s4_cells`，同时设计
独立可验证的产物父摘要，不直接改写候选 keyed dict/list 的 public 形状。
`cli/runtime.py` 的 PoC `--manifest` 含义必须保留；其单独执行还缺目标配置，
应显式提供/读取已绑定完整 round，不伪造完整身份。
回执入口在 `cli/evidence.py`，只读 inspect 与执行证据接受必须分离。

```sh
git status --short --branch
git log -1 --format=%H -- scripts/agent/orchestrator/run_identity.py
gh pr view 8 --json headRefOid,isDraft,state,reviewDecision,statusCheckRollup
gh run list --branch Zer0Gate/vulngate-release-1.3.0 --limit 5
PYTHONPATH=scripts /tmp/vulngate-vault.bQGImB/bin/python -m unittest discover -s tests -t tests -q
PYTHONPATH=scripts:tests /tmp/vulngate-vault.bQGImB/bin/python -m unittest test_run_identity -q
```

## 2026-10-01 增量：R02 健康检查与 backend 清理候选（最新断点）

### 最新已验证状态（优先于本节下方失败/候选历史）

源码 HEAD `f9836f0a8a29ad2efae8580eac2606e96d66ceb3` 已提交推送。
最终完整本地 817 项（69.7s）成功，4 项 live 在 macOS 本地明确 skip；
Ruff/mypy/compileall/diff-check 成功。远端 run `36752932751` 已完成 SUCCESS：
四组 Linux/macOS Python 3.10/3.13 回归、security、固定 test 均通过；独立
CodeQL SUCCESS，PR merge ref open alerts=0。真实隔离 job `110015860233`
实际运行 4 项、8.0s、OK，无 skip，涵盖两个 backend 的真实就绪、隔离健康命令、
脱离 session 后代停止、健康超时和清理失败重试；验证了 capabilities=0、
NoNewPrivs=1、64 MiB 文件限额、真实 cgroup 成员/限额读回及容器消失。

上述证据关闭本批**托管健康检查宿主执行路径与正常停止边界**的验证缺口，
但 R02 仍 partial：PoC 与服务统一 backend、namespace 内 observer 对接、
非 root/rootless 变体、真实 memory/pids/cpu 压力、engine 断连/迟到创建、
崩溃后恢复/未决创建的持久化清理未全部验收。不得用 4 项 live 测试代替这些要求。
镜像引用已绑定授权，但可移动标签的实际镜像 digest 固定仍须 R06。
PR #8 仍 draft/review required，未合并、发布或安装；源码工作树干净。
本次后续仅提交状态文档；文档 HEAD 的新 CI 需再次检查。

下一步先核对文档 HEAD 的 CI，再按依赖推进 R06 Run Manifest
（源码/配置/目标/镜像/policy 身份及续跑漂移拒绝），同时继续 R02 统一后端。
具体入口：`sandbox/network_sandbox.py` 当前 PoC 仍是 macOS Seatbelt；
`tools/build.py` 的 Java/Shell runner 在 verify/wrap_network_sandbox 及 observation
provenance 边界使用它；`sandbox/runner.py` 负责资源和结果。不能直接让 Linux PoC
脱离现有 observer/cell 绑定而“运行成功”，也不能绕过缺后端时的 fail closed。

### 本节早期候选与失败记录（不是当前终态）

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

`c638997` 继续 `5045c8c`，不重做已有整改。其候选内容：托管命令式健康检查不再
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

当时独立只读候选审查 `01a0f342-4284-7140-a466-5166c69c182f` 尚在运行，
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

## 2026-09-30 增量：R02 cgroup 启动/清理止损（历史断点）

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
