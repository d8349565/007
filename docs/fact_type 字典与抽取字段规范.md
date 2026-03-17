# fact_type 字典与抽取字段规范

## 一、文档定位

本文档用于定义“资讯颗粒化项目”第一版的 `fact_type` 字典与抽取字段规范，作为后续以下模块的统一基础：

- `Evidence Finder` 的候选识别规则
- `Fact Extractor` 的结构化输出模板
- `Reviewer / Validator` 的审核规则
- `fact_atom` 入库逻辑
- `entity linking` 与 `relation build` 的字段映射
- 查询分析、时间轴、企业画像等下游消费逻辑

说明：

- 所有英文名称、字段名、状态名保持不变
- 在英文术语基础上补充中文解释
- 本文档优先面向 MVP 阶段，不追求一次覆盖所有行业事实
- 第一版重点解决“抽取稳定性”和“字段一致性”

---

## 二、设计目标

`fact_type` 的作用不是给文章打标签，而是为“事实原子”提供稳定的结构模板。

也就是说：

- 同一类事实必须使用同一套字段规则
- 同一字段在不同 `fact_type` 中尽量保持一致语义
- 不允许同一个概念今天放 `predicate`，明天放 `qualifier_json`
- 不允许将评论性内容和硬数值事实混在同一个模板里

结论：

**`fact_type` 的本质是“结构化抽取协议”。**

---

## 三、统一抽取原则

### 3.1 只抽取 evidence 明确支持的信息

允许抽取：

- 原文明确给出的主体
- 原文明确给出的数值
- 原文明确给出的单位
- 原文明确给出的时间
- 原文明确给出的地点
- 原文明确给出的对象
- 原文明确给出的同比、环比、份额、变化方向

不允许抽取：

- 模型根据常识补全的内容
- 模型推断出的隐含因果
- 原文未明确表达的时间口径
- 原文未明确给出的排序位次
- 原文未明确说明的市场 scope

---

### 3.2 一句可以拆成多个 `fact_atom`

如果一句话里有多个事实，必须拆开，不允许合并成一条模糊记录。

例如：

“2023年主营业务收入同比下降4.5%至4044.8亿元，利润总额同比增长9.5%至237.4亿元。”

应拆成两条 `fact_atom`：

- 一条是 `revenue_in_period`
- 一条是 `profit_in_period`

---

### 3.3 `fact_type` 是大类，`predicate` 是细分动作

例如：

- `FINANCIAL_METRIC` 是大类
- `revenue_in_period`、`profit_in_period`、`gross_margin_in_period` 是细分 `predicate`

这样设计的好处：

- `fact_type` 数量不会爆炸
- 细分指标仍可稳定表达
- 下游统计时既可以按大类，也可以按子类

---

### 3.4 时间必须分口径保存

抽取时要区分：

- `publish_time`：文章发布时间，存在 `source_document`
- `time_expr`：原文时间表达
- `time_start` / `time_end`：标准化后的时间范围

必要时可通过 `qualifier_json` 增加：

- `ranking_year`
- `report_period`
- `forecast_period`
- `effective_date`

---

### 3.5 `scope` 不清楚时不能硬填

尤其是这类事实：

- market share
- CR3 / CR5 / CR10
- top10 share
- segment revenue share

必须明确以下任一项：

- 全国市场
- 某细分市场
- 某区域市场
- 某产品线
- 某榜单口径

建议统一放在 `qualifier_json.market_scope` 中。

---

### 3.6 定性事实与定量事实分开

定量事实：

- revenue
- sales volume
- capacity
- market share
- investment amount

定性事实：

- demand weak
- market sluggish
- competition intensified
- high concentration
- growth strong

这两类不要混在同一个 `fact_type` 模板里。

---

## 四、统一字段规范

以下为所有 `fact_type` 尽量共用的字段定义。

### 4.1 核心字段

- `fact_type`：事实类型，大类
- `subject`：主体原始文本
- `predicate`：细分动作/关系
- `object`：对象原始文本
- `value_num`：数值型结果
- `value_text`：原始值文本
- `unit`：单位
- `currency`：币种
- `time_expr`：原始时间表达
- `location`：地点原始文本
- `qualifiers`：补充限定信息
- `confidence`：模型置信度

---

### 4.2 字段语义说明

#### `subject`
中文说明：事实主语，通常是公司、行业、市场、产品、政策、项目等。

示例：

- 中国涂料工业
- 佐敦
- 汽车涂料市场
- 某工厂
- 某政策

#### `predicate`
中文说明：事实动作或属性，用于表达“subject 在做什么”或“subject 具有什么指标”。

示例：

- `revenue_in_period`
- `output_in_period`
- `market_share_in_period`
- `invested_in`
- `launched`
- `certified_by`

#### `object`
中文说明：事实对象，通常在关系型事实中使用。如果没有明确对象，可为空。

示例：

- 船舶涂料
- 某合作方
- 某项目
- 某认证机构

#### `value_num`
中文说明：可解析为数值的标准值，供数据库计算和查询使用。

示例：

- `4044.8`
- `3577.2`
- `96`
- `5`

#### `value_text`
中文说明：原文中的数值表达，用于保留原始口径。

示例：

- “4044.8亿元”
- “5万吨”
- “高达96%”
- “超10亿元”

#### `unit`
中文说明：标准单位。

示例：

- `亿元`
- `万吨`
- `%`
- `辆`
- `万平方米`

#### `currency`
中文说明：币种。若原文未体现，允许为空。

示例：

- `CNY`
- `USD`
- `EUR`

#### `time_expr`
中文说明：原文中的时间表达，不做解释时原样保存。

示例：

- `2023年`
- `2025年上半年`
- `2024年前三季度`
- `自2026年4月1日起`

#### `location`
中文说明：地点原始文本。

示例：

- 常州
- 天津
- 中国
- 华东地区

#### `qualifiers`
中文说明：补充限定信息，统一使用 JSON 对象表达。

常见内容：

- `metric_name`
- `market_scope`
- `segment`
- `yoy`
- `qoq`
- `ranking_name`
- `ranking_year`
- `is_forecast`
- `effective_date`
- `capacity_type`
- `source_scope`

#### `confidence`
中文说明：模型输出置信度，建议范围 `0 ~ 1`。

---

## 五、第一版 `fact_type` 字典

第一版建议使用以下 12 类主类型，外加 2 个建议补充类型。

---

## 5.1 `FINANCIAL_METRIC`

中文说明：财务指标类事实。

适用场景：

- 主营业务收入
- 营收
- 利润总额
- 净利润
- 毛利率
- 费用率
- 单位产品收入
- 分业务收入

常见 `predicate`：

- `revenue_in_period`
- `profit_in_period`
- `net_profit_in_period`
- `gross_margin_in_period`
- `expense_ratio_in_period`
- `segment_revenue_in_period`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `value_num`
- `value_text`
- `unit`
- `currency`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `metric_name`
- `segment`
- `yoy`
- `qoq`
- `report_scope`
- `is_forecast`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `value_text`
- `time_expr`

可选字段：

- `value_num`
- `unit`
- `currency`
- `qualifiers`

常见关键词：

- 营收
- 收入
- 主营业务收入
- 利润
- 净利润
- 毛利率
- 同比
- 环比

容易混淆点：

- “收入增长”可能是财务收入，也可能是销售额
- “销售收入”在某些行业文章里也可能更适合放入 `FINANCIAL_METRIC`
- “同比增长 5%”不能丢掉绝对值和时间

审核重点：

- 指标名称是否明确
- 金额是否有单位
- 时间口径是否明确
- 同比/环比是否被误当成主值

标准输出示例：

```json
{
  "fact_type": "FINANCIAL_METRIC",
  "subject": "中国涂料工业",
  "predicate": "revenue_in_period",
  "object": null,
  "value_num": 4044.8,
  "value_text": "4044.8亿元",
  "unit": "亿元",
  "currency": "CNY",
  "time_expr": "2023年",
  "location": null,
  "qualifiers": {
    "metric_name": "主营业务收入",
    "yoy": -4.5
  },
  "confidence": 0.96
}
```

---

## 5.2 `SALES_VOLUME`

中文说明：产量、销量、消费量等数量类事实。

适用场景：

- 总产量
- 销量
- 表观消费量
- 出货量
- 装机量
- 交付量

常见 `predicate`：

- `output_in_period`
- `sales_in_period`
- `consumption_in_period`
- `shipment_in_period`
- `delivery_in_period`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `value_num`
- `value_text`
- `unit`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `metric_name`
- `segment`
- `yoy`
- `qoq`
- `market_scope`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `value_text`
- `time_expr`

常见关键词：

- 产量
- 销量
- 消费量
- 出货量
- 交付量
- 同比

容易混淆点：

- “总产量”和“表观消费量”不能混成一个口径
- 单位可能是“吨”“万吨”“辆”“台”
- 产量和产能完全不是一回事

审核重点：

- 是产量、销量还是消费量
- 单位是否正确
- 时间是否对应同一口径

标准输出示例：

```json
{
  "fact_type": "SALES_VOLUME",
  "subject": "中国涂料工业",
  "predicate": "output_in_period",
  "object": null,
  "value_num": 3577.2,
  "value_text": "3577.2万吨",
  "unit": "万吨",
  "currency": null,
  "time_expr": "2023年",
  "location": "中国",
  "qualifiers": {
    "metric_name": "总产量",
    "yoy": 4.5
  },
  "confidence": 0.95
}
```

---

## 5.3 `CAPACITY`

中文说明：产能类事实。

适用场景：

- 设计产能
- 新增产能
- 年产能
- 投产规模
- 扩建后产能

常见 `predicate`：

- `capacity_in_period`
- `new_capacity_in_period`
- `expanded_capacity_in_period`
- `designed_capacity_of`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `value_num`
- `value_text`
- `unit`
- `time_expr`
- `location`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `capacity_type`
- `is_new_capacity`
- `segment`
- `project_name`
- `effective_date`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `value_text`

常见关键词：

- 产能
- 年产
- 新增
- 扩建
- 投产
- 达产

容易混淆点：

- 产能与产量混淆
- “总投资 10 亿元，建成 5 万吨产能”一句含两条事实
- 时间可能是开工时间、投产时间、达产时间，不是同一个口径

审核重点：

- 是否真的是 capacity
- 单位是否为“吨/年”等产能口径
- 地点和项目是否明确

标准输出示例：

```json
{
  "fact_type": "CAPACITY",
  "subject": "某公司",
  "predicate": "new_capacity_in_period",
  "object": "汽车涂料产线",
  "value_num": 50000,
  "value_text": "5万吨",
  "unit": "吨/年",
  "currency": null,
  "time_expr": "2026年",
  "location": "常州",
  "qualifiers": {
    "capacity_type": "汽车涂料",
    "is_new_capacity": true
  },
  "confidence": 0.92
}
```

---

## 5.4 `PRICE_CHANGE`

中文说明：价格调整类事实。

适用场景：

- 涨价函
- 降价
- 调价通知
- 单位价格变化
- 原料涨价传导

常见 `predicate`：

- `price_changed`
- `price_increased`
- `price_decreased`
- `asp_in_period`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `value_num`
- `value_text`
- `unit`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `change_type`
- `change_value`
- `effective_date`
- `reason`
- `segment`
- `product_scope`

必填字段：

- `fact_type`
- `subject`
- `predicate`

常见关键词：

- 涨价
- 降价
- 调价
- 价格上调
- 价格下调
- 自某日起执行

容易混淆点：

- 有些文章只说“涨价”，没有给具体幅度
- 有些说的是原材料涨价，不是产品涨价
- “每吨上调 500 元”和“上涨 5%”口径不同

审核重点：

- 调价对象是否明确
- 生效日期是否明确
- 调价幅度是否明确
- 是产品价格还是原材料价格

标准输出示例：

```json
{
  "fact_type": "PRICE_CHANGE",
  "subject": "某涂料企业",
  "predicate": "price_increased",
  "object": "汽车修补漆",
  "value_num": 500,
  "value_text": "每吨上调500元",
  "unit": "元/吨",
  "currency": "CNY",
  "time_expr": "自2026年4月1日起",
  "location": null,
  "qualifiers": {
    "change_type": "increase",
    "effective_date": "2026-04-01",
    "reason": "原材料成本上涨"
  },
  "confidence": 0.93
}
```

---

## 5.5 `INVESTMENT`

中文说明：投资类事实。

适用场景：

- 投资金额
- 新建项目投资
- 增资扩产
- 对外投资
- 战略投资

常见 `predicate`：

- `invested_in`
- `investment_in_period`
- `capital_increased_for`
- `strategic_investment_in`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `value_num`
- `value_text`
- `unit`
- `currency`
- `time_expr`
- `location`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `project_name`
- `investment_type`
- `segment`
- `purpose`
- `stage`

必填字段：

- `fact_type`
- `subject`
- `predicate`

常见关键词：

- 投资
- 计划投资
- 总投资
- 增资
- 项目投资
- 战略投资

容易混淆点：

- “总投资”与“注册资本”不同
- “拟投资”与“已投资”状态不同
- 同一句可能同时出现投资额和产能

审核重点：

- 投资主体是否明确
- 投资对象是否明确
- 是否为计划值还是完成值

标准输出示例：

```json
{
  "fact_type": "INVESTMENT",
  "subject": "某公司",
  "predicate": "investment_in_period",
  "object": "华东生产基地项目",
  "value_num": 10,
  "value_text": "总投资10亿元",
  "unit": "亿元",
  "currency": "CNY",
  "time_expr": "2025年",
  "location": "江苏",
  "qualifiers": {
    "investment_type": "project_investment",
    "purpose": "扩建汽车涂料产线"
  },
  "confidence": 0.91
}
```

---

## 5.6 `EXPANSION`

中文说明：扩产、扩建、项目建设类事实。

适用场景：

- 扩建工厂
- 新增产线
- 新基地建设
- 技改扩产
- 二期项目启动

常见 `predicate`：

- `expanded_in`
- `built_new_plant`
- `launched_project`
- `started_construction_of`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `location`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `project_name`
- `segment`
- `stage`
- `capacity_related`
- `investment_related`

必填字段：

- `fact_type`
- `subject`
- `predicate`

常见关键词：

- 扩建
- 扩产
- 新建
- 开工
- 投产
- 建设
- 二期

容易混淆点：

- 与 `CAPACITY`、`INVESTMENT` 经常一起出现
- `EXPANSION` 更偏事件
- `CAPACITY` 更偏产能结果
- `INVESTMENT` 更偏金额结果

审核重点：

- 这是扩产事件，还是产能数字，还是投资金额
- 项目名称和地点是否明确
- 阶段是否明确：签约、开工、建设、投产、达产

标准输出示例：

```json
{
  "fact_type": "EXPANSION",
  "subject": "某公司",
  "predicate": "built_new_plant",
  "object": "汽车涂料生产基地",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2025年10月",
  "location": "常州",
  "qualifiers": {
    "project_name": "华东基地项目",
    "stage": "construction_started",
    "segment": "汽车涂料"
  },
  "confidence": 0.89
}
```

---

## 5.7 `NEW_PRODUCT`

中文说明：新品发布、新产品投放类事实。

适用场景：

- 推出新品
- 发布新涂层体系
- 新系列上市
- 新型号发布

常见 `predicate`：

- `launched`
- `released_new_product`
- `introduced_new_series`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `product_type`
- `application`
- `technology_route`
- `target_market`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `object`

常见关键词：

- 推出
- 发布
- 上市
- 新品
- 新系列
- 新涂层体系

容易混淆点：

- “推出新产品”与“项目启动”不是一回事
- 产品名和技术体系名要区分
- 若文章仅说“展示新产品”，不一定等于正式上市

审核重点：

- 产品对象是否明确
- 是正式发布还是宣传展示
- 应用场景是否明确

标准输出示例：

```json
{
  "fact_type": "NEW_PRODUCT",
  "subject": "某企业",
  "predicate": "launched",
  "object": "低VOC汽车面漆系列",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2025年6月",
  "location": null,
  "qualifiers": {
    "product_type": "汽车面漆",
    "application": "乘用车",
    "technology_route": "低VOC"
  },
  "confidence": 0.90
}
```

---

## 5.8 `COOPERATION`

中文说明：合作类事实。

适用场景：

- 战略合作
- 联合开发
- 签约合作
- 供应合作
- 框架协议

常见 `predicate`：

- `cooperated_with`
- `signed_agreement_with`
- `jointly_developed_with`
- `supplies_to`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `cooperation_type`
- `project_name`
- `segment`
- `duration`
- `scope`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `object`

常见关键词：

- 合作
- 签署协议
- 战略合作
- 联合开发
- 供应
- 框架协议

容易混淆点：

- 供应关系和一般合作关系不同
- “达成合作意向”与“已签约”不同
- 一些 PR 文案会夸大关系强度

审核重点：

- 合作双方是否明确
- 合作类型是否明确
- 是否有时间与范围信息

标准输出示例：

```json
{
  "fact_type": "COOPERATION",
  "subject": "某涂料企业",
  "predicate": "signed_agreement_with",
  "object": "某主机厂",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2025年8月",
  "location": null,
  "qualifiers": {
    "cooperation_type": "strategic_cooperation",
    "scope": "新能源汽车涂层联合开发"
  },
  "confidence": 0.88
}
```

---

## 5.9 `MNA`

中文说明：并购、收购、出售、资产整合类事实。

适用场景：

- 收购
- 并购
- 股权转让
- 资产出售
- 业务整合

常见 `predicate`：

- `acquired`
- `merged_with`
- `sold_to`
- `transferred_equity_to`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `value_num`
- `value_text`
- `unit`
- `currency`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `transaction_type`
- `equity_ratio`
- `transaction_scope`
- `target_business`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `object`

常见关键词：

- 收购
- 并购
- 出售
- 转让
- 整合
- 交易对价

容易混淆点：

- “投资入股”与“收购控股”不同
- 金额、比例、标的业务必须分清
- 有些只是公告计划，不是已完成

审核重点：

- 买方卖方是否明确
- 标的对象是否明确
- 比例与金额是否被准确抽出
- 是否已完成还是拟进行

标准输出示例：

```json
{
  "fact_type": "MNA",
  "subject": "某公司",
  "predicate": "acquired",
  "object": "某涂料企业",
  "value_num": 60,
  "value_text": "收购60%股权",
  "unit": "%",
  "currency": null,
  "time_expr": "2025年11月",
  "location": null,
  "qualifiers": {
    "transaction_type": "equity_acquisition",
    "target_business": "工业涂料业务"
  },
  "confidence": 0.92
}
```

---

## 5.10 `POLICY_RELEASE`

中文说明：政策、标准、通知、监管要求发布类事实。

适用场景：

- 政策发布
- 标准实施
- 监管通知
- 指导意见
- 地方扶持政策

常见 `predicate`：

- `released`
- `implemented`
- `issued`
- `took_effect`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `policy_type`
- `policy_scope`
- `effective_date`
- `issuing_authority`
- `region`

必填字段：

- `fact_type`
- `subject`
- `predicate`

常见关键词：

- 发布
- 印发
- 实施
- 生效
- 标准
- 通知
- 指导意见

容易混淆点：

- 政策名与发布机构要分开
- “公开征求意见稿”不等于正式生效
- 发布时间和生效时间不是一回事

审核重点：

- 发布主体是否明确
- 政策名称是否明确
- 生效日期是否明确
- 区域适用范围是否明确

标准输出示例：

```json
{
  "fact_type": "POLICY_RELEASE",
  "subject": "某监管机构",
  "predicate": "released",
  "object": "低VOC涂料应用指导意见",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2026年3月",
  "location": "中国",
  "qualifiers": {
    "policy_type": "guideline",
    "effective_date": "2026-04-01",
    "policy_scope": "工业涂料"
  },
  "confidence": 0.94
}
```

---

## 5.11 `CERTIFICATION`

中文说明：认证、资质、准入、型式认可类事实。

适用场景：

- 船级社认证
- 产品认证
- 体系认证
- 准入资格
- 资质获得

常见 `predicate`：

- `certified_by`
- `approved_by`
- `qualified_for`
- `obtained_certification`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `certification_type`
- `certification_scope`
- `authority`
- `validity_period`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `object`

常见关键词：

- 认证
- 获得资质
- 通过审核
- 型式认可
- 准入
- 证书

容易混淆点：

- 产品认证与公司体系认证不同
- “提交申请”不等于“通过认证”
- 认证机构与认证对象不能混

审核重点：

- 认证对象是否明确
- 认证机构是否明确
- 是申请、通过还是续证

标准输出示例：

```json
{
  "fact_type": "CERTIFICATION",
  "subject": "某船舶涂料产品",
  "predicate": "certified_by",
  "object": "CCS",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2025年12月",
  "location": null,
  "qualifiers": {
    "certification_type": "type_approval",
    "certification_scope": "船舶防腐涂层"
  },
  "confidence": 0.93
}
```

---

## 5.12 `MARKET_SHARE`

中文说明：市占率、集中度、份额类事实。

适用场景：

- 市占率
- 份额
- CR3 / CR5 / CR10
- top10 share
- segment share

常见 `predicate`：

- `market_share_in_period`
- `cr10_in_period`
- `cr5_in_period`
- `share_of_top10_in_period`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `value_num`
- `value_text`
- `unit`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `market_scope`
- `segment`
- `ranking_scope`
- `source_scope`

必填字段：

- `fact_type`
- `subject`
- `predicate`
- `value_text`

常见关键词：

- 市占率
- 份额
- CR10
- CR5
- 前十强
- 占比

容易混淆点：

- 必须明确 scope
- 全国市场和细分市场不能混
- top10 share 和单一企业 share 不是一回事

审核重点：

- `market_scope` 是否明确
- 数值单位是否为 `%`
- `subject` 是单一企业、行业还是前十强集合

标准输出示例：

```json
{
  "fact_type": "MARKET_SHARE",
  "subject": "中国船舶涂料市场前十强企业",
  "predicate": "share_of_top10_in_period",
  "object": null,
  "value_num": 96,
  "value_text": "96%",
  "unit": "%",
  "currency": null,
  "time_expr": "2024年榜单口径",
  "location": "中国",
  "qualifiers": {
    "market_scope": "中国船舶涂料市场",
    "ranking_scope": "top10"
  },
  "confidence": 0.94
}
```

---

## 六、建议补充的 2 个 `fact_type`

这两个不一定在 MVP 第一周上线，但我建议尽快纳入。

---

## 6.1 `COMPETITIVE_RANKING`

中文说明：榜单入选、排名、竞争力排行类事实。

适用场景：

- TOP 10 入选
- 排行榜上榜
- 竞争力榜单
- 品牌榜单
- 赛道榜单

常见 `predicate`：

- `ranked_in_top10`
- `ranked_no1_in`
- `listed_in_ranking`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `ranking_name`
- `ranking_year`
- `segment`
- `rank`
- `ranking_scope`

注意：

- 如果原文只说“TOP 10 名单”，没有具体位次，不得硬填 `rank`
- 该类型非常适合处理榜单类文章

标准输出示例：

```json
{
  "fact_type": "COMPETITIVE_RANKING",
  "subject": "佐敦",
  "predicate": "ranked_in_top10",
  "object": "船舶涂料类TOP 10",
  "value_num": null,
  "value_text": null,
  "unit": null,
  "currency": null,
  "time_expr": "2024年",
  "location": "中国",
  "qualifiers": {
    "ranking_name": "2024中国涂料工业专业细分市场竞争力排行榜",
    "segment": "船舶涂料",
    "ranking_scope": "top10"
  },
  "confidence": 0.95
}
```

---

## 6.2 `SEGMENT_TREND`

中文说明：细分市场趋势判断类事实。

适用场景：

- 需求增长强劲
- 市场低迷
- 需求萎缩
- 竞争加剧
- 景气度回升

常见 `predicate`：

- `demand_trend_in_period`
- `competition_trend_in_period`
- `prosperity_trend_in_period`

建议字段：

- `fact_type`
- `subject`
- `predicate`
- `object`
- `time_expr`
- `qualifiers`
- `confidence`

建议 `qualifiers`：

- `trend_direction`
- `market_scope`
- `evidence_strength`

注意：

- 这是定性类事实
- 不能与硬指标混算
- 更适合作为辅助标签或洞察层输入

标准输出示例：

```json
{
  "fact_type": "SEGMENT_TREND",
  "subject": "建筑涂料市场",
  "predicate": "demand_trend_in_period",
  "object": "继续萎缩",
  "value_num": null,
  "value_text": "继续萎缩",
  "unit": null,
  "currency": null,
  "time_expr": "2023年",
  "location": "中国",
  "qualifiers": {
    "trend_direction": "down",
    "evidence_strength": "narrative"
  },
  "confidence": 0.78
}
```

---

## 七、统一输出 JSON 模板

所有 `Fact Extractor` 建议统一输出为以下格式：

```json
{
  "fact_type": "FINANCIAL_METRIC",
  "subject": "主体原始文本",
  "predicate": "细分动作",
  "object": "对象原始文本",
  "value_num": 0,
  "value_text": "原始值文本",
  "unit": "单位",
  "currency": "币种",
  "time_expr": "原始时间表达",
  "location": "地点原始文本",
  "qualifiers": {},
  "confidence": 0.0
}
```

字段说明：

- `subject`、`object`、`location` 在抽取阶段先保留原始文本
- 实体标准化在后续 `entity linking` 做
- `qualifiers` 必须是对象，不允许输出数组或自由文本
- 无法确认的字段可以为 `null`
- 不允许输出 schema 外字段

---

## 八、抽取规则建议

### 8.1 `Evidence Finder` 规则

职责：

- 判断 evidence 是否包含结构化事实
- 输出候选 `fact_type`
- 标出 evidence 范围

判断优先级：

1. 数字 + 单位
2. 时间 + 指标
3. 主体 + 动作 + 对象
4. 榜单块 / 名单块
5. 定性趋势表达

---

### 8.2 `Fact Extractor` 规则

职责：

- 按 `fact_type` 模板提取字段
- 只输出明确支持内容
- 不做推理扩写

硬性要求：

- 不允许省略 `fact_type`
- 不允许没有 `predicate`
- 不允许 `qualifiers` 输出非 JSON 对象
- 同一 evidence 多事实时拆分输出多条记录

---

### 8.3 `Reviewer / Validator` 规则

职责：

- 判断字段是否被 evidence 明确支持
- 判断是否存在主体错配、时间错配、单位错配
- 输出 `PASS / REJECT / UNCERTAIN`

重点校验项：

- `value_num` 是否与 `value_text` 一致
- `unit` 是否与原文一致
- `time_expr` 是否来自原文
- `predicate` 是否合适
- `market_scope` 是否缺失
- 榜单事实是否误填 rank

---

## 九、审核建议

### 9.1 自动通过建议

以下情形可考虑进入 `AUTO_PASS`：

- 结构完整
- evidence 强支撑
- 数值、单位、时间都明确
- `confidence >= 0.90`
- 不存在与已有数据的明显冲突

---

### 9.2 必须人工审核的情形

以下情况建议强制人工审核：

- 金额类事实
- `MARKET_SHARE`
- `COMPETITIVE_RANKING`
- 含有 `yoy` / `qoq`
- 同一句出现多个数值
- 主体不唯一
- 时间口径疑似混合
- 定性趋势事实

---

## 十、开发顺序建议

最适合的落地顺序：

### Step 1
先把本文档中的 `fact_type` 和字段模板固化为配置文件或 Python 常量。

### Step 2
为每个 `fact_type` 写独立抽取 Prompt 模板。

### Step 3
为每个 `fact_type` 写审核规则和 `Reviewer / Validator` Prompt。

### Step 4
把 `qualifiers` 中的常见键做成白名单，避免字段漂移。

### Step 5
再实现 `entity linking` 与 `relation build`。

结论：

**没有这份字典，抽取一定漂；字典定住后，Prompt、审核、ORM、接口都能稳定下来。**

---

## 十一、最终建议

当前阶段，最重要的不是继续扩表，而是把以下三件事做实：

1. 固化 `fact_type`
2. 固化字段模板
3. 固化 `qualifiers` 白名单

这三件事稳定后，你的资讯颗粒化项目才真正具备工程可实施性。

建议本文档文件名为：

`fact_type 字典与抽取字段规范.md`
