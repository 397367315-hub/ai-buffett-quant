# 接入说明（本仓库已完成的部分）

这份前端来自 `AGI_Quant_Frontend.zip`，**界面代码基本未改**，只做了一次构建修复和一处数据接线。

## 改了什么

| 文件 | 改动 |
|---|---|
| `src/App.tsx` | 删掉一个未使用的 `CSSProperties` 导入（`tsc -b` 严格模式下报 TS6133，导致 build 失败） |
| `.env.production` / `.env.local` | `VITE_QUANT_API_BASE=/api`，指到同源后端 |
| — | 其余 `styles.css`、`FunnelDiagram.tsx`、`SeatCard.tsx`、`Icons.tsx`、`data.ts`、`types.ts` 与四个头像资源**全部原样保留** |

## 后端在哪

`../panel/api.py` —— 一个纯标准库的 HTTP 服务：

```
GET /api/dashboard   → QuantPageData（真实筛选结果）
GET /api/health      → {"ok":true,"asOf":"...","source":"live"}
其余路径             → frontend/dist 静态文件
```

数据由 `../panel/refresh.py` 生成到 `../panel/data/dashboard.json`，格式严格按 `API_CONTRACT.md`。

## 跑起来

```bash
# 1. 先生成一次数据（约 16 秒，需要猫爪 CLI）
cd ../panel && python3 refresh.py --once

# 2. 起服务
python3 api.py --port 8787
# 打开 http://localhost:8787/
```

要自动刷新：`python3 api.py --port 8787 --refresh 300`

重新构建前端：

```bash
cd frontend && npm install && npm run build
```

## 十席位映射

| 席位 | API id | 头像 |
|---|---|---|
| 野人哥 | `wildman` | ✅ wildman.webp |
| 剑门 | `jianmen` | ✅ jianmen.webp |
| 杨楠 | `yangnan` | ✅ yangnan.webp |
| 炒股养家 | `yangjia` | ✅ chaogu.webp |
| 退学炒股 | `tuixue` | 占位 |
| 一路奔行 | `gushiguifei` | 占位 |
| 威科夫 | `wyckoff` | 占位 |
| 蜡烛图 | `candle` | 占位 |
| 利弗莫尔 | `livermore` | 占位 |
| 达尔文 | `darwin` | 占位 |

后六位没有画像资源，按 `DESIGN_LOCK.md` 的要求保留占位，没有编造人物。

## 两处刻意的数据取舍

**野人哥的 `count` 是 null。** 它的 `screen.py` 输出的是周期定位与角色标注（60 只已标注），**不是买入候选**。买点要按技能 §6.1–6.5 逐条核，脚本不直接出票。所以这里标 null 而不是凑一个数。

**退学炒股 `gate` 是 CLOSED。** 它是审查席，不产生候选。CLOSED 在这个语境下表示「不作为选股源」，理由写在 `reasoning` 里。

## 演示数据 vs 真实数据

`src/data.ts` 里的 `demoData` 原样保留（那是原设计稿的演示数值）。只要 `/api/dashboard` 通则页面显示「已接入真实数据」，失败则回退 demo 并明确报错，不会假装成功。
