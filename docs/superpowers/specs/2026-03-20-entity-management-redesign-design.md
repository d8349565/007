# 实体管理界面重构设计

## 1. 背景与目标

### 现状问题
- graph.html 使用 D3 force simulation 力导向布局，节点随机浮动，无法表达公司层级结构
- 层级关系（entity_relation）与业务关系（fact_atom）混在同一画布中，无结构区分
- 交互感差，布局混乱，无法按「母公司→子公司→孙公司」层次排列

### 目标
将实体管理界面拆分为两个独立视图：
1. **层级视图**：展示公司股权/子公司树状结构，从上往下布局，支持点击展开
2. **关系图谱**：展示业务关系（投资、合作、竞争等），保留现有 force 布局

两个视图通过顶部 Tab 切换，数据保持同步。

---

## 2. 设计决策

| 问题 | 选择 |
|------|------|
| 视图组织方式 | Tab 切换（层级视图 / 关系图谱），顶部 Tab 栏 |
| 层级方向 | 从上往下（根节点在上） |
| 初始加载 | 展示所有根节点公司 + 直接子公司（两层），渐进展开 |
| 展开方式 | 点击节点直接展开下一级子节点 |
| 节点大小 | 统一大小，按 entity_type 区分颜色/形状 |
| 关系图谱布局 | 保留现有 D3 Force 布局，不做大幅改动 |

---

## 3. 层级视图

### 3.1 数据来源

- 数据表：`entity_relation`
- 关系类型：`SUBSIDIARY`（子公司）、`SHAREHOLDER`（股东）、`JV`（合资）
- 根节点定义：无 incoming 边的实体（作为 to_entity 但非 from_entity 的公司）

### 3.2 新增 API

**GET /api/entity/hierarchy**

返回示例：
```json
{
  "roots": [
    {
      "id": "entity-uuid-1",
      "name": "中远海运国际（香港）有限公司",
      "entity_type": "COMPANY",
      "relation_type": null,
      "children": [
        {
          "id": "entity-uuid-2",
          "name": "上海中远海运油漆工程有限公司",
          "entity_type": "COMPANY",
          "relation_type": "SUBSIDIARY",
          "children": []
        }
      ]
    }
  ]
}
```

### 3.3 渲染引擎

使用 D3 `d3.tree()` 布局：
- `d3.hierarchy()` 构建树数据
- `d3.tree().nodeSize([160, 120])` 设置节点间距（宽×高）
- 画布可缩放（d3.zoom），可拖拽节点（drag）

### 3.4 节点样式

| entity_type | 形状 | 颜色 |
|-------------|------|------|
| COMPANY | 圆角矩形 | `#3b82f6`（蓝） |
| PROJECT | 圆角矩形 | `#f472b6`（粉） |
| REGION | 圆角矩形 | `#94a3b8`（灰） |
| COUNTRY | 圆角矩形 | `#a78bfa`（紫） |
| 其他 | 圆角矩形 | `#60a5fa`（默认蓝） |

节点内显示：
- 主标题：公司简称（超过 10 字符截断）
- 副标题：关系类型（SUBSIDIARY / SHAREHOLDER / JV）

### 3.5 边样式

- 实线：`SUBSIDIARY`（子公司关系）
- 虚线：`SHAREHOLDER`（股权关系）
- 点划线：`JV`（合资关系）
- 边上标注关系类型文字

### 3.6 展开交互

- **初始状态**：所有根节点展开一层子节点
- **点击节点**：展开/收起其直接子节点（toggle）
- **悬停**：节点边框高亮，显示 tooltip（含全称、类型、关联事实数）
- **双击节点**：跳转至该实体的 entity_timeline 页面

### 3.7 布局算法

```
根节点 Y = 60（顶部固定）
同层节点 X 平均分布
子节点 X = 父节点 X ± 兄弟节点宽度/2
节点间距：水平 160px，垂直 100px
```

---

## 4. 关系图谱（改造）

### 4.1 改造内容

- 新增顶部 Tab 栏，与层级视图共用容器
- 现有 force 布局保留，不做核心改动
- 点击节点行为不变（显示详情侧边栏）
- Tab 切换后重新渲染图表

### 4.2 顶部 Tab 结构

```html
<div class="entity-tabs">
  <button class="tab-btn active" data-view="hierarchy">层级视图</button>
  <button class="tab-btn" data-view="graph">关系图谱</button>
</div>
```

Tab 样式与当前页面风格统一（深色主题）。

---

## 5. 数据层注意事项

### 5.1 entity_relation 当前数据

现有数据较少（5 条关系），涉及 5 个实体。重构后层级视图可正常展示，但数据量有限。

### 5.2 数据补全建议

后续应通过实体合并服务和人工补充，丰富 `entity_relation` 数据，以便层级视图有更完整的展示。

---

## 6. 文件变更清单

### 新增文件
- `app/web/templates/hierarchy.html` — 层级视图模板
- `app/web/api/entity_hierarchy.py` 或在 `review_app.py` 中新增路由

### 修改文件
- `app/web/review_app.py` — 新增 `/api/entity/hierarchy` 路由
- `app/web/templates/graph.html` — 移除独立 HTML 结构，改为嵌入 Tab 容器（复用同一个页面）
- `app/web/templates/_base.html` — 可选：如有共享 Tab 样式则提取

### 暂不修改
- `app/services/entity_linker.py`（逻辑无需改动）
- `app/services/entity_merger.py`（逻辑无需改动）

---

## 7. 验收标准

1. 访问 `/graph` 页面，顶部显示「层级视图」和「关系图谱」两个 Tab
2. 默认展示「层级视图」，树状结构从上往下排列
3. 点击节点可展开/收起子节点
4. 切换到「关系图谱」Tab，显示原有 force 布局图谱
5. Tab 切换不刷新页面，图表正确渲染
6. 层级视图节点颜色与 entity_type 对应表一致
7. 双击层级视图节点可跳转至 entity_timeline 页面
