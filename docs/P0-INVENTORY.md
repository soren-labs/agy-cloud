# P0 成果清单与证据分级

日期：2026-09-09。

## 1. 找到的原始位置

主目录：`/home/zheng/tmp/agy-worker/`。

历史会话：`/home/zheng/.claude-sand/projects/-home-zheng-tmp/9e3bfb4e-b842-473f-858f-b0e7bce9a21a.jsonl`。

本地旧测试仓库：`/home/zheng/ai-work/tmp/cursor-cloud-smoke-20260907/`。其 main 检出只有启动骨架，不能把它误认为已经包含所有测试模块；生成的代码主要在原型 state 内的各个分支检出中。

| 成果 | 原始位置（相对于主目录） | 本交接包 | 判定 |
|---|---|---|---|
| run / followup / review 原型 | `worker.sh` | `p0/worker.sh` | 原文件已找到并校验复制一致 |
| 原始运行设计 | `PLAN.md`、`DESIGN.md` | `docs/originals/` | 保留历史参考，PLAN 已被新设计替代 |
| 首次开发及续聊 | `state/smoke-01/` | evidence、fixtures 的同名目录 | 两次 SUCCESS，同 conversation，两个 commit |
| Flash 开发与测试 | `state/smoke-02/` | 同上 | SUCCESS，历史 tests=passed |
| Sonnet 开发与测试 | `state/smoke-03/` | 同上 | SUCCESS，历史 tests=passed |
| Opus 审查 | `state/review-04/` | evidence/review-04 | SUCCESS，提交的是 COMMENT review |
| 不带快照的对照实验 | `state/_restoreA/` | evidence/_restoreA | 警告旧 conversation 不存在，产生新 conversation |
| 带快照恢复实验 | `state/_restoreB/` | evidence/_restoreB | 恢复原 conversation，num_turns=3，回答出此前模块与函数 |
| agy 配置 | 各 state 的 `.gemini/antigravity-cli/settings.json` | settings.reference.json | 派生参考，保留 P0 宽泛授权并替换本机可信路径 |
| agy 二进制 | `/home/zheng/.local/bin/agy` | 只保存路径、大小、哈希、版本 | 当前 --version 为 1.1.27；未分发二进制 |
| Docker 基础镜像 | 本机 `agy-worker:base` | worker.sh 中保留构建定义 | 镜像当前存在；不是设计要求的完整 GCE 镜像 |
| 流式、schema 实验 | 历史会话第 346、351 行 | evidence/history/stream-schema-* | 找到命令和捕获输出；临时完整输出已被原实验清理 |
| 恢复实验命令 | 历史会话第 337、342 行 | evidence/history/restore-* | 原命令和原结果已提取，没有重新执行 |

## 2. 真实 GitHub 交付

- [PR #4：string_utils](https://github.com/soren-labs/cursor-cloud-smoke-20260907/pull/4)：初轮 `c4a2b03`，follow-up `f01f832`；本次远端 head 为 `f01f83272d8a863339c3f3da4fd0ee0ab571c542`。
- [PR #5：text_stats](https://github.com/soren-labs/cursor-cloud-smoke-20260907/pull/5)：对应 smoke-03。
- [PR #6：math_utils](https://github.com/soren-labs/cursor-cloud-smoke-20260907/pull/6)：对应 smoke-02。
- [PR #4 review](https://github.com/soren-labs/cursor-cloud-smoke-20260907/pull/4#pullrequestreview-5141002286)：远端状态 `COMMENTED`，不是 `APPROVED`，不能算作分支保护要求的 approval。

这三份 PR 在本次只读查询时仍为 open。结果保存在 verification JSON。原型用现有 GitHub 身份操作，尚未证明产品 GitHub App 身份、token 铸造、Checks 权限已经工作。

## 3. 快照成果确实存在，但要精确描述

`smoke-01` 和 `_restoreB` 中都找到以下文件集：

1. `brain/ac4682f7-fb06-497e-aae9-c44b6c733829/`
2. `conversations/ac4682f7-fb06-497e-aae9-c44b6c733829.db`
3. `annotations/ac4682f7-fb06-497e-aae9-c44b6c733829.pbtxt`
4. `conversation_summaries.db`

位置、每个文件大小和 SHA-256 见 snapshot-inventory.json。原件原地保留，不包含在可交接代码副本中。

历史命令通过复制这些状态完成恢复，并将排除部分目录后的 tar 输出管道交给 `wc -c`，得到 **92,286 字节**。这不是一个留存下来的 snapshot.tar.gz；打包对象也不严格等于只有四件套（还包括设置等未被排除文件）。不能声称“历史压缩包已找到”或把 90 KB 当成固定大小。

本次检查没有重新进行在线恢复。`_restoreB/out.json` 的 duration_seconds 约 2849，不能据此承诺恢复耗时；应以新测试的外部计时为准。已找到的是同账号恢复证据，没有找到跨账号恢复成功证据。

## 4. 对原设计中“已完成”的修正

| 原文印象 | 原始代码 / 证据实际情况 | 第一版处理 |
|---|---|---|
| GitHub token 已在宿主隔离 | docker_run 使用 `-e GH_TOKEN`，git 与 gh_api 也在同一容器内 | 宿主 runner 与 agy 容器必须拆开；测试容器也不能获得 token |
| 测试容器与 agy 分离 | inner_code 在 agy 所在容器运行 bash 测试 | 增加无凭证测试容器与可写临时空间 |
| 容器加固已齐全 | 原型只有非 root 基础镜像，未实现所设计的 metadata 阻断、cap-drop 等 | T3 补齐并做负向测试 |
| stream-json 与 json 是同一顶层结构 | 捕获结果为 `{"event":"result","result":{...}}`；init/step_update 也有嵌套 | 用真实事件样例定契约，不能只解析顶层 status |
| schema 输出一定叫 structured | 历史命令投影 `.structured_output // .result` 为 structured；原始 JSON 没保留 | 能确认结构化结果可用，不能确认原始字段名；P2 review 前重新验证 |
| review 可直接算 approval | 当前原型固定发送 `event: COMMENT` | 开发审查流程必须区分 COMMENT 与正式 approval |
| 测试失败会阻止成功收尾 | 原型最后只按 agy rc 退出；测试失败仍可能 push / PR | V1 分离模型结果、测试结果和 run 状态，测试失败不得标成功 |
| prompt.txt 已解决 argv 限制 | 最终仍是 `--print="$prompt"` / shell 展开 | V1 大正文保存在任务文件，命令行仅传短指令与路径 |
| 无需照看、并发、VM 回收已完成 | 原型为本地 Docker + state，未找到控制面、Firestore 调度或 VM 回收实现 | 均属于 P1 新开发 |

历史三并行记录包括两个开发容器和一个 review，时间相近且有输出；不能把它当成“五个 GCP Worker 并行”或多账号并发上限证明。

## 5. 本次验证

- smoke-01 的已有测试：11/11。
- smoke-02 的已有测试：8/8。
- smoke-03 的已有测试：11/11。
- `bash -n p0/worker.sh`：通过，仅证明语法成立。
- 原文件复制件 SHA-256：逐项记录并检查。
- GitHub 三个 PR 和一个 review：只读核验。
- agy：只运行 `--version`，未运行带订阅的任务。

没有把“找到旧结果”描述为“重新跑过真实端到端”。

## 6. 尚未存在的交付物

此次没有在 P0 主目录找到独立 Dockerfile、生产 runner.py、GCE 镜像构建脚本、API、fake_agy、可重复执行的独立快照测试、GitHub App 集成、CI 或 `.agy/environment.json`。这些是待实现项，不是需要继续寻找的“已完成产品”。

已找齐两份文档可定位的核心 P0 原型与证据。历史 stream-json 全量临时文件和历史 tar 压缩包没有留存；前者可从历史输出取得完整 init / step_update / result 各一条，后者需基于原件重新生成。交接包中的三条流式记录标明为节选，不能冒充完整事件流。
