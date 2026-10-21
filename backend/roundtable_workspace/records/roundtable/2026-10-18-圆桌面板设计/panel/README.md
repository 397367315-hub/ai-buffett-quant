# 交易圆桌本地服务（panel/）

本机单用户的 A 股研究工作台后端：只监听 `127.0.0.1`，同源提供 `frontend/dist` 与 `/api/v1` 契约接口。
没有券商连接、没有自动下单、没有后台订阅或开机自启；行情只在你点「采集」时取一次。

**界面两套外壳**（`docs/implementation/decisions/ADR-004-restore-legacy-shell.md`）：
默认是上一版的五个页面（漏斗 / 席位 / 候选池 / 个人池 / 回测，读 `/api/dashboard` 这条老刷新管道），
加上新版的环境概览、案例研究两页（读 `/api/v1`）；新版其余功能（事前计划、模拟仓与日记、历史训练、方法验证、席位库、卡组）
在侧栏底部「实验」里（地址 `#/lab/...`）。两套样式按 `.legacy-shell` / `.v1-shell` 作用域化，互不影响。

> 路径约定：`WORK_ROOT` = `~/Desktop/交易圆桌_十三席_WORK`，`WB` = `WORK_ROOT/records/roundtable/2026-10-18-圆桌面板设计`。
> 下面的命令都在 `WORK_ROOT` 下执行。

## 1 依赖

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | ≥ 3.10（已测 3.10.12 与 3.14.8；Mac 上的 Homebrew `python@3.14` 可直接用） | 服务端只用标准库，不需要 pip 安装任何包 |
| SQLite | Python 自带（已测 3.37.2 / 3.53.1） | WAL 模式；**运行目录必须在本机磁盘**（网络盘/共享挂载/iCloud 同步目录不适合 SQLite） |
| Node.js | ≥ 18（已测 22） | 只在重建前端时需要；锁文件 `WB/frontend/package-lock.json`，用 `npm ci` |
| meoz CLI | `~/Downloads/meoz-2/scripts/meoz.py` | 只有「采集」（provider_api）用到；路径可用 `MEOZ_CLI` 覆盖。API Key 由 meoz 自己读取（环境变量 `MEOZ_API_KEY`，或 `meoz.py configure-key` 存在 `~/.meoz/config.json`）；本服务只把 `MEOZ_*`、代理、CA 证书这些环境变量原样交给 meoz 子进程，**从不读取或打印密钥** |
| 验收工具（可选） | PyYAML、jsonschema、Playwright 1.48 + Chromium | 只在跑验收 / 生成发布清单时需要，产品运行不依赖 |

## 2 启动与停止

**最简单：双击 `WORK_ROOT/打开交易圆桌.command`。** 它会找本机的 Python（3.10 以上，优先 Homebrew 3.14）→ 用
`~/交易圆桌_runtime` 作数据目录启动服务 → 服务起来后自动打开浏览器；8787 被别的程序占着就换 8788…8796；
工作台已经在运行就直接打开浏览器，不会起第二个。关掉那个终端窗口或按 Ctrl-C 就是干净停止。
macOS 第一次可能提示「无法打开」：右键 →「打开」→「打开」；也可以在终端里 `bash 打开交易圆桌.command`。
第一次运行时 macOS 可能问「终端想访问桌面文件夹」，要点「好」（代码在桌面上）。

手动方式：

```bash
# 前端产物（仓库里已有构建好的 dist；改了 frontend/src 才需要重建）
cd "$WB/frontend" && npm ci && npm run build && cd -

# 启动（默认 127.0.0.1:8787，运行目录默认 WORK_ROOT/runtime）
ROUNDTABLE_RUNTIME=~/交易圆桌_runtime python3 "$WB/panel/api.py"
#   --port 8790              换端口
#   --no-workers             只读调试：不执行后台任务
#   --dev-origin http://127.0.0.1:5173   配合 vite dev 服务器（只接受本机地址）

# 打开（只开浏览器而服务没起来，会显示「无法连接」）
open http://127.0.0.1:8787/

# 停止：前台运行时 Ctrl-C 或关掉终端窗口；后台运行时 kill -TERM <pid>
#   都是干净停止：不再接新请求 → 停工作线程 → 关闭数据库，退出码 0
#   用 nohup 放后台时，关终端窗口不会停（尊重 nohup），用 kill -TERM 停
```

第一次打开是空的：点顶部「采集最新快照」。按钮先按服务端时钟要「今天」——交易时段内得到盘中快照，收盘后得到当天日线；
今天休市 / 未开盘 / 还没发布时，这次请求已经让服务端从数据源取回交易日历，再改采服务端给出的最近完整交易日（状态条写明原因）。
数据源暂时连不上时，空页面有「用本机历史归档」按钮，先用只读归档里最近一个交易日看看（标「本机历史归档」，不冒充今天）。
详见 `docs/implementation/decisions/ADR-003-first-run-calendar-and-collect.md`。

启动时依次执行：迁移（已有库升级前先做一致性备份，迁移文件带 SHA-256 校验，被改过就拒绝启动）→
把上次进程中断的 running 任务标为 `failed / PROCESS_INTERRUPTED`（排队中的任务保留，由工作线程重新领取）→ 写入当前 13 席注册表 → 版本化冷却预设 →
关闭任何遗留的提醒观察会话 → 启动 4 个任务线程。演练实测：首页约 0.2 秒可用，SIGTERM 后约 0.1 秒内退出
（`docs/implementation/evidence/backup-restore-drill-20261021.json`）。

拒绝的启动方式：`--host 0.0.0.0` 等非回环地址（除非显式 `--allow-remote`，而远程部署另有身份网关验收，见 05 文档）。

## 3 运行目录（持久数据）

```
<ROUNDTABLE_RUNTIME>/
  roundtable.sqlite3(-wal/-shm)   业务库：快照、证据索引、Case、计划版本、事件账本、复盘、训练、验证、卡组、会话
  evidence/blobs/<sha前2位>/<sha256>.bin   不可变证据仓（原始响应、导入原件、席位原始输出），文件名即哈希
  backups/                        迁移前自动备份；手动备份默认也放这里
  work/                           席位输入物化与运行缓存（可删，可重建）
  audit/                          预留
```

前端 `dist` 与运行数据分开：重建或替换前端不会碰运行目录。

## 4 配置（环境变量）

| 变量 | 默认 | 作用 |
|---|---|---|
| `ROUNDTABLE_RUNTIME` | `WORK_ROOT/runtime` | 运行目录（放本机磁盘） |
| `ROUNDTABLE_DB` | `<运行目录>/roundtable.sqlite3` | 单独指定数据库文件 |
| `ROUNDTABLE_SOURCE_ROOT` | `~/Desktop/交易圆桌_AI交接包_20261019` | 只读源包（旧回测原件登记、字节核对） |
| `ROUNDTABLE_FILE_IMPORT_ROOTS` | `panel/data/daily` 与源包里的日线归档 | 只读历史日线归档（file_import / 历史训练 / 方法验证），多个用 `:` 分隔；客户端不能传路径 |
| `MEOZ_CLI` | `~/Downloads/meoz-2/scripts/meoz.py` | 行情 CLI |
| `ROUNDTABLE_QUIET=1` | 关 | 不输出逐请求访问日志 |
| `ROUNDTABLE_VALIDATE_RESPONSES=1` | 关 | 每个响应按 OpenAPI/JSON Schema 校验（开发自检用） |

日志：只写标准错误（启动行、中断任务回收、访问日志：方法/路径/状态，不含 cookie、令牌与请求体）。需要留存就重定向：
`python3 "$WB/panel/api.py" 2>> ~/交易圆桌_runtime/server.log`。

## 5 健康探针

| 探针 | 请求 | 期望 |
|---|---|---|
| 存活 | `GET /`（静态首页，不需要会话） | 200 |
| 就绪 | 先 `POST /api/v1/session/bootstrap`（同源 Origin），再 `GET /api/v1/health/data` | 200；`status`：`ready`（最新快照完整且新鲜）/ `degraded`（有快照但陈旧或不完整）/ `unavailable`（还没有快照）；另有 `api_ready`、`database_ready`、`quality`、`freshness`、`quote_date` |

未带会话访问 `/api/v1/health/data` 返回 401（受保护接口一律需要本地会话）。部署到托管平台时，平台探针用存活探针；
就绪检查放到身份网关之后。

## 6 备份、恢复与对账

```bash
python3 tools/runtime_backup.py backup  --runtime ~/交易圆桌_runtime            # 服务不用停：SQLite 在线一致性备份 + 证据仓
python3 tools/runtime_backup.py restore --from <备份目录> --to <新的空目录>      # 只恢复到空目录，绝不覆盖
python3 tools/runtime_backup.py verify  --runtime <运行目录>                    # 只读对账
```

`verify` 逐项检查：`PRAGMA integrity_check` 与外键、迁移校验和、每个组合的事件哈希链、按事件重放的现金/股数与投影一致、
冻结计划 `frozen_hash` 可重算、快照指纹可重算、证据文件齐全且哈希相符。任何一项不过都退出码 1。

不要直接复制 `roundtable.sqlite3`：运行中最新的写入可能还在 `-wal` 文件里。完整演练：`python3 tools/backup_restore_drill.py`。

## 7 测试

```bash
python3 -m unittest discover -s tests -t .                 # 单测 48 + 端到端 1 + 首次启动 7（约 50 秒；没装 Playwright 时浏览器那 2 项自动跳过）
LD_LIBRARY_PATH=<Chromium 依赖库目录> python3 -m tests.acceptance.runner --run-id <名字>   # 110 项验收（约 2–3 分钟）
python3 -m tests.acceptance.runner --stage P1               # 只跑一个阶段
python3 -m tests.acceptance.runner --case AC-047            # 只跑一个用例
python3 tools/gen_operation_map.py                          # 改了 driver 后重新生成绑定表
```

验收产物在 `artifacts/acceptance/<run>/`（report.json、junit.xml、每例 http.ndjson / db_assertions.json / result.json / 截图）。
`panel/tests/ui_smoke.js` 等是旧界面的自检脚本，不属于本版验收（见 `panel/tests/README.md`）。

## 8 代码结构

```
panel/
  api.py            入口：参数、组装、信号处理
  config.py         路径与配置（全部可被环境变量覆盖）
  web/              App、路由（operations.json 由 contracts/openapi.yaml 生成）、handlers/、错误结构、会话与 CSRF
  services/         业务：snapshot_*、seat_runner、case_orchestrator、plan_service、journal_service、review_service、
                    replay_service、validation_service、deck_service、alert_service、legacy_*、backup_service …
  domain/           纯函数：金额、canonical-json、时钟、交易日历、费用、成交假设、时点可见性、公司行动、心理规则来源 …
  data/             db.py（迁移/备份）、migrations/*.sql、evidence_store.py；*.json 是旧刷新管道的缓存（旧资料）
  jobs/             任务队列（jobs 表）与进程内工作线程
  adapters/         numcat（meoz CLI）、llm_provider（默认 disabled）、legacy_dashboard
  refresh.py        旧面板刷新管道（保留，只产出旧资料）
```

## 8.5 模型供应商（可选，默认关闭）

`<运行目录>/config/llm.json`（或环境变量 `LLM_*`）决定用哪个模型：`deepseek`（默认，带官方搜索）、
`openai_compatible`、`anthropic`。文件形状是 `{version, active, profiles[]}`：可以存多份存档、随时切换当前用哪一份
（ADR-009），切换用 `python3 tools/llm_config.py profiles|use|save|drop`，页面里也有同一个「模型设置」弹窗；
供应商抽象与新增厂商见 `docs/implementation/decisions/ADR-008-model-provider-abstraction.md`。

密钥解析顺序：环境变量（名字由存档的 `api_key_env` 指定）→ `LLM_ENV_FILE` → `~/.llm.env` → `~/.deepseek.env`
→ `<运行目录>/config/llm_secrets.json`（页面里填的，按存档 id 分开存，0600）。密钥不写进 `llm.json`、不进日志、不进证据；
状态接口只回 `key_source`。

## 9 旧面板刷新管道（保留，只读旧资料）

`refresh.py --once` 现在生成两份旧视图：`panel/data/dashboard.json`（沪深主板＋创业板，默认）与
`panel/data/dashboard_main.json`（仅主板）；`GET /api/dashboard?universe=main` 取后者（ADR-005）。
数据源连不上时用本地日线/资金/事件缓存重建（缺的日子跳过并打印），不会写出一份「看起来成功」的空面板。


`refresh.py --once` / `--serve` 仍可生成旧版 `圆桌决策面板_预览.html` 与 `data/dashboard.json`；新工作台只把它们当
「旧资料」展示（`X-API-Compatibility: legacy`），训练上下文里 legacy 路由一律 409 `LEGACY_DISABLED_IN_REPLAY`。
旧前端源码在 `frontend/legacy_src_20261019/`，单文件 `frontend/standalone.html` 由 `python3 panel/build_standalone.py` 从它构建。
