# DESIGN (1)

# agy-cloud — 基于「临时 GCP VM + Antigravity Pro 订阅」的云端编程 Agent 平台

> 目标：用你自己的 GCP 项目和多个 Antigravity Pro 订阅，复刻 Cursor Cloud Agents / Hoplite 的核心体验——
**调用一次 API 就不用再管机器；每个 thread 随时可恢复；任意并发；每次执行都在干净 VM 上；VM 用完立刻回收，不常驻。**
> 
> 
> 文档状态：设计定稿（2026-09-08）。标注说明：✅ 已在本机实测 · 🔧 设计决策 · ❓ 首次部署时需验证。
> 上一版 `PLAN.md`（常驻单机 + worker.sh）被本文档取代；`worker.sh` 的容器内流程被原样保留为本方案的 agent 容器层。
> 

---

## 0. 一页总览

| 维度 | Cursor Cloud Agents | Hoplite | **agy-cloud（本方案）** |
| --- | --- | --- | --- |
| 大脑（模型账单） | Cursor 额度 / on-demand | Hoplite 额度 | **Antigravity Pro 订阅池**（Gemini 3.1 Pro / 3.8 Flash、Claude Sonnet 4.6 / Opus 4.6、GPT-OSS） |
| 执行沙箱 | Cursor 托管 VM（可 self-hosted worker） | 托管沙箱（可 `LocalAgentExecutionTarget`） | **每个 run 一台全新 GCE VM，跑完即删**（支持 Spot） |
| 控制面 | Cursor 服务端 | Hoplite 服务端 | **FastAPI on Cloud Run（缩容到 0，空闲 ≈ $0）**，Plan B：always-free e2-micro |
| 状态存储 | 服务端 | 服务端 | **Firestore（元数据）+ GCS（会话快照 / 事件 / 日志）** |
| 调用后是否需要照看 | 否 | 否 | **否**：VM 自治完成 → 推 PR → 上传快照 → 自我回收 |
| thread 恢复 | 服务端快照 | 服务端 | **90 KB 会话快照 + GitHub 分支**，任意时刻在新 VM 上续聊 ✅ |
| 运行中追加 follow-up | 是（排队） | 公开 API 无 | **是**（同 VM 排队），结束后 follow-up → 新 VM 恢复 |
| GitHub | GitHub App，`cursor/*` 分支，自动 PR，`@cursor` 评论触发，webhook | GitHub App，`hoplite/*` 分支，PR review loop | **GitHub App，`agy/*` 分支，确定性开 PR，`@agy` 评论/Issue 触发，Check Run，可选 CI 自动修复循环** |
| API | `/v1/agents` durable agents + runs，webhook | project / thread | **与 Cursor v1 形状对齐**：agents ↔︎ runs ↔︎ sessions，webhook，SSE 事件 |
| 并发 | Pro 约 8（论坛口径） | 受套餐限制 | **= min(GCP vCPU 配额 / 每 VM vCPU, Σ账号并发上限)**，可申请提额 |
| 成本（300 run/月 × 10 min） | 订阅 + 用量 | 订阅 | **≈ $3–6/月 计算 + 免费层存储**（对比常驻 e2-standard-4 ≈ $98/月） |

**非目标（MVP 明确不做）**：Web UI（用 API/CLI/MCP 代替）；多租户计费；秒级冷启动（目标 ≤ 60 s）；替代 CAO 的多 agent 协作编排（CAO 可作为本 API 的客户端接入）。

---

## 1. 已验证的事实（设计依据）

### 1.1 Antigravity CLI（`agy` 1.1.27）✅

| 事实 | 观察 |
| --- | --- |
| 可移植 | 原生 x86-64 ELF（210 MB）。只拷贝二进制 + `antigravity-oauth-token` 进干净 `ubuntu:24.04`（仅 ca-certificates）即可运行、列模型、调工具改文件 |
| 无头模式 | `agy --print="<prompt>" --output-format json\|stream-json --dangerously-skip-permissions --model <m> --print-timeout 30m --add-dir <ws> --conversation <id>`。注意 **`--print=` 必须用 `=` 连接**，否则后续 flag 会被当成 prompt |
| json 结果字段 | `conversation_id, status(SUCCESS), response, duration_seconds, num_turns, usage{input_tokens, output_tokens, thinking_tokens, cache_read_tokens, total_tokens}` |
| stream-json 事件 | `init{model,cwd,tools[],permission_mode}` → N × `step_update{step_index,state,step_type}` → `result{...}`（可做实时进度） |
| 结构化输出 | `--json-schema '<schema>'` 生效，结果带 `structured` 字段（用于 review 的 `verdict/findings`） |
| 模型 | gemini-3.8/3.7/3.6-flash-{high,medium,low}、gemini-3.1-pro-{high,low}、claude-sonnet-4-6、claude-opus-4-6-thinking、gpt-oss-120b-medium |
| Token | `{token:{access_token, token_type, refresh_token, expiry}, auth_method:"consumer"}`；**agy 在容器内自动刷新了 access_token**（expiry 19:00 → 20:05） |
| **会话是本地状态** | 只带 token 续会话 → `conversation not found`（新建了会话）。带 `brain/<conv>/`、`conversations/<conv>.db`、`annotations/<conv>.pbtxt`、`conversation_summaries.db` → **成功续会话**，`num_turns: 3`。四件套 tar.gz = **92 KB** |
| 性能 | 单轮 20–128 s；单轮 token 14k–60k；三容器并行无干扰 |
| 需 settings.json | `permissions.allow["command(*)","write_file(*)",...]`, `toolPermission: always-proceed`（从本机拷贝） |

### 1.2 端到端闭环（`worker.sh`，Docker 容器 = VM 替身）✅

- run → 改代码 → harness commit/push → 开 PR（[PR #4](https://github.com/soren-labs/cursor-cloud-smoke-20260907/pull/4)）
- follow-up 续同一 `conversation_id` → 同分支追加 commit → PR 自动更新
- 3 容器并行（Gemini Flash / Claude Sonnet / Claude Opus review），测试门禁 `AGY_TEST_CMD` 通过并写入 PR
- review 模式：Claude Opus 在 PR #4 上提交了正式 Review，指出真实问题

### 1.3 参照物

- **Cursor**：`POST /v0/agents` 需 agent 专用 key（普通 key 会返回 500）；`autoCreatePr:true` 不稳定（同一时段一个开出 PR、一个 `pr:null`）→ 我们由 harness **确定性**开 PR；follow-up 可重激活已 FINISHED 的 agent；Pro 并发上限约 8（论坛，非文档）；v1 = durable agents + per-prompt runs；`EXPIRED` 后只能合并分支或从分支新开 agent。
- **Hoplite**：`X-Api-Key`/Bearer；project → thread；`createThread{model, speed, reasoning, autoFix, autoMerge, sourcePullRequest, repos}`；thread 状态 `queued→running→ready`，有 `pendingWakeups`；`LocalAgentExecutionTarget{provider: codex|claude, workspaceMode: direct|worktree}`；`PullRequestReviewLoop` 状态机 `observing→waiting_checks→fixing→finalizing→ready|blocked|error`；当前套餐 `project_limit_exceeded`（2 个）；公开 API 无 follow-up 端点。
- **AWS CAO**（已安装）：tmux 里编排交互式 CLI，自带 `antigravity_cli` provider（靠屏幕抓取判状态，仅 tmux 后端）。对无头批处理不必要；定位为 Phase 2 的可选客户端/编排层。
- **gcloud 559** ✅：`-max-run-duration`、`-instance-termination-action`、`startup-script`/`startup-script-url` 元数据均支持。

---

## 2. 总体架构

```
            ┌──────────────── 你 / 本地 Claude Code / CAO / CI ────────────────┐
            │   agyctl CLI · MCP server · curl                                 │
            └───────────────┬───────────────────────────────────┬──────────────┘
                            │ HTTPS  Authorization: Bearer <api key>            │ webhook 回调 (HMAC)
                            ▼                                                   │
 ┌──────────────────────── 控制面：agy-api (FastAPI, Cloud Run, min-instances=0) ─────────────────┐
 │  /v1/agents /runs /repositories /models /accounts   /webhooks/github   /internal/* (仅 VM & Scheduler)│
 │  scheduler: 容量/账号分配 → 建 VM     reaper: 孤儿 VM / 过期 lease / 快照 TTL    webhook 投递           │
 │  GitHub App: mint installation token(单仓库,1h)   Secret Manager: App key, agy tokens, webhook secret │
 └───────┬──────────────────────────┬───────────────────────────────────────┬──────────────────────┘
         │ Firestore (agents/runs/sessions/accounts)   │ GCS 签名 URL (snapshot/events/logs)      │ compute.instances.insert/delete
         ▼                                              ▼                                          ▼
 ┌────────────────────────────── Worker VM（每个 run 一台，自定义镜像 agy-worker-vN）──────────────────────────┐
 │ 宿主 (root, worker SA 仅有 run.invoker):  runner.py                                                          │
 │   1 取 run spec(/internal, ID token)  2 clone 分支  3 restore 90KB 快照  4 turn 循环  5 commit/push/PR        │
 │   6 快照上传  7 心跳/lease  8 排队 follow-up  9 finished → 控制面删 VM；兜底 max-run-duration=DELETE          │
 │   ┌──────────── agent 容器 (非 root, cap-drop ALL, 屏蔽 169.254.169.254) ────────────┐                        │
 │   │  agy --print … --conversation <id>   只看到: 工作区 + agy token(+settings)      │  ← 无 GitHub token       │
 │   │  测试容器: bash -c "$TEST_CMD"   (同镜像, 只读挂载工作区副本)                     │  ← 无 VM SA 凭证         │
 │   └──────────────────────────────────────────────────────────────────────────────┘                        │
 └──────────────────────────────────────────────┬────────────────────────────────────────────────────────────┘
                                                │ git push / PR / Check Run (installation token)      │ Antigravity API (OAuth token)
                                                ▼                                                     ▼
                                          GitHub (repo, PR, @agy 评论)                          Google Antigravity
```

### 2.1 组件职责

| 组件 | 职责 | 生命周期 |
| --- | --- | --- |
| **agy-api**（FastAPI） | 唯一的写入者：接收请求、分配账号/容量、创建 VM、签发 token 与签名 URL、接收 VM 回报、投递 webhook、GitHub webhook 处理、reaper | Cloud Run 按请求唤醒，空闲缩到 0 |
| **Worker VM** | 一个 run（可连带排队的 follow-up）的全部执行；除控制面、GitHub、Google 外不访问任何东西 | 分钟级；结束即删 |
| **agent 容器** | 运行 agy；只能改工作区 | 每个 turn 一个容器 |
| **Firestore** | agents / runs / sessions / accounts / apikeys | 永久（免费层足够） |
| **GCS** | 会话快照、事件流、stderr、测试日志 | 快照保留最近 3 版；30 天无活动后 GC |
| **Secret Manager** | GitHub App 私钥、webhook secret、每个 Antigravity 账号的 token（含刷新回写） | 永久 |
| **Cloud Scheduler** | 每分钟 `POST /internal/tick`（调度 + reaper 的安全网） | 永久（免费 3 个 job） |

### 2.2 为什么控制面不放在 worker VM 里（回应「在 VPS 里自动部署 worker 以及 FastAPI」）

- worker VM 是一次性的，而 API 必须在 run 之间、在你离线时也能接收 GitHub webhook 和 follow-up，所以它必须活在一个**便宜且持久**的地方。
- **Cloud Run 就是「不常驻的 FastAPI」**：无请求时实例数为 0，不消耗额度；有 webhook / API 调用时 1–2 s 内冷启动。`deploy.sh` 一条命令把 FastAPI 部署上去——这就是「自动部署 FastAPI」。
- **worker 的「自动部署」** = 自定义 VM 镜像（预装 docker、agy、预拉容器镜像）+ `startup-script`。每次 run 由 API 用该镜像 `instances.insert`，VM 起来后自己拉 spec 开工，不需要任何人 ssh。
- Plan B：如果你更想要一台看得见的 VPS，同一份 `docker-compose.yml` 可跑在 **GCP always-free 的 e2-micro**（us-central1/us-west1/us-east1 各 1 台免费）+ Caddy 自动 TLS。代码零改动，只是 `/internal/*` 鉴权从 ID token 换成共享 secret。

---

## 3. 生命周期、回收与恢复

### 3.1 三个概念（对齐 Cursor v1）

- **agent**（= Cursor agent = Hoplite thread）：持久对象。一段对话 + 一个分支 + 一个 PR + 一个 agy `conversation_id` + 一个绑定的 Antigravity 账号。
- **run**：一次 prompt 的执行（初始 prompt 或 follow-up），对应 agy 的一个 turn。
- **session**：一台 VM 的生命周期。一个 session 顺序执行 ≥1 个 run（初始 run + 运行期间排进来的 follow-up）。

### 3.2 状态机

```
 POST /v1/agents
      │
      ▼
  CREATING ──VM 取到 spec、拿到 lease──▶ RUNNING ──turn 完成 && 队列空──▶ IDLE(热等, 计时 idleTimeout) ──超时──▶ FINISHED
      │                                   │  ▲                              │   ▲                                   │   (VM 已删；快照在 GCS；分支/PR 在 GitHub)
      │ 建 VM 失败 / 配额不足 → QUEUED ────┘  │                              │   │ 快照已在进入 IDLE 前上传          │
      │   (tick 重试)                        │                              └───┘ follow-up 到达 → 直接复用 VM，3–5 s 开工  │
      │                                     └── RUNNING 中 follow-up 到达 → 排队，本轮结束后接着跑    ├── follow-up ──▶ CREATING（新 VM，restore 快照，~60 s）
      │                                                                                          ├── 30 天无活动 ──▶ EXPIRED（快照 GC；分支/PR 保留；可从分支新开 agent）
      ├── run 报错 ────────────────────────▶ ERROR   （尽量仍上传快照；VM 删除）                    └── DELETE /v1/agents/{id} ──▶ ARCHIVED
      ├── POST /stop ─────────────────────▶ CANCELLED（WIP 提交推送；快照；VM 删除）
      └── lease 丢失（VM 死/Spot 抢占）───▶ ERROR:lost_lease → 若 retries<2 且原因=preempted 自动重排队
```

状态字面量与 Cursor 一致：`CREATING RUNNING FINISHED ERROR CANCELLED EXPIRED`，另加 `QUEUED`、`ARCHIVED`（Cursor v1 也有 archive）。

### 3.3 回收机制（三层，任一层失效都不会留下常驻 VM）

| 层 | 机制 | 触发 |
| --- | --- | --- |
| L1 正常路径 | VM 上报 `finished` → 控制面 `instances.delete`；上报失败则 VM `poweroff`（TERMINATED，不计 CPU 费） | 每次 session 结束 |
| L2 GCE 硬兜底 | 创建 VM 时 `--max-run-duration=<session_max+15m> --instance-termination-action=DELETE`，由 GCE 自己执行，与控制面是否存活无关 ❓（DELETE 对标准 VM 的支持在首次部署验证） | VM 超时 |
| L3 reaper | `/internal/tick`（Cloud Scheduler 每分钟 + 每个 VM 结束时回调）：列出 `labels.agy-role=worker` 的实例，删除「对应 run 不在 RUNNING」或「年龄 > session_max」的；把心跳超时（>3 min）的 agent 标 `ERROR:lost_lease` 并释放账号槽位 | 每分钟 |

**空闲策略**：默认 `keep_warm_seconds = 0`——turn 结束、队列为空就立刻快照 + 退出，绝不空转。可按 agent 设置 `keep_warm_seconds`（如 120）：在对话密集时避免每条 follow-up 都付 ~60 s 冷启动。这是本方案对「每次都干净」唯一可控的放宽。

### 3.4 恢复机制（每台新 VM 都从零重建）

新 VM 上重建一个 agent 所需的全部输入：

1. **代码**：`git clone --depth 50 --branch agy/<slug>-<hex>`（GitHub 是工作区的唯一真源，每个 turn 结束都已 push）。
2. **对话**：`gs://…/agents/<id>/snapshot/<seq>.tar.gz`（90 KB）解到 `~/.gemini/antigravity-cli/`，然后 `agy --conversation <id>`。✅
3. **凭证**：绑定账号的 agy token（Secret Manager，最新版本）+ 单仓库 1 h GitHub installation token（控制面签发）。
4. **仓库环境**：`.agy/environment.json` 里的 `setup` 命令（如 `pip install -r requirements.txt`）在 agent 容器内重跑；Phase 2 可缓存依赖层。

快照兼容性策略：快照记录 `agy_version`；镜像固定 agy 版本；升级 agy 前先用旧快照做一次 restore 回归。若不兼容或快照缺失 → **降级模式**：用 Flash 模型把历史 runs 的 prompt/response 压缩成「上下文摘要」作为新会话首条消息（新 `conversation_id`），并在 agent 上标记 `context_handoff: true`。对话历史本身永远还在 Firestore（`runs` 集合），不依赖 agy 快照。

### 3.5 follow-up 竞态（不丢、不重、不空转）

核心：agent 上有 `lease{sessionId, expiresAt}`（VM 每 30 s 续 120 s）和 `pendingRuns` 计数，全部通过 Firestore **事务**修改。

```python
# 控制面: POST /v1/agents/{id}/followup
@transactional
def enqueue_followup(tx, agent_ref, prompt) -> bool:
    agent = agent_ref.get(transaction=tx)
    tx.set(agent_ref.collection("runs").document(new_run_id()),
           {"seq": agent.runCount + 1, "prompt": prompt, "status": "QUEUED", "createdAt": now()})
    tx.update(agent_ref, {"pendingRuns": Increment(1), "runCount": Increment(1)})
    return bool(agent.lease and agent.lease.expiresAt > now())   # True: 活着的 VM 会接; False: 调用方立刻建新 VM

# VM runner: 每个 turn 结束、快照已上传之后
@transactional
def try_release(tx, agent_ref, session_id) -> bool:
    agent = agent_ref.get(transaction=tx)
    if agent.pendingRuns > 0:
        return False                       # 继续循环: pop 下一个 QUEUED run（也是事务: RUNNING, pendingRuns-1）
    tx.update(agent_ref, {"lease": None, "status": "FINISHED"})
    return True                            # 之后 VM 退出
```

因为「看到 lease 存活并入队」与「看到队列为空并释放 lease」是串行化事务，二者只能有一个先发生：要么 VM 看见新 run 继续干，要么控制面看见 lease 已空而新建 VM。快照总在释放 lease **之前**上传，所以新 VM 一定能拿到最新对话。运行中的 run 收到 `stop` → runner 给 agy 发 SIGTERM → 提交 WIP → 快照 → `CANCELLED`。

---

## 4. Worker VM 详细设计

### 4.1 镜像 `agy-worker-vN`（`infra/image-build.sh`，~5 分钟一次）

- Ubuntu 24.04 LTS → 安装 docker、python3 + `google-auth`/`requests`、`agy`（固定版本，`curl -fsSL https://antigravity.google/cli/install.sh | bash` 后把二进制固化）、预拉 agent 基础镜像 `agy-agent:base`（ubuntu + git + python3 + node + jq）。
- 写入 `settings.json` 模板（always-proceed）、`runner.py`、`startup.sh`；`iptables` 规则脚本（见 4.4）。
- `gcloud compute images create agy-worker-v1 --source-disk=...` → 删除构建 VM。**效果**：每次 run 跳过 apt/curl，冷启动从 ~3 min 降到 ~40–60 s。

### 4.2 创建 VM（控制面 `services/gce.py`）

```bash
gcloud compute instances create agy-${RUN8} \
  --zone=us-central1-a --machine-type=${MACHINE:-e2-standard-2} \
  --image=agy-worker-v1 --boot-disk-size=30GB --boot-disk-type=pd-balanced \
  --service-account=agy-worker@$PROJECT.iam.gserviceaccount.com --scopes=cloud-platform \
  --labels=agy-role=worker,agy-agent=${AGENT_ID},agy-run=${RUN_ID} \
  --metadata=agy-run-id=${RUN_ID},agy-api=${API_URL},enable-oslogin=TRUE \
  --metadata-from-file=startup-script=worker/startup.sh \
  --max-run-duration=2h15m --instance-termination-action=DELETE \
  [--provisioning-model=SPOT]        # 成本模式；抢占 → GCE 直接 DELETE，reaper 重排队
```

无入站端口（默认防火墙即可），不需要 SSH。外网出口用临时外部 IP（比 Cloud NAT 便宜且简单）。

### 4.3 `runner.py` 主循环（宿主侧，~400 行，把已验证的 `worker.sh` 逻辑搬进来）

```
startup.sh: 读元数据 run-id/api → 取 ID token(audience=API) → exec runner.py
runner:
  spec = GET /internal/runs/{run}/spec            # agent, repo, branch, base, model, mode, prompt, resume, tests, env,
                                                  # agy_token, github_token(1h), signed_urls{snapshot_get, snapshot_put, events_put, stderr_put, tests_put}
  apply_iptables()                                # 容器 → 169.254.169.254 REJECT
  git clone --depth 50 (resume ? branch : base) → /work/ws ; git checkout -b branch (首次)
  if resume: curl signed_urls.snapshot_get | tar xz -C /work/home/.gemini/antigravity-cli
  write agy_token, settings.json → /work/home/.gemini/antigravity-cli/
  start heartbeat thread: POST /internal/sessions/{ses}/heartbeat every 30s  (返回 cancel_requested)
  run = spec.initial_run
  loop:
    write TASK.md (含仓库 AGENTS.md 提示、规则: 只改 /home/ubuntu/ws、不要 git commit/push)
    docker run --rm --user ubuntu --cap-drop ALL --pids-limit 512 --memory 6g --network agy-net \
       -v /work/home:/home/ubuntu -v /work/ws:/home/ubuntu/ws -v /work/task:/home/ubuntu/task:ro agy-agent:${image} \
       agy --print="$(cat /work/task/prompt.txt)" --output-format stream-json --dangerously-skip-permissions \
           --model $model [--conversation $conv] --add-dir /home/ubuntu/ws --print-timeout ${turn_timeout}
       │ tee events.jsonl → 每 5 s PUT 到 signed_urls.events_put（覆盖写）
    result = 最后一行 result 事件 (response, usage, conversation_id)
    tests  = spec.tests ? docker run … bash -c "$TEST_CMD" : skipped        # 独立容器，无 agy token
    git add -A && git commit (author = App bot) && git push -u origin branch        # 宿主侧，agent 看不到 GH token
    if first push: POST /repos/{repo}/pulls (title, body=summary+footer, draft?) ; POST check-run "agy/tests"
    else: PATCH check-run ; 评论测试结果 (可配置)
    tar czf snapshot.tgz brain/<conv> conversations/<conv>.db annotations/<conv>.pbtxt conversation_summaries.db → PUT snapshot_put
    POST /internal/runs/{run}/finished {status, response, usage, duration, tests, commit, pr_url, conversation_id, agy_version}
    if cancel_requested: break
    wait ≤ keep_warm_seconds for pendingRuns>0 (heartbeat 响应里带)
    run = POST /internal/agents/{id}/pop-run   # 事务: 有则返回下一 run, 无则释放 lease 并返回 null
    if run is None: break
  POST /internal/accounts/{acct}/token  (若 refresh_token/expiry 变化则回写)
  POST /internal/sessions/{ses}/finished {reason}    → 控制面 instances.delete
  fallback: sleep 60; poweroff
```

- prompt 通过文件传递（`prompt.txt` + `TASK.md`），避开 argv 128 KB 限制和元数据 256 KB 限制；大上下文放 `task/` 目录让 agy 自己读。
- `mode`：`code`（默认）、`review`（拉 PR diff，`-json-schema` 输出 `{verdict, findings[]}`，提交 PR Review，不推代码）、`plan`（只读，输出到 run.response，不建分支）。
- 每个 turn 的 `usage` 累加到 agent 与 account（配额观测）。

### 4.4 隔离与凭证边界

| 凭证 | 在哪 | agent 能否拿到 |
| --- | --- | --- |
| VM 服务账号（只有 `run.invoker`） | 元数据服务器 | **否**：`iptables -I DOCKER-USER -d 169.254.169.254 -j REJECT` |
| GitHub installation token（单仓库、1 h） | 仅宿主 runner 内存/环境变量 | **否**：commit/push/PR 全在宿主完成 ✅（worker.sh 已如此） |
| Antigravity OAuth token | agent 容器 `~/.gemini/...` | **是**（agy 必须读）→ 用**专用 Google 账号**，不要用主账号 |
| 会话快照 / 事件 签名 URL | 仅宿主 | 否 |

容器加固：非 root、`--cap-drop ALL`、`--pids-limit`、内存上限、无 docker socket；测试容器只读挂载工作区副本。Phase 3：出口白名单代理（github.com、*.googleapis.com、pypi/npm 镜像）。

### 4.5 超时

| 项 | 默认 | 说明 |
| --- | --- | --- |
| turn（agy `--print-timeout`） | 30 min | 超时 → run `ERROR:timeout`，仍提交 WIP + 快照 |
| session_max | 2 h | runner 自检；`max-run-duration = session_max + 15 min` |
| 心跳 lease | 120 s / 续 30 s | 丢失 3 min → reaper 判死 |
| 冷启动预算 | ≤ 60 s 到首个 `init` 事件 | 镜像预热 + 无 apt |

---

## 5. 多账号 Antigravity 池

```
accounts/{accountId}: { name, secretName, status: ok|cooldown|needs_relogin|disabled,
                        maxConcurrent: 2, activeRuns, cooldownUntil, usageToday{total_tokens, runs},
                        lastUsedAt, lastError }
```

- **登录**：每个订阅在有浏览器的机器上 `agy` 登录一次 → `agyctl accounts add pro-2 --token-file ~/.gemini/antigravity-cli/antigravity-oauth-token`（API 写入 Secret Manager `agy-token-pro-2`）。
- **分配**（事务）：优先 agent 已绑定的账号（**sticky**，因为快照是否能跨账号恢复未验证 ❓）；无绑定时取 `status=ok && activeRuns<maxConcurrent` 中 `usageToday` 最小者；都满 → run `QUEUED`，tick 重试。
- **回写**：session 结束时若 token 文件变化（agy 自刷新 ✅）→ 新 secret version；控制面始终下发最新版本。
- **配额/封控**：agy stderr 或 result 匹配 `/quota|RESOURCE_EXHAUSTED|429|rate limit/i` → 账号 `cooldown` 30 min，run 重排队（sticky 账号则等待）；匹配 `not logged in|invalid_grant|unauthorized` → `needs_relogin` + webhook/通知，人工重登录后 `accounts add` 覆盖。
- **预算**：`usageToday.total_tokens` 超阈值 → 当日不再分配（Antigravity Pro 的真实配额未知，先观测）。

---

## 6. GitHub 集成（对齐 Cursor）

### 6.1 GitHub App `agy-cloud-agent`（私有，安装到 soren-labs 选定仓库）

- 权限：Contents RW · Pull requests RW · Issues RW · Checks RW · Metadata R · Workflows RW（仅当允许 agent 改 `.github/workflows`）。
- 事件：`issue_comment`、`pull_request_review_comment`、`pull_request_review`、`pull_request`、`check_suite`、`installation_repositories`。
- Webhook → `https://<api>/webhooks/github`，`X-Hub-Signature-256` 校验。
- 通过 [manifest flow](https://docs.github.com/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) 一键创建，`deploy.sh` 把 `app_id / private key / webhook secret` 写入 Secret Manager。
- `GET /v1/repositories` = App 的 installation 仓库列表（等价 Cursor `/v0/repositories` ✅）。

### 6.2 分支 / 提交 / PR 约定

| 项 | 约定 |
| --- | --- |
| 分支 | `agy/<slug-40>-<4hex>`（Cursor：`cursor/<slug>-<4hex>`）；可指定 `target.branchName` |
| 提交作者 | `agy-cloud-agent[bot] <ID+agy-cloud-agent[bot]@users.noreply.github.com>`；trailer `Agent: antigravity-cli (<model>) run <run_id>` |
| PR | 首次 push 后由宿主**确定性**创建（不依赖模型）；`draft` 可选；正文 = 模型摘要 + 测试结果 + 页脚（agent 链接、模型、conversation_id）；每个后续 turn 追加一条评论「第 N 轮：…」；`requestReviewer` 可选（Cursor 默认请求发起人 review，`skipReviewerRequest` 关闭） |
| Check Run | `agy / tests`：`AGY_TEST_CMD` 结果（success/failure + 日志摘要），PR 页面直接可见 |
| 以自己身份开 PR | 可选：`accounts` 里存你的 fine-grained PAT，`target.openAs: "user"`（Cursor 的 `openAsCursorGithubApp:false` 反向等价） |

### 6.3 `@agy` 触发（等价 `@cursor`）

| 场景 | 行为 |
| --- | --- |
| Issue 评论 `@agy <指令>` | 新建 agent：prompt = Issue 标题 + 正文 + 指令；PR 正文 `Closes #N`；回复评论附 agent 链接 |
| agent 自己 PR 上的评论 `@agy <指令>` | `followup` 到拥有该 PR 的 agent（按 `target.prNumber` 反查）；给评论加 👀，完成后 ✅ + 回复 |
| 非 agent 的 PR 上 `@agy <指令>` | 从 PR 头分支新建 agent（Hoplite `sourcePullRequest` 语义） |
| Review 提交（含 inline 评论） | 聚合为一条 follow-up：「处理以下审查意见：[file:line](file:///line) …」（Cursor「address review comments」） |
| `@agy review` | `mode=review` 的 run，用 `review_model`（默认 claude-opus-4-6-thinking） |
| 只响应白名单用户（仓库 collaborator 且 `association ∈ {OWNER, MEMBER, COLLABORATOR}`） | 防止陌生人触发消耗额度 |

### 6.4 可选：CI 自动修复循环（借 Hoplite `PullRequestReviewLoop`）

按 agent 或仓库开启 `autoFix`：`check_suite.completed` 且 conclusion=failure 且 head 分支属于某 agent → 自动 follow-up「CI 失败：<失败 job 与最后 60 行日志>，请修复并确保通过」，最多 `maxPasses=3`，状态 `observing → waiting_checks → fixing → ready|blocked`。`autoMerge` 明确**不做**（MVP）。

### 6.5 仓库配置 `.agy/environment.json`（对齐 `.cursor/environment.json`）

```json
{ "image": "agy-agent:base",
  "setup": "pip install -r requirements.txt",
  "test":  "python3 -m unittest discover -s tests -v",
  "machineType": "e2-standard-2", "turnTimeoutMinutes": 30,
  "defaultModel": "gemini-3.1-pro-high", "reviewModel": "claude-opus-4-6-thinking" }
```

`AGENTS.md` 作为系统级说明拼进 TASK.md（Hoplite/Cursor 皆读取仓库规则文件）。

---

## 7. 控制面 API（`/v1`，形状对齐 Cursor）

### 7.1 鉴权

- `Authorization: Bearer agyc_<random>`；Firestore `apikeys/{sha256}`；单租户，可多 key（本地 Claude Code、CI、MCP 各一把）。
- `/webhooks/github`：`X-Hub-Signature-256`。
- `/internal/*`：Google ID token（Cloud Run IAM `run.invoker`，仅 worker SA 与 Scheduler SA）。
- 平台限速：60 req/min/key（`X-RateLimit-*` 头）。

### 7.2 端点

| 方法 & 路径 | 作用 | Cursor 对应 |
| --- | --- | --- |
| `GET /v1/me` | key 名、创建时间 | `/v0/me` |
| `GET /v1/models` | agy 模型列表（缓存 `agy models`）+ 默认值 | `/v0/models` |
| `GET /v1/repositories` | App 已安装仓库 | `/v0/repositories` |
| `POST /v1/agents` | 创建 agent 并排队首个 run（下文示例） | `POST /v0/agents` |
| `GET /v1/agents?status=&repo=&limit=&cursor=` | 列表 | `GET /v0/agents` |
| `GET /v1/agents/{id}` | 状态、分支、PR、账号、统计、快照信息 | `GET /v0/agents/{id}` |
| `GET /v1/agents/{id}/conversation` | `[{type:user_message\|assistant_message, text, runId}]`（由 runs 重建） | `/conversation` ✅ 同形 |
| `POST /v1/agents/{id}/followup` | 追加 run（运行中排队 / 已结束则新 VM 恢复） | `/followup` |
| `POST /v1/agents/{id}/stop` | 取消当前 run，保留可恢复 | v1 lifecycle |
| `POST /v1/agents/{id}/archive` · `/unarchive` | 归档/恢复 | v1 |
| `DELETE /v1/agents/{id}` | 归档 + 删快照（分支/PR 不动） | `DELETE /v0/agents/{id}` |
| `GET /v1/agents/{id}/runs` · `/runs/{run}` | 每轮的 prompt/response/usage/tests/commit | v1 runs |
| `GET /v1/agents/{id}/runs/{run}/events?offset=` | stream-json 事件（tail 语义，SSE 可选） | — |
| `GET /v1/agents/{id}/runs/{run}/logs` | stderr / tests.log 签名 URL | — |
| `GET/POST /v1/accounts` · `POST /v1/accounts/{id}/token` | Antigravity 账号池管理 | — |
| `POST /v1/reviews` | 快捷：对 `prUrl` 发起 review 模式 agent | —（Bugbot） |
| `POST /webhooks/github` | GitHub 事件 | — |
| `POST /internal/tick` | 调度 + reaper（Scheduler / VM 回调） | — |
| `GET /internal/runs/{run}/spec` · `POST /internal/runs/{run}/finished` · `POST /internal/sessions/{s}/heartbeat\|finished` · `POST /internal/agents/{id}/pop-run` · `POST /internal/accounts/{a}/token` | VM ↔︎ 控制面 | — |

### 7.3 创建示例

```bash
curl -sS -X POST "$API/v1/agents" -H "Authorization: Bearer$AGY_CLOUD_API_KEY" -H 'Content-Type: application/json' -d '{
  "prompt":  {"text": "Add clamp(x, lo, hi) to number_utils.py with unit tests", "images": []},
  "source":  {"repository": "https://github.com/soren-labs/cursor-cloud-smoke-20260907", "ref": "main"},
  "model":   "gemini-3.1-pro-high",
  "mode":    "code",
  "target":  {"autoCreatePr": true, "draft": false, "branchName": null, "requestReviewer": false},
  "tests":   {"command": "python3 -m unittest discover -s tests -v"},
  "compute": {"machineType": "e2-standard-2", "spot": false, "keepWarmSeconds": 0},
  "webhook": {"url": "https://hooks.example.com/agy", "secret": "…"},
  "autoFix": false
}'
# 201
{"id":"ag_7HkQ…","name":"Add clamp utility","status":"CREATING",
 "source":{"repository":"github.com/soren-labs/cursor-cloud-smoke-20260907","ref":"main"},
 "target":{"branchName":"agy/add-clamp-x-lo-hi-3f1a","url":"https://<api>/v1/agents/ag_7HkQ…","prUrl":null,"autoCreatePr":true},
 "model":"gemini-3.1-pro-high","account":"pro-1","run":{"id":"run_01…","status":"QUEUED"},"createdAt":"2026-09-08T12:30:00Z"}
```

### 7.4 Webhook（对齐 Cursor `statusChange`）

```
POST <webhook.url>
X-Webhook-ID: evt_…   X-Webhook-Event: statusChange   X-Webhook-Signature: sha256=<hmac(body, secret)>
{"event":"statusChange","timestamp":"…","id":"ag_…","status":"FINISHED",
 "source":{…},"target":{"branchName":"…","prUrl":"…","url":"…"},
 "run":{"id":"run_…","seq":2,"usage":{"total_tokens":14507},"durationSeconds":20.0,"tests":"passed","commit":"c4a2b03"},
 "summary":"Added clamp() …"}
```

事件：`RUNNING`、`FINISHED`、`ERROR`、`CANCELLED`、`EXPIRED`、`pr_opened`。投递失败指数退避重试 5 次（10 s … 15 min）。

### 7.5 客户端

- `agyctl`（Python/click，~200 行）：`launch / ls / status / conv / followup / stop / archive / review / logs -f / accounts add|ls / repos / models`。
- `agy-cloud-mcp`（~100 行）：把上述包装成 MCP 工具，供本地 Claude Code、Cursor、CAO 直接调用（对应 Cursor 的 SDK/MCP）。

---

## 8. 数据模型

### 8.1 Firestore（Native 模式）

```
agents/{agentId}
  name, status, mode, model, reviewModel
  source{repository, ref}            target{branchName, prUrl, prNumber, autoCreatePr, draft, requestReviewer, openAs}
  accountId, runCount, pendingRuns
  lease{sessionId, instanceName, zone, expiresAt} | null
  snapshot{gcsUri, seq, conversationId, agyVersion, updatedAt, contextHandoff:boolean}
  stats{runs, linesAdded, filesChanged, totalTokens, totalSeconds}
  webhook{url, secretRef}     autoFix{enabled, passes, maxPasses, state}
  createdAt, updatedAt, lastRunAt, expiresAt(=lastRunAt+30d), archivedAt
  runs/{runId}: seq, prompt{text, imagesGcs[]}, origin(api|github_comment|review|ci_autofix), status,
                sessionId, startedAt, finishedAt, response, usage{…}, durationSeconds,
                tests{command, status, logGcs}, commit, eventsGcs, stderrGcs, error{code, message}, retries
sessions/{sessionId}: agentId, instanceName, zone, machineType, spot, imageVersion, createdAt, bootSeconds,
                      endedAt, endReason(finished|cancelled|error|lost_lease|preempted|timeout), runIds[]
accounts/{accountId}: 见 §5
apikeys/{sha256}: name, createdAt, lastUsedAt, scopes
repos/{owner__name}: installationId, repositoryId, defaultBranch, environment(json 缓存), autoFixDefault
webhook_deliveries/{id}: agentId, event, attempts, nextAt, lastStatus
```

索引：`agents(status, updatedAt desc)`、`agents(target.prNumber, source.repository)`、`runs(status)`（跨集合）、`agents(expiresAt)`。

### 8.2 GCS `gs://agy-cloud-<project>/`

```
agents/<agentId>/snapshot/<seq>.tar.gz        保留最近 3 个；EXPIRED 时删除
agents/<agentId>/runs/<runId>/events.jsonl    stream-json（每 5 s 覆盖）
agents/<agentId>/runs/<runId>/stderr.log · tests.log · result.json
sessions/<sessionId>/runner.log
```

生命周期规则：`runs/**` 90 天转 Coldline / 365 天删除；`snapshot/**` 由 reaper 管理。

---

## 9. 部署与自动化（`infra/deploy.sh`，幂等，一次跑完）

```
 0  前置: gcloud auth login; gcloud config set project $P; gh auth (仅用于 manifest 流程的浏览器打开)
 1  gcloud services enable compute run firestore secretmanager cloudscheduler artifactregistry cloudbuild iam
 2  gcloud firestore databases create --location=nam5 ; gsutil mb -l us-central1 gs://agy-cloud-$P ; 生命周期规则
 3  服务账号
    agy-control: roles/compute.instanceAdmin.v1 (IAM 条件 resource.name startsWith "…/instances/agy-")
                 roles/iam.serviceAccountUser (on agy-worker)  roles/datastore.user  roles/storage.objectAdmin (bucket)
                 roles/secretmanager.secretAccessor (agy-* 与 github-* secrets)  roles/logging.logWriter
    agy-worker : 无项目级角色；仅 roles/run.invoker on service agy-api   ← 被入侵也几乎无权
    agy-sched  : roles/run.invoker on agy-api
 4  构建 worker 镜像: infra/image-build.sh → agy-worker-v1  (后续 bump 版本，旧镜像保留一个用于回滚)
 5  部署 API:  gcloud run deploy agy-api --source ./api --region us-central1 --service-account agy-control \
                --min-instances 0 --max-instances 3 --concurrency 40 --timeout 60 --no-allow-unauthenticated?
                → 公开 /v1 与 /webhooks 需要 allow-unauthenticated + 应用层 API key；/internal 用 ID token 校验
 6  GitHub App: 打开 manifest URL → 回调 code → API 换取 app_id/pem/webhook_secret → Secret Manager；安装到仓库
 7  Cloud Scheduler: agy-tick  "* * * * *"  POST $API/internal/tick  (OIDC, agy-sched)
 8  agyctl init: 生成首把 API key → 写入 ~/.config/agy-cloud/config ; agyctl accounts add pro-1 --token-file …
 9  冒烟: agyctl launch soren-labs/cursor-cloud-smoke-20260907 "Add a hello() to string_utils with tests" --tests "…"
    → 观察 CREATING→RUNNING→FINISHED、PR、VM 消失（gcloud compute instances list 为空）
```

**本地开发模式**（不需要 GCP）：`AGY_CLOUD_BACKEND=docker uvicorn api.main:app` — 同一 API，状态用本地 SQLite/文件，`sessions` 直接在本机起 `agy-agent` 容器（即今天验证的 worker.sh 路径）。CI 用它跑集成测试。

**Plan B（e2-micro VPS）**：`infra/vps/docker-compose.yml`（api + caddy），`gcloud compute instances create agy-cp --machine-type e2-micro --zone us-central1-a`，一样由脚本完成。区别仅在 `/internal` 鉴权与无自动缩容。

---

## 10. 并发、配额与成本

- **并发上限** = `min( floor(区域 vCPU 配额 / VM vCPU), Σ accounts.maxConcurrent, MAX_CONCURRENT_RUNS )`。新项目默认 vCPU 配额常见 8–24（试用账号可能更低）→ e2-standard-2 时 4–12 台；`gcloud compute regions describe us-central1` 查看，可申请提额。轻量仓库可用 e2-medium（2 vCPU 共享/4 GB，≈ 一半价钱）。
- **排队**：超限的 run 处于 `QUEUED`，`tick` 每分钟或每次 VM 结束时立刻调度，不需要常驻调度进程。
- **成本（us-central1，约数，以定价页为准）**：

| 项 | 单价 | 300 run/月 × 10 min（含 1 min 启动） |
| --- | --- | --- |
| e2-standard-2 按需 | ≈ $0.067/h | ≈ $3.7 |
| e2-standard-2 Spot | ≈ $0.020/h | ≈ $1.1 |
| 30 GB pd-balanced（按分钟计） | ≈ $0.10/GB·月 | ≈ $0.2 |
| 临时外部 IP | ≈ $0.005/h | ≈ $0.3 |
| Cloud Run（min 0） | 免费层 200 万请求 | ≈ $0 |
| Firestore / GCS / Scheduler | 免费层 | ≈ $0 |
| Secret Manager | $0.06/版本·月 | ≈ $0.6 |
| **合计** |  | **≈ $5/月**（对比常驻 e2-standard-4 ≈ $98/月；常驻 e2-standard-2 ≈ $49/月） |
- **Spot 模式**：`compute.spot=true`。抢占 → GCE `DELETE` → 心跳丢失 → reaper 标 `preempted` 并自动重排队（≤2 次）。因为每个 turn 结束都 push + 快照，最多丢当前一轮。

---

## 11. 安全与风险

| 风险 | 缓解 |
| --- | --- |
| **Antigravity ToS**：消费级 Pro 订阅（`auth_method: consumer`）在服务器上无人值守自动化 + 多账号轮转，可能违反条款，存在封号风险 | 这是最大的非技术风险，无技术缓解。建议：专用账号、合理并发（每账号 ≤2）、观测配额、准备 Antigravity 官方 API/企业方案作为替代大脑（runner 的 agent 层可替换为 Claude Code / Codex CLI，接口不变） |
| 仓库内容对 agent 的提示注入 → 窃取凭证 | GitHub token 永不进入容器；VM SA 元数据被 iptables 屏蔽；agy token 不可避免地在容器内 → 专用账号；Phase 3 出口白名单 |
| VM 泄漏（常驻） | §3.3 三层回收 |
| 无限循环烧额度（autoFix、@agy 互相触发） | `maxPasses`、白名单用户、bot 评论不触发、每 agent 每小时 run 上限 |
| Cloud Run 不可用时 VM 无法上报 | VM 指数退避重试 30 min；仍失败则 poweroff，快照已在 GCS，reaper 随后清理并标 ERROR |
| 快照跨 agy 版本不兼容 | 镜像固定版本；升级前回归；降级「上下文摘要」模式保证可继续 |
| API key 泄露 | 所有 key 哈希存储、可吊销；`/v1` 限速；仓库范围由 GitHub App 安装决定 |

---

## 12. 实施计划

| 阶段 | 内容 | 规模 | 验收 |
| --- | --- | --- | --- |
| **P0 已完成** ✅ | agent 容器层（worker.sh：run/followup/review/tests）、快照/恢复实验、并行、Cursor/Hoplite/CAO 调研 | — | 本文 §1 |
| **P1 MVP（约 2–3 天）** | `infra/deploy.sh`、`image-build.sh`、`worker/startup.sh + runner.py`、`api/`（agents/runs/repositories/models/accounts/internal/tick/reaper/webhook 投递）、GitHub App（token 铸造、PR、Check Run）、`agyctl` | api ≈ 900 行 · runner ≈ 400 行 · infra ≈ 300 行 · cli ≈ 200 行 | ① 冒烟 run 出 PR 且 VM 消失 ② 已 FINISHED 的 agent follow-up 在新 VM 续聊并更新 PR ③ 运行中追加 follow-up 同 VM 完成 ④ 5 个 agent 并行 ⑤ 杀掉 VM 后 reaper 3 min 内标 ERROR 并清理 ⑥ 断掉控制面 5 min，VM 结束后自行 poweroff 且快照已上传 |
| **P2（约 2 天）** | `/webhooks/github`（`@agy`、review 评论聚合、Issue 触发）、autoFix 循环、SSE 事件流、MCP server、Spot、`.agy/environment.json` + 依赖缓存、多账号回写与冷却 | ≈ 600 行 | PR 评论 `@agy` 触发 follow-up；CI 失败自动修复一轮 |
| **P3** | 出口白名单代理、预算/告警、只读 Web 面板、CAO 供应商接入（把 agy-cloud 作为 CAO 的远程 worker）、跨账号快照实验 | 按需 | — |

---

## 13. 未决问题 / 首次部署验证清单

1. ❓ `-max-run-duration` + `-instance-termination-action=DELETE` 对标准（非 Spot）VM 是否被接受；若否，用 STOP + reaper 删除 TERMINATED 实例。
2. ❓ 快照跨 Antigravity 账号 / 跨 agy 版本恢复是否成功（决定 sticky 是否可放宽）。
3. ❓ Antigravity Pro 单账号的真实并发与日配额；`cache/default_project_id.txt` 与 `-project` 是否影响会话（本次未包含 cache 也能恢复）。
4. ❓ GitHub App bot 提交在 PR 中的归属显示；Workflows 权限是否需要。
5. ❓ 目标项目的区域 vCPU 配额（决定初始并发）。
6. 决策：控制面选 Cloud Run（推荐）还是 always-free e2-micro；机器默认 e2-standard-2 还是 e2-medium；是否默认 Spot。
7. 决策：哪些 Google 账号用于订阅池（建议全部为专用账号）。

---

## 附录 A：agy 无头速查（✅）

```bash
agy models                                                     # 验证登录 + 列模型
agy --output-format json --dangerously-skip-permissions --model gemini-3.1-pro-high \
    --add-dir /home/ubuntu/ws --print-timeout 30m --print="$(cat prompt.txt)"         # 新会话
agy … --conversation <conversation_id> --print="…"             # 续会话（需本地快照四件套）
agy … --output-format stream-json --print="…"                  # 事件流: init / step_update / result
agy … --json-schema '{"type":"object","properties":{"verdict":{"enum":["approve","request_changes"]},"findings":{"type":"array"}},"required":["verdict","findings"]}' --print="Review …"
# 快照四件套（相对 ~/.gemini/antigravity-cli）: brain/<id>/ conversations/<id>.db annotations/<id>.pbtxt conversation_summaries.db
```

## 附录 B：仓库布局

```
agy-cloud/
  api/        main.py  routes/{agents,runs,repos,models,accounts,webhooks,internal}.py
              services/{scheduler,reaper,gce,github_app,firestore,storage,secrets,webhooks}.py  models.py  auth.py
  worker/     startup.sh  runner.py  Dockerfile.agent  iptables.sh  settings.template.json
  infra/      deploy.sh  image-build.sh  iam.sh  scheduler.sh  vps/docker-compose.yml  firestore.indexes.json
  cli/        agyctl.py  mcp_server.py
  tests/      e2e_local.sh (docker backend)  e2e_gcp.sh (冒烟六项)
  docs/       DESIGN.md (本文)
```

## 附录 C：reaper 伪代码

```python
def tick():
    schedule_queued_runs()                                    # 受 vCPU 配额 / 账号并发 / 全局上限约束
    for inst in gce.list(filter="labels.agy-role=worker"):
        run = db.run(inst.labels["agy-run"])
        if run is None or run.status not in ("QUEUED", "RUNNING") or age(inst) > SESSION_MAX + 15*60:
            gce.delete(inst); db.session(inst.name).end("reaped")
    for agent in db.agents(status="RUNNING", lease_expired_before=now()-180):
        run = agent.current_run(); cause = "preempted" if gce.was_preempted(agent.lease.instanceName) else "lost_lease"
        run.fail(cause); agent.release_lease(status="ERROR"); accounts.release(agent.accountId)
        if cause == "preempted" and run.retries < 2: run.requeue()
        webhooks.emit(agent, "ERROR")
    for agent in db.agents(status_in=("FINISHED","ERROR","CANCELLED"), expiresAt_before=now()):
        storage.delete_prefix(f"agents/{agent.id}/snapshot/"); agent.set(status="EXPIRED")
    webhooks.retry_due(); accounts.clear_expired_cooldowns()
```