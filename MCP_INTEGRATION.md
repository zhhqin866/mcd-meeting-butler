# MCP 集成说明

本文档说明「麦会点 · 会议室团餐管家」实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。所有接口参数与返回结构均来自真实调用验证，不是文档抄写。

---

## 1. MCP Server

| 项 | 值 |
|---|---|
| 名称 | `mcd-mcp`（麦当劳中国 MCP Server） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 握手版本 | `2024-11-05`，`initialize` → `notifications/initialized` → `tools/call` |
| 可用 Tool | 实测 35 个 |
| 限流 | 600 请求 / 分钟 / Token，超限返回 429 |
| 官方文档 | <https://github.com/M-China/mcd-mcp-server> |
| 开放平台 | <https://open.mcd.cn/mcp>（注意：不是 `open.mcd.cn`，那是商户合作平台） |

### 客户端实现

`src/mcd_client.py` 是最小实现，**不依赖任何第三方 MCP SDK**，只用 `requests` 完成三步握手：

1. 兼容 `application/json` 与 `text/event-stream` 两种响应格式（MCP 服务端可能返回 SSE）
2. 保存并回传 `Mcp-Session-Id`
3. 429 指数退避重试，401 直接报 Token 失效
4. 统一拆信封：所有 Tool 返回 `{success, code, message, datetime, traceId, data}`，`success=false` 时抛出带 `code` 的异常，业务层直接拿到 `data`

> Token 通过环境变量 `MCD_MCP_TOKEN` 注入，仓库内不含任何真实凭证。

---

## 2. 使用的 Tool

### 核心链路（每次 `plan` 必调）

| # | Tool | 关键入参 | 在本项目中的角色 |
|---|---|---|---|
| 1 | `delivery-query-addresses` | 无 | 取账号下已保存的配送地址，拿到 `addressId` |
| 2 | `delivery-query-stores` | `addressId` + **`beType=6`** | 按地址查可配送的**团餐**门店，返回 `storeCode`、`beCode`、`reservationTimeOptions` |
| 3 | `query-meal-assistance` | `storeCode` + `beType=6` + `orderType=2` + **`beCode`** | 查门店助餐服务，返回 `gmServiceCode` |
| 4 | `query-meals` | `storeCode` + `orderType=2` + `beType=6` + `beCode` | 拉菜单：`categories` 给分类与标签，`meals` 给名称与价格 |
| 5 | `list-nutrition-foods` | 无 | 拉营养表，用于忌口与热量硬约束 |
| 6 | `calculate-price` | 上述门店参数 + `gmServiceCode` + **`reservationDate`** + `items` | 官方计价，返回原价、优惠、实付 |

### 条件调用

| Tool | 触发条件 | 用途 |
|---|---|---|
| `query-meal-detail` | 需要展开套餐组成或可替换配料时 | 取 `modification` 字段，判断能否去掉某配料 |
| `create-order` | 用户加 `--confirm` | 创建订单 |
| `query-order` | 下单后 | 回查订单状态 |

### 未使用（说明理由）

`draw-lottery`、`mall-*`、`party-*`、`campaign-calendar` 等积分商城与主题活动类 Tool 与「会议室团餐」场景无直接关系，**故意不接**——为凑工具数而堆砌会让调用链失去说服力。

---

## 3. 调用流程

```
输入：8 人 / 人均 40 元 / 18:00 开会 / 忌口牛肉、花生 / 需要咖啡
   │
   ├─ 1. delivery-query-addresses
   │      → addressId
   │
   ├─ 2. delivery-query-stores(addressId, beType=6)
   │      → storeCode + beCode + reservationTimeOptions
   │      → 门店：麦当劳××××得来速餐厅
   │      → 时段：午餐 12:00–15:59 / 下午茶 16:00–18:29 / 夜市 18:30–23:59
   │
   ├─ 3. 时段对齐（本地计算，不调接口）
   │      → 18:00 落在「下午茶」，预约时间取 2026-10-24 18:00
   │      → 关键：用门店真实时段决定热量区间，而不是按小时硬猜
   │
   ├─ 4. query-meal-assistance(storeCode, beType=6, orderType=2, beCode, reservationDate)
   │      → 保鲜速达：可用
   │      → 专人分餐：可用（距会议 4h 以上才解锁）
   │      → 专人摆台：可用（同上）
   │      → 选服务等级最高的一项
   │
   ├─ 5. query-meals(...)
   │      → categories: 人气热卖 8 / 主食 18 / 小食甜品 13 / 饮品 12 / 麦咖啡 7
   │      → meals: {code → {name, currentPrice, originalPrice}}
   │      → 合并去重后 48 项可点餐品（已过滤酱料、加购、餐具、服务费）
   │
   ├─ 6. list-nutrition-foods()
   │      → 158 条营养数据，按名称匹配到 28 项
   │
   ├─ 7. 本地组合求解（planner.py）
   │      → 主食 × 小食 × 饮品 穷举，按「超预算 / 热量偏离 / 无咖啡」代价排序
   │      → 忌口与过敏原作为硬约束，命中直接剔除（本次剔除 11 项）
   │
   ├─ 8. calculate-price(..., gmServiceCode, reservationDate, items)
   │      → 原价 ¥310.80 → 实付 ¥308.00
   │      → 若返回 60006「商品已售罄」，逐项试探定位后剔除重解（见下）
   │
   ├─ 9. 助餐服务价差对比
   │      → 用同一份餐单再按 GMS001 计价，把「摆台 vs 折扣」的价差告诉用户
   │
   └─ 10. 输出四 Sheet Excel → 用户确认 → create-order
```

### 关键设计点（都是踩坑换来的）

**① 团餐走的是外送通道，不是到店**
`query-nearby-stores` 是到店场景，必传 `beType`（1=到店自提 / 5=得来速）与 `searchType`。团餐必须走 `delivery-query-stores` + `beType=6`，门店返回里带 `beCode`，而 `beCode` 是后续所有团餐接口的必传项。用错通道，后面算出来的价格全是错的。

**② `beCode` 是团餐链路的通行证**
`query-meal-assistance`、`query-meals`、`calculate-price` 三个接口在 `beType=2/5/6` 时都强制要求 `beCode`。漏传直接返回 `600048 外送场景下 beType=2/5/6 时必传 beCode`。

**③ `gmServiceCode` 必须和 `reservationDate` 一起传**
只传 `gmServiceCode` 会返回 `600062 助餐服务不可用`。助餐服务是「预约制」的，服务码脱离预约时间没有意义。

**④ 菜单要两级拼接**
`query-meals` 的 `categories[].meals[]` 只给 `code` 和 `tags`（`tags` 里含「牛肉」「鸡肉」「100%纯牛肉」这类配料标签），**名称和价格在顶层 `meals` 字典里**。只读 `categories` 会得到一堆没有名字的编码。

**⑤ 营养数据不是 JSON**
`list-nutrition-foods` 返回的是一张紧凑表：首行 `[158]{productName,energyKcal,protein,...}:`，后续每行两空格缩进、逗号分隔。`src/nutrition.py` 专门解析这个格式。

**⑥ 金额单位是「分」**
`calculate-price` 返回的 `originalPrice`、`price`、`discount` 都是分，报表里统一除以 100。

**⑦ 菜单里混着不可售的品项**
`query-meals` 会返回门店当前不可售的商品，`calculate-price` 遇到就整单报 `60006 商品已售罄或不可售`，且错误信息不带商品编码。处理方式是**逐项试探定位罪魁，剔除后重新求解**，而不是粗暴砍掉主食——后者会退化成「只剩一份派和一杯可乐」的荒谬方案。

**⑧ 助餐服务有 4 小时预约门槛**
实测：距会议 5 小时时，`专人分餐` 与 `专人摆台` 的 `enable` 从 `false` 翻成 `true`；不足 4 小时则返回 `unusableReason: 专人服务需提前4h预约`。本项目把它做成了产品逻辑：会议时间 ≥4h 才推荐摆台，否则降级为保鲜速达并明确提示原因。

**⑨ 菜单不随时段过滤，售罄是「按时间」的**
同一天，`2026-10-25 11:30` 落到门店「早餐」时段（08:30–11:59），此时 `query-meals` 依然返回 47 项午市餐品，但几乎全部 `calculate-price` 报 60006。也就是说**菜单接口给的是全量菜单，可售性只有计价时才知道**。这不是 bug，是接口边界——所以本项目会先对齐门店真实时段，并在主食全被剔除时直接告诉用户「这个时段主食全不可售，换到午餐/晚餐」，而不是干巴巴重试 6 轮。

**⑩ 营养表的名称和菜单名对不上**
`list-nutrition-foods` 里叫「热美式中杯」，菜单里叫「麦咖啡™美式」，直接包含匹配命中不了。更麻烦的是**命中失败是静默的**：某项没热量，代价函数就悄悄跳过热约束，最后给出「人均 369 kcal 低于下限」这种自相矛盾的输出。修复分两步——`nutrition.normalize()` 去掉商标、品牌前缀与杯型后逐级放宽匹配（命中率 27/47 → 34/47），求解器改为**按类目中位数补齐缺失热量并计一笔惩罚**，不再让缺数据等于无约束。

**⑪ 地址匹配失败会静默退回第一条**
`delivery-query-addresses` 返回账号下全部地址。如果用户传的城市在账号里没有，早期实现会直接拿第一条——于是「查上海的门店」实际查到了另一个城市的门店，后面每一步都是错的，而且没有任何提示。现在会显式告警，并且**输出里的地址默认只留到省级、门店名换成「麦当劳××××得来速餐厅」、门店编码只留前两位**（门店名里的城市 + 商场名本身就是定位信息，光脱敏地址不够）。

**⑫ 热量约束要能算出「差多少钱能补上」**
只说「热量不足」是没用的。现在求解器会顺手算一笔账：在当前方案上追加哪一项、加多少钱、能到多少 kcal。例如「加 ¥9.0 来一份香芋派（人均 ¥42）可到 601 kcal」。这是团餐决策里用户真正会问的那句话。

**⑬ 预算是硬约束，不是代价函数里的一项**
最初把超预算写成代价函数的一个权重（超 ¥1 记 3.0 分），结果是：预算内无解时，它会给你一个「最接近」的方案，再在备注里轻描淡写地标一句「超出预算」。对行政来说预算超一块钱也报不了，这等于把决策成本又推回给用户。

现在改成两段式求解：**先按 `price ≤ budget` 硬过滤，只在可行域里评分；可行域为空时，显式返回 `feasible: false` + 一份「超支最少」的备选 + 一句「没有满足全部条件的方案，最接近的是这个，超 ¥X」**。这样「无解」本身也变成一条有信息量的输出，而不是让代价函数替用户偷偷妥协。

---

## 4. 业务价值

### 解决的问题

企业会议订餐的真实链路是：问忌口 → 找店 → 翻菜单 → 算总价 → 找券 → 拼单 → 报账。每一步都不难，但串起来平均耗时 20–40 分钟，且**极易出错**：漏掉某人的过敏原、总价超预算、忘记用券、报销没有明细、忘了提前预约导致没有摆台服务。

### 量化收益

| 指标 | 人工 | 麦会点 |
|---|---|---|
| 决策耗时 | 20–40 分钟 | 1–2 分钟 |
| 忌口遗漏率 | 高（靠群消息人工汇总） | 0（标签级硬过滤，本次剔除 11 项） |
| 价格准确性 | 心算，常与实付不符 | 官方 `calculate-price` 计价 |
| 助餐服务 | 不知道有、也不知道要提前 4h | 自动判断并推荐 |
| 报销凭据 | 手写表格 | 自动生成 4 Sheet 报表 |

### 为什么必须用 MCP

这五类数据无法在本地静态维护：

1. **门店可售餐品**随时段与门店变化，`query-meals` 是唯一可信来源
2. **团餐资质与助餐服务**只有 `query-meal-assistance` 能回答，且状态随预约时间动态变化
3. **营养数据**需要与餐品编码对齐，`list-nutrition-foods` 提供这个映射
4. **优惠与实付**由 `calculate-price` 按门店、服务码、预约时间实时计算，本地无法推算
5. **预约时段**来自门店维度的 `reservationTimeOptions`，各店不同

没有 MCP，这个产品只能做成一个「菜单推荐器」；有了 MCP，它才是一个能真正完成交易的团餐管家。

---

## 5. 安全与合规

- 仓库内**不含任何真实 Token**，配置一律使用 `MCD_MCP_TOKEN` 环境变量占位
- `tools/setup_token.py` 用 `getpass` 读取 Token（输入不回显），只写本地文件，不经过网络
- 演示与文档使用示例地址，不含真实收货地址与手机号
- 下单动作必须显式传 `--confirm`，默认只做预演
- 输出报表不含个人身份信息

---

## 6. 已知限制

- 各门店餐品供应状态以 `query-meals` / `calculate-price` 实时返回为准，本工具不做缓存
- 营养表按名称匹配，实测 48 项餐品中匹配到 28 项；未匹配的品项不参与热量约束，报表会标注
- 单 Token 限流 600 次/分钟，客户端已做请求节流与 429 退避
- 团餐场景需要账号本身具备企业团餐资质，普通账号可能查不到 `beType=6` 的门店
