# 交易圆桌工作台 · 前端

React 18 + TypeScript + Vite，没有路由库和状态库。由本地服务 `panel/api.py` 同源提供（`frontend/dist`），只调用 `/api`（上一版刷新管道）
与 `/api/v1`（新版）。**两套外壳并存**（见 `docs/implementation/decisions/ADR-004-restore-legacy-shell.md`）：

| 外壳 | 地址 | 代码 | 数据 |
|---|---|---|---|
| **上一版**（默认） | `#/funnel`、`#/seats`、`#/candidates`、`#/portfolio`、`#/backtest`、`#/environment`、`#/cases` | `src/legacy/`（从 `legacy_src_20261019` 原样接回） | `/api/dashboard`（refresh.py 生成）＋ 浏览器本地账本 |
| **实验功能**（新版七工作区） | `#/lab/environment`、`#/lab/cases`、`#/lab/plans`、`#/lab/portfolio`、`#/lab/training`、`#/lab/validation`、`#/lab/seats` | `src/app/`、`src/pages/`、`src/components/` | `/api/v1` 快照、计划、账本、训练、验证 |

「环境概览」「案例研究」两页同时出现在两边：上一版外壳里嵌一份（`src/app/V1Island.tsx`），实验功能里也有。

## 构建

```bash
npm ci                 # 按 package-lock.json 安装（Node ≥ 18）

# 改了样式源文件后重新生成作用域化 CSS（在本目录执行；WORK_ROOT = ~/Desktop/交易圆桌_十三席_WORK）
python3 ~/Desktop/交易圆桌_十三席_WORK/tools/scope_css.py src/styles.css src/v1.css .v1-shell
python3 ~/Desktop/交易圆桌_十三席_WORK/tools/scope_css.py \
        legacy_src_20261019/dist/assets/index-Cp8BGIgd.css src/legacy/legacy.css .legacy-shell

npm run build          # tsc -b && vite build → dist/
npm run dev            # vite 开发服务器（127.0.0.1:5173），后端加 --dev-origin http://127.0.0.1:5173
```

改了 `src/` 一定要重新 `npm run build`，否则服务里还是旧页面。`tests/unit/test_frontend_css.py` 会检查两个作用域化 CSS 与源文件是否同步。

`src/data/seat-capabilities.json` 与 `public/psychology_rules.v1.json` 是生成物，由 `WORK_ROOT/tools/sync_frontend_data.py` 从后端同步，不要手改。

## 样式隔离（重要）

两套界面共用一批类名（`.panel .btn .tag .row .note .check .section .empty` 等），所以**两套 CSS 都按外壳作用域化**：

- 上一版：`src/legacy/legacy.css`，作用域 `.legacy-shell`（源：上一版构建出的 CSS，保证像素级一致）；
- 新版：`src/v1.css`，作用域 `.v1-shell`（源：`src/styles.css`，人改这一份）；
- 最外层规则在 `src/hybrid.css`（`html/body/#root`、两个外壳的最小高度、侧栏底部「实验」按钮、手机底部导航）。

不要让任何一套 CSS 以全局选择器出现：`tools/scope_css.py --check` 会比对生成物，`tests/unit/test_frontend_css.py` 会跑它。

## 结构

| 位置 | 内容 |
|---|---|
| `src/App.tsx` | 外壳路由：默认上一版；`#/lab/...` 进实验功能 |
| `src/lib/route.ts` | hash 路由、两套外壳的地址前缀、裸 v1 地址规范化 |
| `src/lib/appData.ts` | v1 数据层（会话、健康、最新快照、注册表、索引），两处共用 |
| `src/app/Workbench.tsx` | 实验功能：状态条 + 工作区导航 + 七个 v1 页面 |
| `src/app/V1Island.tsx` | 把环境概览 / 案例研究嵌进上一版外壳 |
| `src/pages/` | 新版页面（环境概览、案例研究、席位库、事前计划、模拟仓与日记、历史训练、方法验证） |
| `src/legacy/` | 上一版五页：`LegacyApp.tsx`、`components/`、`data.ts`、`portfolio.ts`、`types.ts`、`legacy.css` |
| `src/components/`、`src/lib/` | 新版通用组件与工具（金额 BigInt 分、上海时间、标签、hooks） |
| `legacy_src_20261019/` | 上一版源码与构建产物的原始备份（只读参考，不参与构建） |

约定：涨红跌绿，不分方向的量不上色；所有状态都有文字；缺失显示「--」或「不可判定」并说明缺什么；
金额不用浮点；时间写明是上海时间。上一版页面在 `/api/dashboard` 失败时会按上一版的行为回退静态演示数据，
并在顶部写明「真实接口连接失败……回退为静态演示数据」、页头标「界面演示模式」；新版页面绝不回退演示数据。

## 旧文档

`API_CONTRACT.md`、`INTEGRATION.md`、`DESIGN_HANDOFF.md`、`DESIGN_LOCK.md` 描述的是上一版的接口与设计，新版以
`WORK_ROOT/contracts/openapi.yaml` 为准。
