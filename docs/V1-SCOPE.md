# agy-cloud 第一版范围定稿

日期：2026-09-09。状态：当前开发基线，尚未实现。根据用户要求收敛 DESIGN 与 PREP；本文件的取舍用于后续任务拆分。账户、预算和资源授权仍以实际配置为准，不隐含创建或购买资源。

## 1. 第一版目标

单用户通过 API / CLI，向已授权 GitHub 仓库发出代码任务，系统自动创建临时 GCP VM、运行真实 agy、执行测试、产生 PR、保存会话并回收机器；任务运行中和结束后均可追加指令。

代码和对话持久化，VM 临时化。每个 agent 固定一个分支、PR 和账号；每条指令为一个 run；每台 VM 为一个 session，可串行执行多个 run。同一 agent 不能同时有两个有效 session 执行。

首个成功标准是单账号单并发完成整个流程；第一版发布标准仍保留原 DESIGN 的五并发验收，不降低成只演示一个任务。

## 2. 纳入第一版

| 领域 | 必须交付 |
|---|---|
| API 与 CLI | API key 鉴权、创建/列表/详情/对话/runs/日志、followup、stop、archive/unarchive、repos/models、账号导入和列表 |
| 执行 | code 模式；固定版本 agy；AGY_BIN 可替换；30 分钟 turn 上限；每轮独立 agy 容器 |
| GitHub | 产品 GitHub App；单仓库 token；宿主 clone/commit/push；确定性创建并复用 PR；发布测试 Check Run |
| 状态 | Firestore 元数据；GCS 快照、事件及日志；每轮完成后持久化；同账号新 VM 恢复 |
| 调度 | 有界队列、账号槽位、全局并发上限、幂等建 VM、心跳、带 session 身份的租约 |
| 生命周期 | 正常删除、超时兜底、reaper、取消、孤儿实例与磁盘清理、快照过期 |
| 账号管理 | 绑定账号；基本限额冷却、登录失效状态；刷新后 token 回写与版本冲突处理 |
| 结果交付 | 状态轮询、分页事件/日志；可选配置的出站状态 webhook，持久化重试 |
| 本地验证 | Docker backend + fake_agy + 本地状态/存储适配；无 GCP、无真实订阅可以跑完整测试 |
| 部署 | Cloud Run + GCE 脚本、镜像固定版本、CI、独立生产/测试项目 |

账号刷新和基本冷却虽然在原 DESIGN 的 P2 列表中出现，实际是无人值守执行的基础，明确提前到 P1；高级用量统计和自动账号切换仍不做。

## 3. 留到第二版或以后

- GitHub 入站 `/webhooks/github`：`@agy`、Issue 触发、review 评论聚合。
- CI 自动修复循环、自动合并（自动合并仍不在 MVP 范围）。
- 产品 review / plan 模式及 `/v1/reviews`；P0 review 保留为后续素材。开发期间交叉审查继续由 Cursor/Hoplite 负责。
- MCP server、SSE 长连接、图片输入、依赖缓存、自定义容器镜像。
- Spot 与抢占重排队、跨账号快照恢复、模型摘要式会话降级。
- 热等 keepWarmSeconds > 0、VPS 备用控制面、Web UI、CAO 接入、多租户和计费。
- 动态解析仓库 `.agy/environment.json`：第一版由 launch 请求显式给出 setup/test，仓库只提供示例配置。解析与依赖缓存一起后置。

API 形状参照原 DESIGN，不承诺完整兼容 Cursor。未支持字段明确报不支持，不能静默接受。

## 4. 固定的运行决策

| 项目 | 第一版取值 |
|---|---|
| 控制面 | Cloud Run，min=0；所有持续工作必须有请求、持久化队列或 Scheduler 驱动 |
| 区域 / 默认机型 | us-central1 / us-central1-a；e2-standard-2；部署前检查实际可用性和配额 |
| VM 模式 | 标准按需，Spot 关闭 |
| 并发 | 初次冒烟全局 1；最终五并发；账号并发初始 1，经同账号实测后才可提高到 2 |
| 账号容量 | 五并发需总槽位 >=5；若每号2槽需至少3个可用专用账号；也可5号各1槽。资源不足记录未通过，不跳过验收 |
| vCPU | 默认每 VM 2 vCPU，五并发需该机型适用的区域配额至少10 vCPU，并检查其他相关配额 |
| 会话亲和 | sticky 账号；限额后等待原账号；不跨账号自动迁移 |
| 空闲 | keepWarmSeconds=0；队列为空就保存并释放，不设置外部 IDLE 状态 |
| 超时 | turn=30 min；session=2 h；平台兜底目标2 h 15 min |
| 心跳 | 每30秒；租约120秒；从最后成功心跳起180秒内检测，假设控制面与每分钟tick正常 |
| 快照 | 同账号/同 agy 版本；最近3版；无活动30天后过期；版本不兼容明确报错并保留分支 |
| 模型 | runtime 可配置；首轮以当前账号实际能列出的模型为准，模型 ID 不作为产品硬编码 |

五并发是能力验收，不是默认长期运行设置。预算告警不等于硬性费用上限；通过并发、每轮/session 超时和回收减少失控时间，不承诺固定月费。

## 5. T0 必须定清的契约

### 5.1 状态与幂等

- agent 状态：QUEUED、CREATING、RUNNING、FINISHED、ERROR、CANCELLED、EXPIRED、ARCHIVED。run 状态独立：QUEUED、RUNNING、SUCCEEDED、FAILED、CANCELLED。
- seq 在事务中递增；launch/followup 支持幂等请求标识；HTTP 重试不能重复入队。每个 session 有单调递增 generation/fencing 值。
- 创建 VM 前先事务预约 session 和账号槽位；VM 名从 session ID 确定，失败重试须查询同名资源。不能仅凭“lease不存在”就同时创建多个 VM。
- pop、claim、renew、finish、release 校验 sessionId + generation；旧 VM 的迟到回报不能修改新 session 或扣减其槽位。
- 先上传不可变的 run 快照与 manifest，再提交元数据，再原子检查队列并释放。释放前入队由当前 session 接；释放后入队由新 session 接。
- VM 以 session 为归属；reaper 不得因为第一条 run 已完成而删除正在执行 followup 的 VM。
- 外部 GitHub 副作用用分支、head SHA、run ID 查询去重。不声称跨 GitHub/GCS/Firestore 存在全局 exactly-once 事务。
- archive 仅作用于无活动任务；活动任务先 stop。queued run 的取消、当前 run 的取消分别记录。失败后不盲目自动重放；用户可在保留快照上追加新的 run。

### 5.2 测试与输出

- stream-json 按 `event` 路由，成功结果读取嵌套 `result`；支持错误退出、无结果、坏JSON、截断输出、取消和超时。
- 模型 SUCCESS 不能覆盖测试失败。测试失败保存 WIP 与快照，可创建 draft PR，run=FAILED/tests_failed，Check Run=failure，不能显示为成功任务。
- 无代码变更时可完成 run，但 prUrl=null 并记录 no_changes；不能为了“必有PR”创建空提交。
- 大任务正文放只读 TASK.md，命令行传短引导和文件路径；禁止把全文通过 shell 展开进 --print。版本固定且升级要跑同版本/旧快照回归。
- setup 在无凭证准备容器中运行；测试在无 agy/GitHub/GCP 凭证的独立容器执行。测试使用工作区副本，可写临时目录，测试结果不得修改宿主用于提交的原工作区。

### 5.3 隔离、权限和凭证

- GitHub token、GCS签名URL只在宿主runner；不通过git remote、credential文件、HOME挂载或环境变量泄漏给agy容器。
- agent容器只得到工作区、任务文件、该任务所需的agy状态和token；不挂宿主Docker socket；非root、cap-drop、资源限制、metadata阻断。
- 同一个公开Cloud Run服务的 `/internal` 由应用严格校验Google ID token的签名、issuer、audience、有效期和允许的SA身份。不能假设服务级run.invoker自动提供路径级保护。worker只能操作其绑定session；Scheduler只能tick，API key不能访问内部凭证接口。
- 共享worker SA身份本身不能区分不同VM；每个session增加一次性启动绑定/随机能力凭证，宿主保存并与generation一起校验。禁止任意worker遍历其他run的spec和token。
- 控制面需要指定secret读取和新增版本能力；账号token回写需带基准版本和账号级串行化，旧session不能覆盖新凭证。CI与prod使用独立测试App和secret。
- 用服务账号远程签发GCS URL时补齐签名权限、签名主体和对象权限；不只配置storage.objectAdmin就认为已完成。
- GitHub installation token在长session中需按有效期刷新；超过一次token有效期的任务必须有集成测试。
- 保留P0产物但不将token、私人会话数据库或整个state目录提交给云端开发平台。

权限细节依据官方文档核对；这些是实施约束，仍需T1权限正反向验证：[Cloud Run服务间认证](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)、[GCS签名URL](https://docs.cloud.google.com/storage/docs/access-control/signing-urls-with-helpers)、[新增Secret版本](https://docs.cloud.google.com/secret-manager/docs/add-secret-version)。

### 5.4 控制面故障的明确规则

- 验收注入仅阻断worker到控制API的通信，GCS/GitHub保持可达；不把“整个网络断掉还能上传快照”作为保证。
- spec提前给本session有效期充足、仅限本任务对象的GCS上传URL；每run持久化快照、测试结果和completion manifest。有效期覆盖session上限及收尾窗口。
- 心跳续约失败后不领取新run；到本地租约截止时停止当前agy进程，限时保存WIP/快照。为避免旧VM和新VM同时写分支，未重新确认租约时不push；将本地commit打包为恢复材料写GCS，报告明确的未推送状态。
- 最终回报重试累计最多60秒，失败后本机poweroff；停止和收尾总预算120秒。原设计的30分钟重试取消。
- 控制面恢复后reaper先读取manifest，对失败/中断状态和恢复材料做幂等核对，再清理VM/磁盘、释放槽位。不能仅凭心跳缺失将已成功并完整持久化的run覆盖成失败。
- GCS不可达时不能保证最新快照；报告持久化失败、保留最近已确认版本，仍由平台硬超时限制VM寿命。
- GCE标准VM的超时DELETE须在部署预检验证；若只支持STOP，则使用STOP+reaper，并明确控制面恢复前磁盘仍可能收费。

## 6. 发布验收：六项一个都不能隐去

| 编号 | 场景 | 必须保存的断言与证据 |
|---|---|---|
| A1 | 正常任务 | 真实模型修改指定smoke仓库，测试通过，PR+Check Run存在，快照manifest存在；完成回报后180秒内VM及自动删除磁盘清理 |
| A2 | FINISHED后followup | 旧VM已删除，新session和VM ID不同；conversation、账号、分支和PR不变；新run增加commit，明确引用上一轮上下文 |
| A3 | RUNNING中followup | 在第一轮运行时提交追加指令，两个run的session相同且按seq串行；release边界竞态另用确定性测试覆盖 |
| A4 | 五agent并发 | 五个独立agent/VM在采样时同时执行，时间区间重叠；账号槽位不超限，全部完成并清理；仅同时处于QUEUED不算并发 |
| A5 | 运行中删除VM | 控制面正常；从最后成功心跳起180秒内run失败/lost_lease、槽位释放；无孤儿磁盘，后续任务可调度；不会自动重放可能有副作用的任务 |
| A6 | 控制API中断5分钟 | GCS仍可达；Worker在失去租约后停止或在此前正常完成，快照/结果材料可查，收尾预算内poweroff；恢复API后180秒内最终状态核对和VM/磁盘清理；遗留有效快照可供后续恢复 |

A6允许任务按失租约规则中断，并要求明确状态；它验证故障收尾，不保证控制面失联后仍完成全部编程任务。与原文“断网后照常完成”含糊表述相比，这是明确的第一版安全边界。

除六项外，发布必需门禁：无凭证unit、模拟本地闭环、快照打包恢复、错误/超时/取消、重复请求/重复回报、并发claim/release、容器凭证隔离及内部接口越权测试。模拟GCP通过不能替代A1–A6真实prod验收；真实容量不足应报告受阻项。

## 7. 这次没有替用户决定的外部事项

本文件固定产品默认方案，但不购买订阅、不更改Hoplite套餐、不创建生产资源、不安装GitHub App、不提高账号并发或云配额。实际项目ID、计费账户、专用账号和生产凭证属于部署输入。T0和离线开发可先开展，最终真实验收必须等这些输入齐备。
