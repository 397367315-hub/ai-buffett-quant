# 后端接口契约（给 Agent 直接对接）

## GET `/dashboard`

返回 `QuantPageData` JSON。示例见 `src/data.ts`，TS 类型见 `src/types.ts`。

```json
{
  "source": "live",
  "asOf": "2026-10-18T15:00:00+08:00",
  "stages": [
    {"id":"L1","title":"全市场","shortTitle":"初始市场样本","count":4600,"note":"沪深主板 + 创业板","logic":"用于构建本次候选全集","conditions":["沪深主板","创业板"]},
    {"id":"L2","title":"合格池","shortTitle":"基础资格过滤","count":2363,"note":"价格、流动性、上市时间","logic":"根据基础条件排除不符合项","conditions":["非ST","股价≥2元","成交额≥1亿元","上市≥60日"]},
    {"id":"L3","title":"席位覆盖","shortTitle":"席位候选集合","count":80,"note":"任一席位命中","logic":"独立策略候选并集","conditions":["独立策略输出"]},
    {"id":"L4","title":"环境闸门","shortTitle":"环境筛选","count":75,"note":"通过环境判断","logic":"只保留允许参与的席位候选","conditions":["独立环境阈值"]},
    {"id":"L5","title":"去重可执行","shortTitle":"最终候选","count":73,"note":"唯一代码去重","logic":"去重并保留命中席位","conditions":["stock_code唯一"]}
  ],
  "seats": [
    {"id":"wildman","name":"野人哥","subtitle":"保住本金","version":"trading-wildman-v2","gate":"CLOSED","runState":"SUCCESS","count":0,"keywords":["周期与主线","风控优先"],"reasoning":"当前条件未满足","portrait":"/avatars/wildman.webp","configured":true,"updatedAt":"2026-10-18T15:00:00+08:00"}
  ]
}
```

**上述 JSON 是结构演示，不是 2026-10-18 的实际行情。** 请由后端使用真实筛选结果替换所有数字和说明，不得复制演示值作为实时数据。

- `gate` 允许：`OPEN`, `CLOSED`, `MIXED`, `WEAK`, `NO_SIGNAL`, `UNCONFIGURED`。业务中建议把 `gate` 再按 `canOpen:boolean` 和 `marketRegime` 拆分；此演示保持对截图的兼容。
- `runState`：`IDLE`, `RUNNING`, `SUCCESS`, `ERROR`。`runState` 只代表分析任务状态。
- `count`：某席位返回候选数，可与「是否开仓」不一致。
- `seats` 按后端配置返回全部席位，不需要前端写死名字。人物 `portrait` 可由后端传同域素材地址。
- 认证、策略计算、缓存、ST 分类、各层股票代码集合与去重来源都应保留在后端，浏览器只展示结果。
- 现有页面先同步 `GET /dashboard` 快照。需要实时任务进度时再引入 SSE/WebSocket；不要用页面动画替代真实执行进度。

---

## 本仓新增接口与字段（个人池 / 模拟盘）

### `GET /dashboard` 增补的可选字段

| 字段 | 说明 |
|---|---|
| `quotes` | 全市场报价快照 `{代码: [名称, 最新价, 昨收]}`。个人池里的票可能早就不在候选表里，估值靠它。 |
| `quoteDate` | 快照对应的交易日，如 `20261016`。 |
| `seats[].picks` | 该席位自己筛出的票（`code/name/pool/poolLabel/seat/stop/risk_pct/risk_flag/stage/rules`）。闸门没开的席位也会给，界面上标注「不该动手」。 |
| `candidates[].seats` | 同时命中这只票的全部席位名。 |

### `GET /api/portfolio`

```json
{"exists": true, "ledger": {"version":1,"cash":992196.92,"initial":1000000,"watch":[],"trades":[]}, "savedAt":"..."}
```

没有账本时返回 `{"exists": false}`。

### `POST /api/portfolio`

请求体 `{"ledger": {...}}`，整份账本覆盖写入 `panel/data/portfolio.json`（原子写）。
同时把新出现的成交追加进 `panel/data/portfolio-trades.jsonl`——成交流水只追加，不随账本重置删除。

响应 `{"ok": true, "savedAt": "...", "trades": 2}`。

### 模拟成交的口径

下单规则在前端 `src/portfolio.ts` 里，服务端只负责存取，不做撮合：

- 佣金 成交额 × 0.025%，单笔最低 5 元，买卖都收
- 印花税 成交额 × 0.05%，只在卖出收
- 过户费 成交额 × 0.001%，买卖都收
- T+1：当日买入的股份当日不能卖
- 买入必须 100 股整数倍；卖出后剩余不足 100 股必须一次卖完
- 成交价 = 最近一次快照价，不是逐笔行情；涨停价买入、跌停价卖出会给出提示但不拦截
