# 04-changecast（变投影）

Changecast — 变更日志投影：源变更流 → 按序投影 → 乱序/晚到策略钩子 → 快照可查互证。

与 Feedstitch（多源 Feed 归并）、Batchgate（作业依赖编排）、Syncledger（双端对账）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

## 主链

配置投影流（含 upsert 合并策略）→ 读取 CDC 夹具并按 seq 应用到投影表 → 批次计数与快照互证

## upsert 合并策略（按流互斥）

同一投影流同一时刻只能启用一种策略：

| 策略 | 键 | 语义 |
| --- | --- | --- |
| 字段级补丁 | `patch` | 只更新事件载荷里出现的键；未出现字段保留投影原值。显式 `null` 仍是写入值。 |
| 整行替换 | `replace` | 事件载荷成为该实体完整新行；未出现字段视为清除。 |

约束与保证：

- 策略随流保存；每次批次开启时固化当时的策略快照（`project_batches.upsert_mode`）。
- 批次进行中禁止切换策略；切换接口与投影接口都会拒绝，数据库层另有「每流至多一个 running 批次」的唯一索引兜底。
- 策略变更只作用于之后新开批次，不重写历史批次的计数、留痕与写入语义。
- 补丁策略下缺键绝不把原值抹成空；整行替换下显式 `null` 与缺省清除在事件留痕中分别标注为
  `explicit_null` / `missing`。
- 事件留痕保存每事件的合并前/合并后载荷、保留字段与清除字段明细。

## 种子投影流

- **member_profile**：会员档案变更（`member_id`，字段 `display_name` / `tier` / `region`），默认 `patch`
- **store_archive**：门店档案变更（`store_id`，字段 `name` / `status` / `city`），默认 `replace`
- **channel_attr**：渠道属性变更（`channel_code`，字段 `label` / `active`），默认 `patch`

夹具含只带部分字段的补丁事件，例如 `mp-007`（仅 `tier`）、`sa-006`（仅 `status`）、`ch-006`（仅 `active`），
用于演示补丁保留未给字段、整行替换清字段。

底座默认按 **事件 seq 升序** 投影；乱序缓冲表空挂，深逻辑留给后题。对外文案不用「工单」「CRM」。

## 页面

- **投影流**：逐流配置补丁 / 整行替换，显示当前策略与变更留痕（含生效截止批次）。
- **投影台**：发起投影；以同一快照只读回放两种策略，并排展示终态差异字段。
- **应用批次**：每批写入的批次显示其固化的策略。
- **事件留痕**：每事件的策略、合并前后载荷、保留字段、清除原因。
- **投影快照**：当前投影载荷与 applied_seq。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 测试

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q
```

## 0-1 切口（未实现，见出题规划）

`arrival_vs_event_order` / `ooo_window` / `event_idempotency` / `tombstone_policy` / `watermark_rewind` / …
