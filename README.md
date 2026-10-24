# 麦会点 · 会议室团餐管家

> 开会最烦的不是议题，是「中午吃什么」。
> 说一句「下午 3 点，8 个人，人均 40，两个人不吃牛肉」，它把菜单、热量、优惠券、分摊账单全算好，附一张 Excel，等你点确认。

基于 **麦当劳中国 MCP** 构建的团餐决策 Agent。输入一场会议的人数、预算、时长、忌口，输出一份可直接下单的点餐方案 —— 不只是"推荐套餐"，而是把**团餐助餐服务、营养约束、优惠券叠加、AA 分摊**这四件最麻烦的事一次性算清。

---

## 目标用户

- **行政 / HR**：每周要订会务餐，反复在群里问「有没有人不吃辣」「谁不吃牛」
- **项目组 leader**：复盘会、评审会、加班餐，预算卡死但人数浮动
- **创业团队**：没有专职行政，订餐这事落在谁头上谁头疼

核心痛点不是"不知道麦当劳有什么"，而是**众口难调 + 预算对不上 + 报销要凭据**。

---

## 它到底做了什么

一句话：**把"问一圈人 → 翻菜单 → 心算总价 → 找券 → 拼单 → 报账"这条链路压成一次对话。**

| 环节 | 人工做法 | 麦会点 |
|---|---|---|
| 摸清需求 | 群里挨个问忌口 | 一次输入，转成结构化约束 |
| 选门店 | 搜地图，不知道能不能送 | `query-nearby-stores` + `query-meal-assistance` 查团餐资质 |
| 挑餐品 | 翻菜单，套餐内容看不全 | `query-meals` + `query-meal-detail` 拉全量组成 |
| 控热量 | 凭感觉 | `list-nutrition-foods` 按人均热量区间约束 |
| 算总价 | 计算器手算 | `calculate-price` 官方计价，含配送费与优惠 |
| 省钱 | 想不起来有券 | `query-store-coupons` + `available-coupons` 自动叠加 |
| 报账 | 手写 Excel | 自动生成点餐表 + AA 分摊 + 营养汇总 |
| 下单 | 打开 App 重输一遍 | `create-order` 确认后直接下单 |

### 和"套餐推荐助手"的区别

市面上的同类作品大多停在「按预算推一个套餐」。麦会点的差异在三点：

1. **真的走团餐通道**：先用 `query-meal-assistance` 判断门店是否支持企业团餐、起订量与助餐服务，再决定走团餐还是普通到店取餐。这是 MCP 里被大量忽略的一个 Tool。
2. **约束是硬的，不是软的**：忌口、过敏、人均热量上下限作为硬约束参与组合求解，不满足就换品，而不是"建议少吃"。
3. **交付物是 Excel，不是一段话**：报销要凭据、分摊要明细，所以最终产物是一份能直接发群里的点餐表。

---

## 安装

```bash
git clone https://github.com/<your-name>/mcd-meeting-butler.git
cd mcd-meeting-butler

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 配置 MCP Token

1. 打开 <https://open.mcd.cn/mcp>，右上角【登录】，手机号验证
2. 点【控制台】→【激活】→ 同意服务协议 → 复制 Token
3. 写入环境变量（**不要写进代码，也不要提交到仓库**）

```bash
export MCD_MCP_TOKEN="你的 Token"        # Windows: set MCD_MCP_TOKEN=...
```

---

## 使用示例

### 1. 先探活，确认 Token 与工具列表

```bash
python -m src.main probe
```

输出当前 Token 可用的 Tool 列表。**第一次跑请先执行这一步**，用于确认权限范围。

### 2. 出一份会议点餐方案

```bash
python -m src.main plan \
  --people 8 \
  --budget 40 \
  --address "上海市徐汇区虹梅路1801号" \
  --start "2026-10-24 15:00" \
  --duration 120 \
  --avoid 牛肉,花生 \
  --need-coffee \
  --out 点餐方案.xlsx
```

实际执行链路：

```
query-nearby-stores(address)
  → query-meal-assistance(store)        判断团餐资质
  → query-meals(store)                  拉全量菜单
  → list-nutrition-foods()              拉营养表，按忌口与热量区间过滤
  → query-meal-detail(combo)            展开套餐可替换项
  → query-store-coupons / available-coupons   找可用券
  → calculate-price(最终购物车)          官方计价校验
  → 输出 Excel（4 个 Sheet）
```

终端会打印方案摘要，同时生成 `点餐方案.xlsx`：

- **Sheet 1 点餐总表**：每人的主食 / 小食 / 饮品 / 热量 / 单价
- **Sheet 2 分摊账单**：总额、人均、AA 到分
- **Sheet 3 营养汇总**：总热量、蛋白质、钠，标注是否超出设定区间
- **Sheet 4 省钱明细**：用券前后对比，省了多少

### 3. 确认后下单

```bash
python -m src.main order --plan 点餐方案.json --confirm
```

不加 `--confirm` 只做预演，不会产生真实订单。

### 4. 没有 Token 也能跑通演示

```bash
python -m src.main plan --people 8 --budget 40 --address "示例地址" --mock --out demo.xlsx
```

`--mock` 使用 `examples/mock_menu.json` 里的静态数据，用于录制演示视频与跑测试。

### 5. 网页版（零依赖，直接双击打开）

```bash
# 不需要 Python、不需要 Token、不需要联网
open web/index.html          # Windows: start web\index.html
```

左侧填人数、人均预算、开会时间、忌口，右侧直接出每人餐单、预算与热量体检、剔除清单、账单分摊，并可一键导出四张 Sheet 的 Excel。网页版内置一份脱敏菜单样本，用于演示完整决策链路；逻辑与 CLI 共用同一套约束求解规则（组合穷举 + 代价函数）。

---

## 参数说明

| 参数 | 说明 | 默认 |
|---|---|---|
| `--people` | 会议人数 | 必填 |
| `--budget` | 人均预算（元） | 必填 |
| `--address` | 会议地点，用于定位门店 | 必填 |
| `--start` | 会议开始时间 | 当前时间 |
| `--duration` | 会议时长（分钟） | 120 |
| `--avoid` | 忌口 / 过敏原，逗号分隔 | 无 |
| `--need-coffee` | 需要咖啡提神（下午场建议开） | 关 |
| `--max-kcal` | 人均热量上限 | 按时段自动推断 |
| `--mock` | 使用本地静态数据 | 关 |
| `--out` | Excel 输出路径 | `点餐方案.xlsx` |

---

## 项目结构

```
mcd-meeting-butler/
├── README.md
├── CONTEST_DECLARATION.md      # 参赛声明（官方原文，勿改）
├── MCP_INTEGRATION.md          # MCP 接入说明（官方要求）
├── workbuddy.md                # WorkBuddy 开发对话记录
├── requirements.txt
├── src/
│   ├── main.py                 # CLI 入口：probe / plan / order
│   ├── mcd_client.py           # Streamable HTTP MCP 客户端（含 SSE 解析）
│   ├── planner.py              # 约束求解与选品逻辑
│   └── report.py               # Excel 报表生成
├── examples/
│   ├── meeting_input.json
│   └── mock_menu.json
└── docs/
    └── demo.md                 # 演示脚本与截图
```

---

## 设计取舍

- **为什么不直接让大模型挑？** 预算、忌口、热量是硬约束，大模型容易在"差不多"上妥协。所以选品交给确定性算法（贪心 + 局部替换），大模型只负责把自然语言转成参数、把结果讲成人话。
- **为什么最终产物是 Excel？** 因为真实场景里这张表要发群、要报销、要留档。一段聊天记录解决不了这个需求。
- **为什么先查团餐资质？** 团餐和普通取餐的起订量、配送、结算方式都不同。不先判断，后面算出来的价格是错的。

---

## 已知限制

- 各门店餐品供应状态以 `query-meals` 实时返回为准，本工具不做缓存
- 营养数据来自 `list-nutrition-foods`，仅供搭配参考，不构成营养建议
- 单 Token 限流 600 次/分钟，批量场景已做请求节流

---

## 免责声明

本项目为「麦当劳程序员节创意开发大赛」参赛作品，由参赛者独立开发，**非麦当劳官方产品**。餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。

---

## License

MIT
