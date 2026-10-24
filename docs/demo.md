# 演示脚本

录制 60–90 秒的 GIF / 短视频用这份脚本，顺序照抄即可。

## 准备

```bash
cd mcd-meeting-butler
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MCD_MCP_TOKEN="你的 Token"
```

> 如果还没拿到 Token，全程加 `--mock`，效果一样，只是数据是静态样本。

---

## 镜头 1｜先证明真连上了（8 秒）

```bash
python -m src.main probe
```

画面停在工具列表上，让观众看到 `mcd-mcp` 真实返回了几十个 Tool。

**旁白**：「这不是本地假数据，是麦当劳官方 MCP 真实返回的工具清单。」

---

## 镜头 2｜一句话下单（25 秒）

```bash
python -m src.main plan \
  --people 8 --budget 40 \
  --address "上海市徐汇区虹梅路1801号" \
  --start "2026-10-24 15:00" \
  --avoid 牛肉,花生 \
  --need-coffee
```

画面依次出现：

```
→ query-nearby-stores 定位门店 ...
→ query-meal-assistance 检查团餐资质 ...
→ query-meals 拉取菜单 ...
→ list-nutrition-foods 拉取营养数据 ...
→ calculate-price 官方计价校验 ...
```

**旁白**：「注意第三行——先查团餐资质，再决定走团餐还是到店取餐。这一步跳过去，后面算出来的价格是错的。」

---

## 镜头 3｜结果（20 秒）

```
■ 下午场 · 8 人 · 人均预算 ¥40
  每人一份：
    · 麦香鱼      ¥17.0  340 kcal
    · 玉米杯      ¥8.0    90 kcal
    · 美式(中)    ¥15.0    5 kcal
  人均 ¥40.0 / 435 kcal
  合计 ¥320.00（预算 ¥320.00）
  ! 方案在预算与热量区间内，可直接下单
  已按忌口剔除 4 项
```

**旁白**：「人均正好卡在 40 块，热量 435 kcal 落在下午场区间内，两个人不吃牛肉——所以巨无霸、双层吉士、吉士汉堡、巨无霸套餐这四项被直接剔除了，不是'建议少吃'。」

---

## 镜头 4｜打开 Excel（25 秒）

打开生成的 `点餐方案.xlsx`，依次切四个 Sheet：

1. **点餐总表** —— 发群里就这张
2. **分摊账单** —— AA 到分，尾差归最后一位
3. **营养汇总** —— 人均热量对比区间
4. **省钱明细** —— 用券前后差多少

**旁白**：「报销要凭据，分摊要明细。所以最终交付物是这张表，不是一段聊天记录。」

---

## 镜头 5｜收尾（5 秒）

```bash
python -m src.main order --plan 点餐方案.json
```

展示「预演模式，未产生真实订单」的提示。

**旁白**：「默认只预演，加 `--confirm` 才真正下单——不敢让 AI 自己花钱。」

---

## 截图清单（README 用）

| 文件名 | 内容 |
|---|---|
| `docs/screenshot-probe.png` | probe 输出的工具列表 |
| `docs/screenshot-plan.png` | plan 的调用链路 + 结果摘要 |
| `docs/screenshot-excel.png` | Excel 点餐总表 |

放在 `docs/` 下，README 里用相对路径引用。
