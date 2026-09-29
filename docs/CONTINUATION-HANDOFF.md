# VulnGate 整改续接记录（2026-09-29）

本文件用于额度恢复或会话切换后继续工作，不是整改完成声明。
先核对实际 Git/CI 状态，再沿本文断点推进；不要从头重做已有提交。

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
