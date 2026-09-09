# 第一版任务与依赖

以 V1-SCOPE.md 为范围，不直接执行旧 PREP 的全部 W1–W4。保留原任务编号便于对照。

## 启动顺序

1. 建立最小仓库，放用户DESIGN、PREP、本次范围文件、AGENTS与PR模板；P0代码作为reference，不作为生产worker入口。
2. 授权开发平台访问仓库，核实模型ID；Hoplite不可用时可使用Cursor独立review agent，不让产品实现依赖某个review平台。
3. T0先合并：接口/状态/存储适配契约、fake_agy、测试入口、CI以及verify-creds入口。云凭证验证此时才具备可执行命令。
4. 并行准备CI资源和授权；无凭证开发继续，真实集成测试在凭证验证通过后启用。
5. 依赖完成且PR审查通过后推进下一批，最后部署prod并运行真实验收。

## 任务表

| ID | 所有权/交付 | 依赖 | 验收边界 |
|---|---|---|---|
| T0 | docs/CONTRACTS、骨架、fake_agy、Makefile、CI、测试公共设施与适配接口 | 最小仓库 | P0真实输出形状一致；fake两种格式；无凭证lint/unit；所有状态和幂等规则定清 |
| T1 | infra/部署、IAM、Scheduler、镜像构建 | T0；最终镜像集成等T3/T9 | dry-run、权限矩阵、标准VM超时预检；构建/部署版本固定 |
| T2 | api/models/auth、存储服务及本地适配 | T0 | GCP/本地共用repository接口；内部身份及session越权负向测试 |
| T3 | worker容器定义、settings、metadata阻断；准备/测试容器 | T0 | fake_agy跑通；GitHub/GCP/token泄漏负向测试；资源上限生效 |
| T4 | cli/agyctl第一版命令与配置 | T0 | mock API全命令；明确未支持字段 |
| T5 | api/services/github_app.py | T0 | 测试App token、幂等PR、Check Run、长session token刷新；真实写操作只在指定测试仓库 |
| T6 | api/services/gce.py | T1/T2契约 | session命名和幂等创建；删除含磁盘；mock故障覆盖；CI真VM验证按权限启用 |
| T7 | /v1路由及出站webhook投递 | T2/T5；最终闭环等T6/T8 | 契约一致、幂等launch/followup、持久化投递和重试 |
| T8 | /internal、调度、reaper、账号池 | T2；最终集成等T6 | 并发claim/release、过期租约、迟到回报、token版本冲突、按session回收 |
| T9 | worker/startup.sh、runner.py | T0/T3/T5契约 | fake驱动模块测试：嵌套事件、快照、测试失败、WIP、有限重试；不提前要求T10完整e2e |
| T10 | Docker session backend、tests/e2e_local.sh | T2/T6接口/T7/T8/T9 | 本地无云凭证完整create→PR模拟→followup→finished；两种新旧session续聊路径 |
| T12 | tests/e2e_gcp.sh、故障注入与证据采集 | T1/T5/T6/T7/T8/T9/T10 | 真实A1–A6；故障注入只操作本次测试标记资源；清理可重复执行 |
| T13a | CLI与README收尾 | T4/T7/T10 | 第一版所有命令可操作、部署及失败恢复步骤完整 |

T11（GitHub入站触发）和T13中的MCP移到P2。code以外的review/plan同样移到P2。

原DAG的“所有任务目录两两不相交”不成立：T7/T8都会触及路由装配，T2/T10共用本地适配接口，T1/T3/T9共同影响镜像。T0明确公共文件所有权；每批开始前指定集成修改负责人。需要修改CONTRACTS时先提出契约变更并暂停受影响任务，不让每个agent私自改接口。

## 开发与审查规则

- 单任务单分支单PR；开发模型和审查模型不同。优先沿用PREP角色，但必须以平台实际可用模型为准。
- reviewer提交正式审查；COMMENTED不能计作APPROVED。GitHub审批身份必须满足实际仓库规则，自己审自己不算完成。
- 不把某一份测试日志等同于全部验收。PR列明unit、mock、CI集成、真实云验证各自结果或尚未执行原因。
- 必需检查名统一为实际job `lint-unit`，配置分支保护前用一次真实CI确认显示名。
- label驱动itest必须监听pull_request的labeled事件；workflow_dispatch明确选择itest/e2e-local，不依赖只存在于PR事件的字段。
- T0完成后，Cursor/Hoplite（若启用）/Actions分别执行verify-creds；只验证CI身份和指定测试secret，不打印secret值。
- 不设置“PR超过600行必须偷拆成无意义提交”；目标保持可审查，较大契约/生成代码注明例外及负责人。

## 发布门禁

不是所有PR合并即发布。发布需要：离线/集成测试通过、A1–A6真实证据齐全、无遗留测试VM/磁盘、已固定镜像版本、已记录实际账号和配额容量。预算/耗时只记录实测，旧DESIGN的行数和天数不作为交付承诺。
