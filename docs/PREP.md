# PREP — agy-cloud 多并发云端 Agent 开发前置准备

> 目标：用 **Cursor Cloud Agents + Hoplite** 的工作模式（本地只做编排与验收，开发/review/测试全部由云端 agent 并发完成）来实现 `design.md` 定稿的 **agy-cloud** 项目。
> 本文档 = 开工前必须一次性做完的所有准备。做完后，本地的工作只剩：按 §6 的 DAG 逐波次发任务 → 收 PR → 合并。
>
> 状态：2026-09-09 起草。基础事实来源：历史会话 `9e3bfb4e`（Cursor/Hoplite/agy 实测）+ `design.md`（2026-09-08 定稿）。
> 本文件应作为 `docs/PREP.md` 提交进主仓库首个 commit。

---

## 0. 工作模式（我们要复刻的开发流程）

```
本地 (WSL, Claude Code)                     云端
┌─────────────────────────┐    POST /v0/agents     ┌──────────────────────────┐
│ 编排者：                │ ─────────────────────▶ │ Cursor Cloud Agent × ≤8  │
│  · 按 DAG 发任务 prompt │                        │  各自干净 VM · clone 仓库 │
│  · 轮询状态 / follow-up │ ◀───────────────────── │  改代码 · 跑测试 · 开 PR  │
│  · 合并 PR、推进下一波  │    PR + statusChange   └──────────────────────────┘
│  · 本地跑 e2e（真 agy） │    POST /api/... thread ┌──────────────────────────┐
└─────────────────────────┘ ─────────────────────▶ │ Hoplite thread（review    │
                                                   │  loop / 第二意见）        │
                                                   └──────────────────────────┘
```

关键原则（来自实测教训）：
1. **每个任务 = 一个 agent = 一个分支 = 一个 PR**；分支由平台管理（`cursor/*`、`hoplite/*`）。
2. **Cursor `autoCreatePr` 不稳定**（实测同批一个开出 PR、一个 `pr:null`）→ 任务 prompt 里明确要求 agent 用 `gh pr create` 确定性开 PR，编排侧仍要兜底检查。
3. **开发者模型 ≠ review 模型**：每个 PR 由另一个模型的 agent 做 review（交叉审查）。
4. **真凭证最小化**：Antigravity token / 订阅凭证**绝不**进入 Cursor/Hoplite 的 VM；云端 agent 只拿「CI 专用 GCP 项目」的受限凭证。

---

## 1. 仓库准备（GitHub, org: `soren-labs`）

### 1.1 主仓库 `soren-labs/agy-cloud`（private）

```bash
gh repo create soren-labs/agy-cloud --private --clone
```

首个 commit 必须包含（这是云端 agent 的全部上下文来源，务必开工前就位）：

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `docs/DESIGN.md` | design.md 原文 | 每个任务 prompt 都引用它的章节号 |
| `docs/PREP.md` | 本文档 | DAG、任务分配、验收标准 |
| `docs/CONTRACTS.md` | **T0 任务产出**（见 §6） | `/v1` + `/internal` 的 OpenAPI、Firestore schema、runner↔控制面协议。并行开发不冲突的关键 |
| `AGENTS.md` | agent 行为守则 | 分支/commit 规范、"必须跑 `make test` 且贴结果进 PR"、"不改 CONTRACTS.md，有异议开 issue"、"PR ≤ 600 行" |
| 目录骨架 | `api/ worker/ infra/ cli/ tests/` + 空 `__init__.py` | 按 design.md 附录 B，避免 agent 各自发明布局 |
| `pyproject.toml` + `Makefile` | `make lint / test / itest / e2e-local` | 统一入口；ruff + pytest |
| `.cursor/environment.json` | `{"snapshot":..., "install":"pip install -e .[dev]", "terminals":[]}` | Cursor VM 环境初始化 |
| `tests/fake_agy/` | **agy 桩程序**（见 §4.3） | 云端 agent 没有真 agy，全靠它 |
| `.github/workflows/ci.yml` | PR: lint + unit；label `run-itest`: 集成测试 | 见 §5 |
| `.github/PULL_REQUEST_TEMPLATE.md` | 任务 ID、对应 DESIGN 章节、测试结果、自查清单 | |
| `CODEOWNERS` | `* @zheng`（你本人最终合并） | |

分支保护 `main`：required check = `ci/lint+unit`；禁止 force-push；PR 必须 1 approval（review agent 的 approval 计数，最终由你合并）。

```bash
gh api -X PUT repos/soren-labs/agy-cloud/branches/main/protection \
  -f 'required_status_checks[strict]=true' -f 'required_status_checks[contexts][]=ci' \
  -f 'enforce_admins=false' -f 'required_pull_request_reviews[required_approving_review_count]=1' \
  -F 'restrictions=null'
```

### 1.2 测试目标仓库 `soren-labs/agy-cloud-smoke`（private）

作用：agy-cloud 自身 e2e 的"被操作仓库"（对标已验证的 `cursor-cloud-smoke-20260907`，可直接以它为模板）：

```bash
gh repo create soren-labs/agy-cloud-smoke --private \
  --template soren-labs/cursor-cloud-smoke-20260907   # 若模板不可用则手工放入下述内容
```

内容：`number_utils.py` / `string_utils.py` + `tests/`（unittest 可发现）、`.agy/environment.json`（design.md §6.5 的格式，`test: python3 -m unittest discover -s tests -v`）、`AGENTS.md`。
**e2e 断言会检查该仓库上出现 `agy/*` 分支与 PR**，所以它必须允许 agy-cloud 的 GitHub App 安装（§1.4）。

### 1.3 授权给 Cursor 与 Hoplite

- **Cursor**：cursor.com → Settings → GitHub → 为 Cursor GitHub App 追加 `agy-cloud`、`agy-cloud-smoke` 两个仓库。验证：`curl -H "Authorization: Bearer $CURSOR_API_KEY" https://api.cursor.com/v0/repositories` 应列出两仓库。
- **Hoplite**：⚠️ 实测 `project_limit_exceeded`（套餐上限 2 个 project，已被 `minimax-h3-colab`、`novahub` 占满）。**开工前必须二选一**：删除/归档一个旧 project，或升级套餐。然后建 project `agy-cloud` 并在其 `repos` 中加入两个仓库。验证：`curl -H "X-Api-Key: $HOPLITE_API_KEY" https://api.hoplite.sh/api/projects`。

### 1.4 agy-cloud 自己的 GitHub App（供 e2e 用）

design.md §6.1 的 App（`agy-cloud-agent`）是**被测系统的一部分**，但注册动作是人工前置：
- 按 manifest flow 注册私有 App（权限：Contents/PR/Issues/Checks RW, Metadata R），先只安装到 `agy-cloud-smoke`。
- 产出三件套：`app_id`、`private-key.pem`、`webhook_secret` → 存入 **GCP Secret Manager**（§2.4），**不要**放进 Cursor/Hoplite secrets。
- Webhook URL 先留空/占位，P1 部署 Cloud Run 后由 `deploy.sh` 回填。

---

## 2. GCP 准备

### 2.1 两个项目（隔离爆炸半径）

| 项目 | 用途 | 谁能访问 |
| --- | --- | --- |
| `agy-cloud-prod` | 你本人部署/运行真系统 + 本地 e2e | 只有你（gcloud 用户凭证） |
| `agy-cloud-ci` | 云端 agent 的集成/端到端测试专用 | Cursor/Hoplite agents（受限 SA key）+ GitHub Actions（WIF） |

> 理由：SA key 会进入第三方（Cursor/Hoplite）的 VM，必须假设可能泄露。`agy-cloud-ci` 里没有任何生产数据、没有 Antigravity token、有预算硬顶，泄露损失可控。

```bash
for P in agy-cloud-prod agy-cloud-ci; do
  gcloud projects create $P --name=$P
  gcloud billing projects link $P --billing-account=<BILLING_ID>
  gcloud services enable compute.googleapis.com run.googleapis.com firestore.googleapis.com \
    secretmanager.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com iam.googleapis.com iamcredentials.googleapis.com --project=$P
done
```

### 2.2 基础资源（每个项目各一份）

```bash
P=agy-cloud-ci   # prod 同理
gcloud firestore databases create --location=nam5 --project=$P
gsutil mb -l us-central1 -p $P gs://agy-cloud-$P
# 预算告警：ci 项目 $20/月 硬提醒（50/90/100%），prod $30/月
gcloud billing budgets create --billing-account=<BILLING_ID> --display-name=$P-budget \
  --budget-amount=20 --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
# 配额检查（决定 e2e 并发；design.md §10）
gcloud compute regions describe us-central1 --project=$P --format='value(quotas.filter("metric:CPUS"))'
```

记录 vCPU 配额到 `docs/PREP.md` 附注（新项目常见 8–24；不足 8 就为 `agy-cloud-prod` 提额）。

### 2.3 服务账号与角色

**运行时 SA（两个项目都建，deploy.sh 也会幂等创建；design.md §9 步骤 3）**：

| SA | 角色 |
| --- | --- |
| `agy-control` | `compute.instanceAdmin.v1`（IAM condition: `resource.name startsWith "…/instances/agy-"`）、`iam.serviceAccountUser`(on agy-worker)、`datastore.user`、bucket `storage.objectAdmin`、`secretmanager.secretAccessor`、`logging.logWriter` |
| `agy-worker` | 无项目级角色；仅 `run.invoker` on `agy-api` |
| `agy-sched` | `run.invoker` on `agy-api` |

**CI 测试 SA（仅 `agy-cloud-ci`）** —— 这是要配进 Cursor/Hoplite 的那把 key：

```bash
P=agy-cloud-ci
gcloud iam service-accounts create agy-ci --project=$P
for R in roles/datastore.user roles/storage.objectAdmin roles/secretmanager.secretAccessor \
         roles/compute.instanceAdmin.v1 roles/iam.serviceAccountUser roles/run.developer; do
  gcloud projects add-iam-policy-binding $P --member=serviceAccount:agy-ci@$P.iam.gserviceaccount.com --role=$R
done
gcloud iam service-accounts keys create agy-ci-key.json --iam-account=agy-ci@$P.iam.gserviceaccount.com
base64 -w0 agy-ci-key.json > agy-ci-key.b64   # 配完 §3 后 shred 本地文件
```

> `compute.instanceAdmin.v1` 给 CI SA 是为了让云端 agent 能跑"真建 VM"的集成测试；若你想更保守，先不给这个角色，`itest` 全部走 mock，仅本地 e2e 建真 VM（§4.2 的分层已按此兼容设计）。

**GitHub Actions**：用 Workload Identity Federation（免 key）：

```bash
gcloud iam workload-identity-pools create gh --location=global --project=$P
gcloud iam workload-identity-pools providers create-oidc github --location=global \
  --workload-identity-pool=gh --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping='google.subject=assertion.sub,attribute.repository=assertion.repository' \
  --attribute-condition="assertion.repository=='soren-labs/agy-cloud'" --project=$P
gcloud iam service-accounts add-iam-policy-binding agy-ci@$P.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/<PROJECT_NUM>/locations/global/workloadIdentityPools/gh/attribute.repository/soren-labs/agy-cloud"
```

### 2.4 Secret Manager 内容规划

| Secret（项目） | 内容 | 消费者 |
| --- | --- | --- |
| `github-app-id` / `github-app-key` / `github-webhook-secret`（prod） | agy-cloud-agent App 三件套 | agy-api |
| `agy-token-pro-1..N`（**仅 prod**） | 各 Antigravity 订阅的 `antigravity-oauth-token` | worker VM；**永不出 prod** |
| `github-app-*`（ci） | 一个**测试用** App（可复用同一 App 另装或注册第二个 `agy-cloud-agent-ci`），装到 smoke 仓库 | itest/e2e |
| `e2e-fake-agy-marker`（ci） | 任意串 | 供集成测试验证 secret 读写路径 |

Antigravity 账号登录（人工，每订阅一次，在有浏览器的机器上）：`agy` 登录 → 把 `~/.gemini/antigravity-cli/antigravity-oauth-token` 写入 prod 的 `agy-token-pro-N`。开工前至少准备 2 个账号（并发实验需要）。

---

## 3. 凭证配置到 Cursor / Hoplite

### 3.1 Cursor Cloud Agents secrets（dashboard → Cloud Agents → Secrets，KMS 加密）

| Secret 名 | 值 |
| --- | --- |
| `GCP_SA_KEY_B64` | `agy-ci-key.b64` 内容 |
| `GCP_PROJECT` | `agy-cloud-ci` |
| `AGY_GCS_BUCKET` | `agy-cloud-agy-cloud-ci` |
| `AGY_TEST_GH_REPO` | `soren-labs/agy-cloud-smoke` |

`.cursor/environment.json` 的 `install` 里加激活逻辑（无凭证时自动降级为 unit-only，保证任何 agent 都能跑基本测试）：

```bash
if [ -n "$GCP_SA_KEY_B64" ]; then
  echo "$GCP_SA_KEY_B64" | base64 -d > /tmp/gcp-key.json
  export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json
  gcloud auth activate-service-account --key-file=/tmp/gcp-key.json 2>/dev/null || true
fi
```

### 3.2 Hoplite（project `agy-cloud` → settings → secrets/env）

同样四个变量。Hoplite 侧主要用途是 **review thread 与 PR review loop**，一般不需要真打 GCP，但配置同一套保证它也能跑 `make itest` 复核。

### 3.3 明确不配置的

- ❌ Antigravity token（订阅凭证，风险最高，只活在 prod Secret Manager + 你本机）
- ❌ `agy` 二进制（不进云端 agent；一切靠 fake_agy）
- ❌ prod 项目的任何 key
- ❌ 你的个人 GitHub PAT（Cursor/Hoplite 自带 GitHub App 通道；e2e 用的 App key 在 GCP Secret Manager，agent 通过 agy-ci SA 读取）

### 3.4 配置后验证（本地一次性）

发一个一次性 Cursor agent，prompt："运行 `make verify-creds` 并把输出贴到 PR 描述"。`make verify-creds` 内容：`gcloud auth list`、`gcloud firestore databases list`、`gsutil ls gs://$AGY_GCS_BUCKET`、读一个 ci secret。三平台（Cursor、Hoplite、GitHub Actions）各验一次，全绿才开工。

---

## 4. 测试分层与桩件

### 4.1 分层

| 层 | 命令 | 依赖 | 跑在哪 |
| --- | --- | --- | --- |
| L0 lint+unit | `make lint test` | 无凭证、无网络 | 每个 PR，CI + agent 自查 |
| L1 集成 | `make itest` | fake_agy + Firestore/GCS/SecretManager（真 ci 项目或 emulator）+ mock GCE | agent VM 内 + CI（label `run-itest`） |
| L2 e2e-local | `make e2e-local` | docker backend（design.md §9"本地开发模式"）+ fake_agy | agent VM 内（有 docker）+ 本地 |
| L3 e2e-gcp | `tests/e2e_gcp.sh` 冒烟六项（design.md P1 验收①–⑥） | 真 VM + 真 agy token | **仅你本地**（WSL→prod），手动触发 |
| L3' e2e-gcp-fake | 同上但 worker 镜像内用 fake_agy | 真 VM，无 agy token | 可选：agent 用 ci 项目跑（若给了 compute 角色） |

### 4.2 关键设计：`AGY_BIN` 可注入

runner.py / worker.sh 中 agy 调用路径必须从环境变量 `AGY_BIN`（默认 `agy`）读取——这是让 L1/L2/L3' 全部无订阅凭证可跑的开关。写进 CONTRACTS.md。

### 4.3 `tests/fake_agy/`（T0 波次就要产出）

一个 ~150 行 Python 脚本，完整模拟已验证的 agy 无头行为（design.md §1.1 / 附录 A）：
- 接受 `--print= --output-format json|stream-json --model --conversation --add-dir --json-schema --print-timeout --dangerously-skip-permissions`
- stream-json 时输出 `init → N×step_update → result`；json 时输出含 `conversation_id/status/response/usage` 的对象
- 按 prompt 中的魔法指令行动：`FAKE:write <file> <content>`、`FAKE:fail`、`FAKE:quota`（打印 RESOURCE_EXHAUSTED 到 stderr，测冷却逻辑）、`FAKE:sleep N`（测超时/心跳）
- 维护假会话状态目录（`brain/<id>/` 等四件套），使**快照打包/恢复逻辑可以被真实测试**

---

## 5. CI（`.github/workflows/ci.yml`）

```yaml
on: { pull_request: {}, workflow_dispatch: {} }
jobs:
  lint-unit:            # 每个 PR，required check
    steps: [checkout, setup-python, make lint, make test]
  itest:                # PR 带 label run-itest 时；WIF → agy-ci@agy-cloud-ci
    if: contains(github.event.pull_request.labels.*.name, 'run-itest')
    permissions: { id-token: write, contents: read }
    steps: [checkout, google-github-actions/auth(WIF), make itest]
  e2e-local:            # workflow_dispatch，docker backend + fake_agy
    steps: [checkout, make e2e-local]
```

AGENTS.md 中要求：agent 完成开发后给自己的 PR 加 `run-itest` label（或在 PR 里说明为何不需要）。

---

## 6. DAG 与任务分配

### 6.1 模型角色

| 模型 | 定位 | 理由 |
| --- | --- | --- |
| **glm-5.3** | 主力开发：控制面/runner 等重逻辑 | 最强编码 |
| **grok-4.6** | ① 全部 PR 的 review agent ② 安全/并发敏感模块的开发 | 挑错敏锐，用于交叉审查 |
| **deepseek-v4-flash** | 测试编写、fake_agy、CLI、infra 脚本、文档、CI 修复 follow-up | 快且便宜，适合大量轻任务 |

规则：**每个任务 dev 模型 ≠ review 模型**；grok-4.6 开发的模块由 glm-5.3 review。review 统一走 Hoplite thread 或 Cursor review-mode agent，prompt 要求输出 `verdict + findings[]` 并作为 PR review 提交。

> 开工前验证：`GET api.cursor.com/v0/models` 确认三个模型 id 的确切写法，记入 CONTRACTS.md 顶部。

### 6.2 DAG（W0 人工完成 = 本文档 §1–§5；W1 起全部云端并发）

```
W0 (人工): 仓库+GCP+secrets+验证 ──▶ T0

T0  CONTRACTS.md + 骨架 + fake_agy + Makefile/CI   (单线程，必须最先合并)
     │
     ├──────────────┬───────────────┬───────────────┬──────────────┐
     ▼              ▼               ▼               ▼              ▼
W1  T1 infra      T2 api核心      T3 worker容器层  T4 agyctl      T5 GitHub App服务
    deploy/iam    models/auth/    Dockerfile.agent  cli骨架        token铸造/PR/
    image-build   firestore/      settings/iptables (对CONTRACTS   CheckRun/评论
    (脚本,可先    storage/secrets  (移植worker.sh)   编程)          (github_app.py)
     不执行)       服务层
     │              │               │               │              │
     │        ┌─────┴─────┐         │               │              │
     ▼        ▼           ▼         ▼               │              │
W2  T6 gce.py T7 /v1路由  T8 /internal+调度器+reaper T9 runner.py  │
    (dep T1,T2) agents/runs/ (dep T2; §3.5事务,§附录C) (dep T3,T0契约;│
              repos/models/  含账号池§5              §4.3主循环)   │
              webhook投递                                          │
     └────────────┴───────────┬───────────────┴───────────────────┘
                              ▼
W3  T10 docker backend + e2e_local.sh   T11 /webhooks/github + @agy触发 (dep T5,T7,T8)
    (dep T7,T8,T9; 把整条链在本机容器里跑通)
                              │
                              ▼
W4  T12 e2e_gcp.sh 冒烟六项脚本 (dep 全部)   T13 文档/agyctl补全/MCP (dep T4,T7)
                              │
                              ▼
W5  (本地,人工+真凭证): deploy.sh 到 prod → 跑 T12 → 验收 design.md P1 ①–⑥
```

### 6.3 任务表

| ID | 交付物（对应 DESIGN 章节） | dev | review | tests by | 平台 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| T0 | CONTRACTS.md、目录骨架、fake_agy、Makefile、ci.yml（§7、§8、附录B） | glm-5.3 | grok-4.6 **+ 你本人** | deepseek | Cursor | fake_agy 两种 output-format 可跑；CI 绿 |
| T1 | `infra/{deploy,iam,scheduler,image-build}.sh`（§9、§4.1） | deepseek | grok-4.6 | deepseek | Cursor | shellcheck 过；`--dry-run` 模式打印全部命令 |
| T2 | `api/models.py auth.py services/{firestore,storage,secrets}.py`（§8） | glm-5.3 | grok-4.6 | deepseek | Cursor | unit 覆盖 schema/auth；itest 读写真 Firestore(ci) |
| T3 | `worker/Dockerfile.agent settings.template.json iptables.sh`（§4.4） | grok-4.6 | glm-5.3 | deepseek | Cursor | 容器内 fake_agy 跑通；iptables 规则单测（netns） |
| T4 | `cli/agyctl.py` 骨架（§7.5） | deepseek | glm-5.3 | deepseek | Cursor | 对 mock server 全命令冒烟 |
| T5 | `api/services/github_app.py`（§6.1–6.2） | glm-5.3 | grok-4.6 | deepseek | Cursor | itest：对 smoke 仓库真铸 token、开/关测试 PR |
| T6 | `api/services/gce.py`（§4.2） | grok-4.6 | glm-5.3 | deepseek | Cursor | mock 单测 + （可选)ci 项目真建/删一台 VM |
| T7 | `api/routes/{agents,runs,repos,models,accounts}.py` + webhook 投递（§7） | glm-5.3 | grok-4.6 | deepseek | Cursor | OpenAPI 与 CONTRACTS 一致性测试 |
| T8 | `/internal/*`、scheduler、reaper、账号池（§3.5、§5、附录C） | glm-5.3 | **grok-4.6 重点审并发/事务** | deepseek | Cursor | follow-up 竞态的属性测试（并发 enqueue/release 千次无丢单） |
| T9 | `worker/{startup.sh,runner.py}`（§4.3） | grok-4.6 | glm-5.3 | deepseek | Cursor | e2e-local 内完整 turn 循环 + 快照上传/恢复（fake_agy） |
| T10 | docker backend + `tests/e2e_local.sh`（§9） | glm-5.3 | grok-4.6 | glm-5.3 | Cursor | 一条命令本机跑通 create→PR→followup→finished |
| T11 | `/webhooks/github`、`@agy` 触发、autoFix（§6.3–6.4） | glm-5.3 | grok-4.6 | deepseek | Cursor | 重放录制的 webhook 样本；白名单/防循环单测 |
| T12 | `tests/e2e_gcp.sh` 冒烟六项（P1 验收） | deepseek | glm-5.3 | — | Cursor | 脚本评审通过；执行在 W5 本地 |
| T13 | agyctl 补全 + `mcp_server.py` + README（§7.5） | deepseek | glm-5.3 | deepseek | Cursor | — |
| R* | 每个 PR 的交叉 review thread | —(见 dev 行) | 表中 review 列 | — | **Hoplite** | verdict=approve 或 findings 全部处理 |

并发预算：Cursor Pro ≈ 8 slots。W1 五个任务 + 2–3 个 review thread 正好吃满；deepseek 的测试补充任务用 follow-up 打进原 agent 而非新开。

### 6.4 任务 prompt 模板（编排时逐个填充）

```
任务 T<n>: <标题>
阅读 docs/DESIGN.md §<章节> 与 docs/CONTRACTS.md §<章节>，实现 <交付物路径>。
约束: 不修改 CONTRACTS.md 与其他任务的目录; agy 调用一律走 $AGY_BIN; 新代码必须有测试。
完成标准: make lint test 通过; <任务专属验收>。
收尾: 用 gh pr create 开 PR（不要依赖 autoCreatePr）, 标题 "T<n>: <标题>",
     正文含: 实现摘要 / 测试输出 / 与 DESIGN 的偏差声明; 加 label run-itest。
```

---

## 7. 开工前 checklist（全绿才发 W1）

- [ ] `agy-cloud` 仓库建好，T0 前置文件（DESIGN/PREP/AGENTS/骨架/模板）已 push，main 分支保护生效
- [ ] `agy-cloud-smoke` 建好，含 `.agy/environment.json` 与可跑的 unittest
- [ ] Cursor GitHub App 已授权两仓库（`/v0/repositories` 可见）
- [ ] Hoplite project 槽位腾出，project `agy-cloud` 建好并绑仓库
- [ ] `GET /v0/models` 确认 glm-5.3 / grok-4.6 / deepseek-v4-flash 的准确模型 id
- [ ] GCP `agy-cloud-prod` / `agy-cloud-ci` 建好：API、Firestore、GCS、预算告警
- [ ] `agy-ci` SA + key 生成；vCPU 配额已记录（不足则已提额）
- [ ] Secrets 配入 Cursor（4 项）与 Hoplite（4 项）；本地 key 文件已 shred
- [ ] GitHub Actions WIF 配好
- [ ] GitHub App `agy-cloud-agent`（prod）+ 测试 App（ci）注册，三件套入 Secret Manager
- [ ] ≥2 个 Antigravity 专用账号已登录，token 存入 prod `agy-token-pro-*`
- [ ] `make verify-creds` 在 Cursor / Hoplite / Actions 三处各验证一次全绿
- [ ] T0 由 glm-5.3 完成、grok-4.6 review、你本人终审合并 → 发 W1 五个并发任务

---

## 8. 风险提示（沿用 design.md §11，前置阶段特有的两条）

1. **ci SA key 进入第三方 VM**：已通过独立 ci 项目 + 最小角色 + 预算告警控制；每月轮换 key（`gcloud iam service-accounts keys create/delete`）。
2. **agent 互相踩目录**：靠 T0 的 CONTRACTS.md + AGENTS.md「只改本任务目录」约束 + 你合并时把关；波次内任务的文件集合已设计为两两不相交。
