# 资讯颗粒化提取系统改进计划

> 基于 Copilot 手动提取 10 篇佐敦涂料资讯的实践反思
> 日期: 2025-03-28

## 一、本次手动提取过程复盘

### 1.1 数据概览
- **输入**: 10 篇涂界(搜狐号)资讯，主题覆盖佐敦涂料的投资、排名、产能、合作等
- **产出**: 42 条事实原子，覆盖 8/9 种事实类型
- **未覆盖**: PRICE_CHANGE（原始文章不涉及价格变动数据）
- **失败**: 1 篇文章（Article 3）正文内容未能成功抓取

### 1.2 发现的关键问题

#### 问题 1: 搜狐文章正文提取噪音极大
- **现象**: fetch_webpage 返回的内容中，正文占比不到 5%，其余 95% 是搜狐的推荐流、广告、侧边栏等噪音
- **影响**: 浪费大量 token，增加处理时间，导致 Article 3 的正文完全丢失
- **现有系统的问题**: `importer.py` 使用 BeautifulSoup 提取正文，但对搜狐这类重度 JS 渲染 + 推荐流的页面，提取效果依赖 CSS 选择器的精确程度

#### 问题 2: 跨文档事实重复率高
- **现象**: 佐敦张家港投资 3.3 亿美元、产能 15.50 万吨、全球排名第 9 等核心数据在至少 3 篇文章中重复出现
- **影响**: 如果使用 LLM 管道逐篇提取，会产生大量重复事实原子，增加审核负担和数据库体积
- **当前去重策略**: `deduplicator.py` 通过 subject + predicate + object + time_expr 做相似度匹配，但对"佐敦"vs"佐敦涂料"vs"佐敦集团"的主体归一化不够

#### 问题 3: 同一事实的不同表述导致数值歧义
- **案例**: 佐敦张家港产能在不同文章中表述为"约 16 万吨"和"15.50 万吨"
- **案例**: 投资金额同时出现"21 亿元"和"3.3 亿美元（约 23.31 亿元）"
- **原因**: 涂料行业资讯习惯性四舍五入，同一数据的精确程度因文章定位不同而异

#### 问题 4: 汇总型文章的多主体提取困难
- **案例**: Article 4 的标题是"涂料行业投资汇总"，一篇文章涉及 7+ 个不同企业实体
- **影响**: 单次 LLM 调用的上下文中混杂多个实体，容易导致主体张冠李戴

#### 问题 5: 时间表达不统一
- **案例**: "达产后"、"截止目前"、"2025 年前三季度"、"2024 年底"
- **影响**: 时间归一化困难，无法直接排序或对比

---

## 二、改进建议

### 2.1 爬虫层 (importer.py) 改进

#### 建议 A: 搜狐专用 CSS 选择器
```python
# 针对 sohu.com 文章页面的正文提取优化
SOHU_CONTENT_SELECTORS = [
    'article.article',
    'div[data-role="original-title"] + div',
    '#mp-editor',
    '.article-content-wrap',
]
```
**优先级**: 高  
**工作量**: 约 2 小时  
**预期效果**: 正文提取成功率从约 80% 提升至 95%+

#### 建议 B: 添加 Readability 算法兜底
在 CSS 选择器失败时，使用 `readability-lxml` 或类似库做通用正文提取。

```bash
pip install readability-lxml
```

**优先级**: 中  
**工作量**: 约 1 小时

### 2.2 去重层改进 — 通用方案（不依赖行业特定映射）

> **｢废弃原方案｣**: 原建议 C 使用硬编码映射表（"佐敦涂料"→"佐敦"），换一批资讯内容就完全失效。以下是通用化改进。

#### 建议 C: 将 entity_merger 候选发现接入 pipeline 自动执行

**现状问题**: `entity_merger.get_merge_suggestions()` 和 `entity_analyzer.analyze_entities()` 目前**不在 pipeline 中自动执行**，只能通过 CLI 或 Web 手动触发。这意味着每批文档处理完后，大量同义实体碎片化存在（"阿里巴巴集团""阿里巴巴""阿里"作为 3 个独立实体），去重和链接都无法利用它们的同一性。

**改进方案**: 在 `pipeline.py` 的 `process_document()` 末尾增加一个轻量级步骤——**仅执行候选发现 + 高置信度自动合并，不触发完整的 LLM 分析**：

```python
# pipeline.py — process_document() 末尾新增
from app.services.entity_merger import get_merge_suggestions, merge_entities

def _auto_merge_high_confidence(doc_id):
    """处理完一篇文档后，自动合并 contain_score ≥ 0.98 的实体对"""
    candidates = get_merge_suggestions()
    auto_merge = [c for c in candidates if c['score'] >= 0.98]
    for pair in auto_merge:
        merge_entities(pair['primary_id'], pair['secondary_id'],
                       note=f"pipeline 自动合并(score={pair['score']:.2f})")
    return len(auto_merge)
```

**关键设计**: 
- 阈值 ≥ 0.98 只合并几乎确定相同的实体（如"立邦涂料有限公司"和"立邦涂料"），不会误合
- 0.70-0.97 的候选仍写入 `entity_merge_task` 表等待人工审核
- 不调用 LLM，不增加 token 消耗
- 任何行业的资讯都适用，因为 `entity_utils.normalize()` 的去括号+去法人后缀规则本身就是通用的

**优先级**: 高  
**工作量**: 约 2 小时

#### 建议 C2: 在 deduplicator 指纹中引入 entity_id 归一化

**现状问题**: `deduplicator.py` 的指纹用 `normalize(subject_text)` 做字符串归一化，但"佐敦涂料"和"Jotun"归一化后仍然不同。而此时 `entity_linker` 已经完成链接，两者可能指向同一个 `entity_id`。

**改进方案**: 在跨文档去重时，如果两条事实的 `subject_entity_id` 相同，视为同一主体，不再依赖 subject_text 的字面匹配。

```python
# deduplicator.py — _build_fingerprint() 修改
def _build_fingerprint(fact, use_entity_id=False):
    if use_entity_id and fact.get('subject_entity_id'):
        subject_part = f"eid:{fact['subject_entity_id']}"
    else:
        subject_part = normalize(fact.get('subject_text', ''))
    # ... 其余逻辑不变
```

**关键设计**:
- 通过已有的 entity_id 做归一化，完全不依赖行业映射表
- 如果 entity_linker 没链接上（subject_entity_id 为空），退回字面匹配
- 适用于任何行业——只要 entity_linker 能识别，去重就能受益

**优先级**: 高  
**工作量**: 约 1.5 小时

#### 建议 D: 数值模糊匹配（保留，与行业无关）
当两个事实原子的主体匹配（通过 entity_id 或 normalized text）且 predicate 相似时，如果 value_num 差异在 5% 以内，标记为潜在重复。

```python
def is_value_similar(v1, v2, tolerance=0.05):
    if v1 is None or v2 is None:
        return v1 == v2
    return abs(v1 - v2) / max(abs(v1), abs(v2)) <= tolerance
```

**优先级**: 中  
**工作量**: 约 1 小时

#### 建议 D2: entity_utils 常量表可配置化

**现状问题**: `entity_utils.py` 中 `COMPANY_SUFFIXES`（涂料/化工/材料）、`GEO_QUALIFIERS`（16 个城市）等都是硬编码的涂料行业词表。切换到汽车行业时，"汽车""配件""动力"等后缀不在词表中，`infer_entity_type()` 全部返回 UNKNOWN。

**改进方案**: 将 `entity_utils.py` 的常量移入 `config.yaml`，按领域配置：

```yaml
# config.yaml
entity_rules:
  company_suffixes: ["有限公司", "集团", "股份", "控股"]  # 通用后缀
  industry_suffixes: ["涂料", "化工", "材料", "新材料"]    # 行业特定，可替换
  geo_qualifiers: []  # 空 = 不限制，从 DB 中已有实体动态推导
  skip_names: ["中国", "全球", "该公司", "行业"]           # 通用停用词
```

**优先级**: 中  
**工作量**: 约 2 小时

### 2.3 提取层 (fact_extractor.py / full_extractor.py) 改进

#### 建议 E: 多主体文章的分段提取
对于检测到多个企业实体的文章，先按段落/实体分组，再分批提交给 LLM 提取。

**检测规则**: 如果 evidence_finder 返回的 evidence 中包含 3+ 个不同企业名称，触发分段模式

**优先级**: 中  
**工作量**: 约 4 小时

#### 建议 F: 增加币种/单位自动转换字段
在 fact atom 中增加 `value_num_cny` 字段，自动将非人民币数值按当期汇率转为人民币，方便横向比较。

**优先级**: 低  
**工作量**: 约 2 小时

### 2.4 Prompt 改进

#### 建议 G: 强化时间表达规范
在 `fact_extractor_full.txt` 中增加时间归一化规则：
- "达产后" → 保留原文，但在 qualifiers 中注明 `时间类型: 预期`
- "截止目前" → 替换为文章发布日期
- "前三季度" → 规范为 "YYYY年1-9月"

**优先级**: 中  
**工作量**: 约 1 小时

#### 建议 H: 增加"精确度"限定词
要求 LLM 在 qualifiers 中标注数据精确度：
- `exact`: 精确数字（如"15.50 万吨"）
- `approximate`: 约数（如"约 16 万吨"）
- `estimated`: 预估值（如"预计可实现年产值 100 亿元"）

**优先级**: 高  
**工作量**: 约 0.5 小时

### 2.5 审核层 (reviewer.py) 改进

#### 建议 I: 跨文档一致性校验（利用已有 entity_analyzer）

**现状问题**: `entity_analyzer.py` 已有 3-evidence 架构（事实证据 + 名称相似度 + Web 搜索），能自动分析实体间关系并在 ≥0.75 置信度时自动确认。但它目前不在 pipeline 中，且只做实体关系分析，不做事实一致性校验。

**改进方案**: 
1. 在 reviewer 阶段增加跨文档比对：查询 DB 中同一 `subject_entity_id` + 相似 `predicate` 的历史事实
2. 如果新提取值与历史值差异超过 10%，标记为"待人工审核"并附注冲突记录
3. 将 `entity_analyzer.analyze_entities()` 作为批处理后置任务（每日/每批执行），而不是逐文档执行（太慢）

**关键设计**: 通过 entity_id 而非 subject_text 做匹配——实体链接后的 ID 是跨行业通用的

**优先级**: 高  
**工作量**: 约 4 小时

### 2.6 数据质量监控

#### 建议 J: 提取质量 Dashboard
在 Web 审核界面增加质量指标页面：
- 每批次提取的事实数 / 通过率 / 重复率
- 按来源站点的正文提取成功率
- 按事实类型的置信度分布
- 跨文档重复事实 TOP 10

**优先级**: 低  
**工作量**: 约 6 小时

---

## 三、优先实施路线图

### Phase 1（1-2 天）— 快速见效
| 序号 | 建议 | 优先级 | 工作量 |
|------|------|--------|--------|
| A | 搜狐专用 CSS 选择器 | 高 | 2h |
| H | 增加"精确度"限定词 | 高 | 0.5h |
| G | 时间表达规范 | 中 | 1h |

### Phase 2（3-5 天）— 去重增强（通用化）
| 序号 | 建议 | 优先级 | 工作量 |
|------|------|--------|--------|
| C | entity_merger 候选发现接入 pipeline | 高 | 2h |
| C2 | deduplicator 指纹用 entity_id 归一化 | 高 | 1.5h |
| D | 数值模糊匹配 | 中 | 1h |
| D2 | entity_utils 常量表可配置化 | 中 | 2h |
| I | 跨文档一致性校验（利用 entity_analyzer） | 高 | 4h |

### Phase 3（1 周）— 提取增强
| 序号 | 建议 | 优先级 | 工作量 |
|------|------|--------|--------|
| B | Readability 算法兜底 | 中 | 1h |
| E | 多主体文章分段提取 | 中 | 4h |
| F | 币种自动转换 | 低 | 2h |
| J | 质量 Dashboard | 低 | 6h |

---

## 四、总结

本次手动提取实践暴露了系统在 **正文提取质量**、**pipeline 集成度**、**行业通用性** 三个环节的薄弱点。其中：

1. **搜狐正文提取** 是最紧迫的问题，直接影响数据输入质量
2. **pipeline 自动化集成** 是最高性价比的改进——系统已有 `entity_merger` 和 `entity_analyzer` 两个强力模块（支持 LLM + 人工审核 + Web 搜索），但当前完全没有接入自动管线。只需在 `pipeline.py` 末尾加入高置信度自动合并，就能显著减少实体碎片
3. **行业通用性** 是长期可持续的关键——`entity_utils.py` 常量和 `entity_merge.txt` prompt 都硬编码了涂料行业术语，切换到其他行业（汽车/半导体/食品...）时会大面积失效。改为 `config.yaml` 驱动 + 通用 prompt 模板即可解决
4. **精确度标注** 是成本最低但价值很高的改进，一行 prompt 改动就能显著提升数据可用性

建议按 Phase 1 → Phase 2 → Phase 3 的顺序逐步实施。
