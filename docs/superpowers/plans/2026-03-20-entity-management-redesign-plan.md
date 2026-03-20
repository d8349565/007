# 实体管理界面重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将实体管理页面拆分为「层级视图」（树状）和「关系图谱」（force）两个 Tab，支持切换

**Architecture:**
- 在 `review_app.py` 中新增 `/api/entity/hierarchy` 接口，从 `entity_relation` 构建层级树数据
- 新增 `hierarchy.html` 模板，使用 D3 `d3.tree()` 从上往下渲染
- 改造 `graph.html`：顶部增加 Tab 栏，Tab 切换 show/hide 两个画布 div

**Tech Stack:** D3 v7 (tree layout), Python/Flask, Jinja2, SQLite

---

## 文件结构

```
app/
  web/
    review_app.py          # 新增 /api/entity/hierarchy 路由
    templates/
      hierarchy.html      # 新增：层级视图（tree 布局）
      graph.html          # 修改：内嵌 Tab 容器，包裹层级视图+关系图谱两个画布
```

---

## Task 1: 新增 `/api/entity/hierarchy` 接口

**Files:**
- Modify: `app/services/query.py` — 新增 `get_entity_hierarchy()` 函数
- Modify: `app/web/review_app.py` — 新增 `/api/entity/hierarchy` 路由

- [ ] **Step 1: 在 query.py 新增 get_entity_hierarchy() 函数**

在 `query.py` 末尾添加：

```python
def get_entity_hierarchy() -> dict:
    """
    从 entity_relation 构建层级树数据。
    返回 {roots: [...], nodes: [...]}。
    roots: 顶层实体（无 parent 的）
    nodes: 所有实体的扁平列表（含 parent_id 和 relation_type）
    """
    conn = get_connection()
    try:
        # 读取所有实体关系
        rows = conn.execute("""
            SELECT r.from_entity_id, r.to_entity_id, r.relation_type,
                   e1.canonical_name AS from_name, e1.entity_type AS from_type,
                   e2.canonical_name AS to_name, e2.entity_type AS to_type
            FROM entity_relation r
            JOIN entity e1 ON e1.id = r.from_entity_id
            JOIN entity e2 ON e2.id = r.to_entity_id
        """).fetchall()

        # 构建 parent_id -> children 映射
        children_map: dict[str, list] = {}
        all_entity_ids = set()
        entity_info: dict = {}

        for r in rows:
            parent_id = r["from_entity_id"]
            child_id = r["to_entity_id"]
            all_entity_ids.add(parent_id)
            all_entity_ids.add(child_id)
            entity_info[parent_id] = {"name": r["from_name"], "type": r["from_type"]}
            entity_info[child_id] = {"name": r["to_name"], "type": r["to_type"]}
            children_map.setdefault(parent_id, []).append({
                "id": child_id,
                "name": r["to_name"],
                "entity_type": r["to_type"],
                "relation_type": r["relation_type"],
            })

        # 找出根节点（出现在 from_entity_id 从未出现在 to_entity_id 的）
        to_ids = {r["to_entity_id"] for r in rows}
        root_ids = [eid for eid in all_entity_ids if eid not in to_ids]

        # 构建 roots 树（只取两层：root + direct children）
        roots = []
        for eid in root_ids:
            info = entity_info.get(eid, {"name": "未知", "type": "UNKNOWN"})
            children = children_map.get(eid, [])
            roots.append({
                "id": eid,
                "name": info["name"],
                "entity_type": info["type"],
                "relation_type": None,
                "children": children,
            })

        return {"roots": roots}
    finally:
        conn.close()
```

- [ ] **Step 2: 在 review_app.py 新增路由**

在 `graph_page()` 路由附近添加：

```python
@app.route("/api/entity/hierarchy")
def api_entity_hierarchy():
    """实体层级树数据 API"""
    data = get_entity_hierarchy()
    return jsonify(data)
```

- [ ] **Step 3: 验证接口返回正确数据**

Run: `cd "d:/Work/1、企划部/Python程序/2026年/资讯颗粒化收集" && python -c "from app.services.query import get_entity_hierarchy; import json; print(json.dumps(get_entity_hierarchy(), ensure_ascii=False, indent=2))"`

Expected: 输出包含 roots 和 nodes 的 JSON

- [ ] **Step 4: Commit**

```bash
git add app/services/query.py app/web/review_app.py
git commit -m "feat(api): 添加 /api/entity/hierarchy 层级数据接口"
```

---

## Task 2: 创建 hierarchy.html 层级视图模板

**Files:**
- Create: `app/web/templates/hierarchy.html`

- [ ] **Step 1: 创建 hierarchy.html**

完整文件内容：

```html
{% extends "_base.html" %}
{% set active = 'graph' %}
{% block title %}层级视图 — 资讯颗粒化{% endblock %}
{% block container_class %}container-fluid{% endblock %}

{% block head %}
<style>
.hierarchy-page { display: flex; height: calc(100vh - 52px); overflow: hidden; position: relative; }
.hierarchy-canvas-wrap {
  flex: 1; position: relative;
  background-color: #1a1a1a;
  background-image: radial-gradient(#333 1.2px, transparent 1.2px);
  background-size: 24px 24px;
  overflow: hidden;
}
.hierarchy-canvas-wrap svg { width: 100%; height: 100%; display: block; user-select: none; -webkit-user-select: none; }

/* Node styles */
.node-group { cursor: pointer; }
.node-rect {
  rx: 8; ry: 8;
  stroke-width: 2.5;
  transition: stroke 0.15s, stroke-width 0.15s;
}
.node-rect:hover { stroke: #fafafa !important; stroke-width: 3.5; }
.node-label { font-size: 12px; fill: #a1a1a1; font-family: system-ui, sans-serif; pointer-events: none; }
.node-sublabel { font-size: 10px; fill: #6b6b6b; font-family: system-ui, sans-serif; pointer-events: none; }

/* Edge styles */
.link { fill: none; stroke-width: 1.5; }
.link-SUBSIDIARY { stroke: #444; stroke-dasharray: none; }
.link-SHAREHOLDER { stroke: #444; stroke-dasharray: 5,3; }
.link-JV { stroke: #444; stroke-dasharray: 2,3; }
.link-label { font-size: 10px; fill: #6b6b6b; font-family: system-ui, sans-serif; }

/* Empty state */
.hierarchy-empty {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  text-align: center; color: #6b6b6b;
}
.hierarchy-empty p { font-size: 15px; margin-bottom: 6px; }
.hierarchy-empty a { color: #3b82f6; font-size: 13px; }

/* Tooltip */
.hierarchy-tooltip {
  position: absolute; background: rgba(40,40,40,0.96); border: 1px solid #404040;
  border-radius: 8px; padding: 10px 14px; font-size: 12px; color: #a1a1a1;
  pointer-events: none; z-index: 100; max-width: 240px; display: none;
  box-shadow: 0 4px 16px rgba(0,0,0,0.4);
}
.hierarchy-tooltip .tip-name { font-weight: 600; color: #fafafa; font-size: 13px; margin-bottom: 4px; }
.hierarchy-tooltip .tip-type { color: #6b6b6b; font-size: 11px; }
</style>
{% endblock %}

{% block content %}
<div class="hierarchy-page">
  <div class="hierarchy-canvas-wrap">
    <svg id="hierarchySvg"></svg>
    <div class="hierarchy-empty" id="hierarchyEmpty" style="display:none;">
      <p>暂无层级结构数据</p>
      <p style="font-size:12px;color:#94a3b8;">需要在实体管理中添加公司层级关系</p>
      <a href="/manage">前往实体管理 →</a>
    </div>
    <div class="hierarchy-tooltip" id="hierarchyTooltip">
      <div class="tip-name" id="tipName"></div>
      <div class="tip-type" id="tipType"></div>
    </div>
  </div>
</div>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>
const TYPE_COLORS = {
  'COMPANY': '#3b82f6',
  'PROJECT': '#f472b6',
  'GROUP': '#f59e0b',
  'REGION': '#94a3b8',
  'COUNTRY': '#a78bfa',
  'UNKNOWN': '#60a5fa',
};

const TYPE_ZH = {
  'COMPANY': '企业', 'PROJECT': '项目', 'GROUP': '群体/排名',
  'REGION': '地区', 'COUNTRY': '国家', 'UNKNOWN': '未知'
};

let hierarchyData = null;

async function loadHierarchy() {
  try {
    const resp = await fetch('/api/entity/hierarchy');
    hierarchyData = await resp.json();
    renderHierarchy();
  } catch (e) {
    console.error('Failed to load hierarchy:', e);
  }
}

function buildTree(roots) {
  // roots 已经是嵌套 children 的结构，直接返回
  return roots;
}

function renderHierarchy() {
  const svg = d3.select('#hierarchySvg');
  svg.selectAll('*').remove();

  const container = svg.node().parentElement;
  const width = container.clientWidth;
  const height = container.clientHeight;

  if (!hierarchyData || !hierarchyData.roots || hierarchyData.roots.length === 0) {
    document.getElementById('hierarchyEmpty').style.display = 'block';
    return;
  }
  document.getElementById('hierarchyEmpty').style.display = 'none';

  svg.attr('width', width).attr('height', height).attr('viewBox', `0 0 ${width} ${height}`);

  const NODE_WIDTH = 160;
  const NODE_HEIGHT = 60;
  const HORIZ_SPACING = 40;
  const VERT_SPACING = 80;

  // D3 tree layout
  const stratify = d3.stratify()
    .id(d => d.id)
    .parentId(d => null); // roots have no parent

  // Build hierarchy from flat children list
  function buildHierarchyData(nodes) {
    if (!nodes || nodes.length === 0) return [];
    const nodeMap = {};
    const roots = [];
    nodes.forEach(n => { nodeMap[n.id] = {...n, children: []}; });
    nodes.forEach(n => {
      if (!n.parent_id) {
        roots.push(nodeMap[n.id]);
      } else if (nodeMap[n.parent_id]) {
        nodeMap[n.parent_id].children.push(nodeMap[n.id]);
      }
    });
    return roots;
  }

  // Flatten roots + children into nodes for d3.hierarchy
  function flatten(nodes, parentId = null) {
    let result = [];
    for (const n of nodes) {
      result.push({...n, parent_id: parentId});
      if (n.children && n.children.length > 0) {
        result = result.concat(flatten(n.children, n.id));
      }
    }
    return result;
  }

  const flatData = hierarchyData.roots.map(r => ({...r, parent_id: null}));
  const flatNodes = flatten(hierarchyData.roots);

  // Build d3 hierarchy
  const rootNode = d3.hierarchy({id: 'virtual-root', children: hierarchyData.roots, parent_id: null}, d => d.children);
  rootNode.x0 = width / 2;
  rootNode.y0 = 60;

  const treeLayout = d3.tree()
    .nodeSize([NODE_WIDTH + HORIZ_SPACING, NODE_HEIGHT + VERT_SPACING])
    .separation((a, b) => a.parent === b.parent ? 1 : 1.2);

  treeLayout(rootNode);

  const g = svg.append('g');
  svg.call(d3.zoom().scaleExtent([0.1, 3]).on('zoom', e => g.attr('transform', e.transform)));

  // Links
  const linkGroup = g.append('g');
  rootNode.links().forEach(link => {
    const relType = link.target.data.relation_type || '';
    const dashClass = relType === 'SHAREHOLDER' ? 'link-SHAREHOLDER' :
                      relType === 'JV' ? 'link-JV' : 'link-SUBSIDIARY';
    linkGroup.append('path')
      .attr('class', `link ${dashClass}`)
      .attr('d', `M${link.source.x},${link.source.y + NODE_HEIGHT / 2} V${(link.source.y + link.target.y) / 2} H${link.target.x} V${link.target.y - NODE_HEIGHT / 2}`)
      .attr('stroke', '#444');
    // Edge label
    if (relType) {
      const midX = (link.source.x + link.target.x) / 2;
      const midY = (link.source.y + link.target.y) / 2;
      linkGroup.append('text')
        .attr('class', 'link-label')
        .attr('x', midX)
        .attr('y', midY)
        .attr('text-anchor', 'middle')
        .text(relType);
    }
  });

  // Nodes
  const nodeGroup = g.append('g');
  const tooltip = document.getElementById('hierarchyTooltip');
  const tipName = document.getElementById('tipName');
  const tipType = document.getElementById('tipType');

  function renderNodes(node) {
    if (!node.children || node.depth === 0) return; // skip virtual root and leaf nodes

    const color = TYPE_COLORS[node.data.entity_type] || TYPE_COLORS['UNKNOWN'];

    const nodeEl = nodeGroup.append('g')
      .attr('class', 'node-group')
      .attr('transform', `translate(${node.x - NODE_WIDTH / 2},${node.y - NODE_HEIGHT / 2})`)
      .on('dblclick', () => {
        if (!node.data.is_text_node) {
          window.location.href = '/entity/' + node.data.id;
        }
      })
      .on('mouseenter', (ev) => {
        tipName.textContent = node.data.name;
        tipType.textContent = TYPE_ZH[node.data.entity_type] || node.data.entity_type || '未知';
        tooltip.style.display = 'block';
        tooltip.style.left = (node.x + NODE_WIDTH / 2 + 10) + 'px';
        tooltip.style.top = (node.y - NODE_HEIGHT / 2) + 'px';
      })
      .on('mouseleave', () => {
        tooltip.style.display = 'none';
      });

    nodeEl.append('rect')
      .attr('class', 'node-rect')
      .attr('width', NODE_WIDTH)
      .attr('height', NODE_HEIGHT)
      .attr('fill', color + '22')
      .attr('stroke', color)
      .attr('stroke-width', 2.5);

    // Label
    const label = node.data.name.length > 10 ? node.data.name.substring(0, 10) + '…' : node.data.name;
    nodeEl.append('text')
      .attr('class', 'node-label')
      .attr('x', NODE_WIDTH / 2)
      .attr('y', NODE_HEIGHT / 2 - 4)
      .attr('text-anchor', 'middle')
      .text(label);

    // Relation type sublabel
    if (node.data.relation_type) {
      nodeEl.append('text')
        .attr('class', 'node-sublabel')
        .attr('x', NODE_WIDTH / 2)
        .attr('y', NODE_HEIGHT / 2 + 12)
        .attr('text-anchor', 'middle')
        .text(node.data.relation_type);
    }
  }

  rootNode.descendants().forEach(renderNodes);
}

// Init
loadHierarchy();
window.addEventListener('resize', () => { if (hierarchyData) renderHierarchy(); });
</script>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add app/web/templates/hierarchy.html
git commit -m "feat(web): 添加层级视图 hierarchy.html"
```

---

## Task 3: 改造 graph.html — 添加 Tab 切换结构

**Files:**
- Modify: `app/web/templates/graph.html`

- [ ] **Step 1: 修改 graph.html，在 {% block content %} 开头添加 Tab 栏**

在 `<div class="graph-page">` 之前添加：

```html
<!-- Entity View Tabs -->
<div class="entity-view-tabs" style="display:flex;gap:0;padding:0 16px;background:#1e1e1e;border-bottom:1px solid #333;height:44px;align-items:center;">
  <button class="ev-tab active" data-view="hierarchy" onclick="switchEntityView('hierarchy')" style="background:none;border:none;color:#a1a1a1;font-size:13px;font-weight:500;padding:8px 16px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s;">层级视图</button>
  <button class="ev-tab" data-view="graph" onclick="switchEntityView('graph')" style="background:none;border:none;color:#6b6b6b;font-size:13px;font-weight:500;padding:8px 16px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s;">关系图谱</button>
</div>
```

在 `</style>` 之前（在 block head 的 style 末尾）添加 Tab 样式：

```css
/* Entity View Tabs */
.ev-tab { outline: none; }
.ev-tab.active { color: #fafafa; border-bottom-color: #3b82f6; }
.ev-tab:hover:not(.active) { color: #a1a1a1; }

/* 两个视图画布的显示切换 */
.view-panel { display: none; }
.view-panel.active { display: flex; }
```

- [ ] **Step 2: 将原有 graph-page div 包裹进 view-panel**

将 `<div class="graph-page">` 修改为：

```html
<div class="view-panel" id="graphViewPanel">
```

在 `</div>` (graph-page 闭合) 之后添加：

```html
</div>
```

- [ ] **Step 3: 添加 /hierarchy 路由指向 hierarchy.html**

在 `review_app.py` 的 `graph_page()` 附近添加：

```python
@app.route("/hierarchy")
def hierarchy_page():
    """实体层级视图页面"""
    return render_template("hierarchy.html")
```

- [ ] **Step 4: 在 graph.html 末尾添加 hierarchy 视图容器和 switchEntityView 函数**

在 `{% endblock %}` 之前添加：

```html
<!-- Hierarchy view panel -->
<div class="view-panel" id="hierarchyViewPanel" style="display:none; flex-direction: column;">
  <iframe src="/hierarchy" style="flex:1; border:none; width:100%; height:100%;" id="hierarchyFrame"></iframe>
</div>

<script>
function switchEntityView(view) {
  document.querySelectorAll('.ev-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === view);
    btn.style.color = btn.dataset.view === view ? '#fafafa' : '#6b6b6b';
    btn.style.borderBottomColor = btn.dataset.view === view ? '#3b82f6' : 'transparent';
  });
  document.getElementById('graphViewPanel').classList.toggle('active', view === 'graph');
  document.getElementById('hierarchyViewPanel').classList.toggle('active', view === 'hierarchy');
}
</script>
```

- [ ] **Step 5: 验证页面**

Run: `cd "d:/Work/1、企划部/Python程序/2026年/资讯颗粒化收集" && python -m app.main web`
访问 `http://localhost:5000/graph`，确认 Tab 显示，切换到层级视图可加载。

- [ ] **Step 6: Commit**

```bash
git add app/web/templates/graph.html app/web/review_app.py
git commit -m "feat(web): 改造 graph.html 添加层级视图/关系图谱 Tab 切换"
```

---

## Task 4: 完善层级视图（树节点展开交互）

**Files:**
- Modify: `app/web/templates/hierarchy.html`

- [ ] **Step 1: 实现点击节点展开/收起子节点**

替换 hierarchy.html 中 renderHierarchy() 的节点渲染部分，采用以下 D3 模式：

**数据预处理**：在调用 `d3.tree()` 之前，克隆 hierarchyData，将所有非根节点的 children 置为空（表示收起状态），只保留根节点的一级子节点可见。

**expanded 集合**：维护一个 `Set<string>` `expandedNodeIds`，初始时包含所有根节点 ID。

**点击处理**：在节点 click handler 中，toggle 该节点 ID 是否在 `expandedNodeIds` 中：
```javascript
.on('click', (ev, d) => {
  ev.stopPropagation();
  const nid = d.data.id;
  if (expandedNodeIds.has(nid)) {
    expandedNodeIds.delete(nid);
  } else {
    expandedNodeIds.add(nid);
  }
  renderHierarchyWithState();
});
```

**renderHierarchyWithState() 函数**：
```javascript
function renderHierarchyWithState() {
  // Clone hierarchy data and filter children based on expandedNodeIds
  function filterChildren(node) {
    if (!node.children) return node;
    const visibleChildren = node.children.filter(c => expandedNodeIds.has(c.id));
    return {...node, children: visibleChildren.map(filterChildren)};
  }
  const filteredRoots = hierarchyData.roots.map(filterChildren);
  const virtualRoot = {id: 'virtual-root', children: filteredRoots};
  const rootNode = d3.hierarchy(virtualRoot, d => d.children);
  // Re-run tree layout and render...
}
```

**展开图标**：在有子节点的节点上追加一个展开/收起的视觉指示（如 +/- 图标或旋转箭头），在节点 group 右上角。

- [ ] **Step 2: Commit**

```bash
git add app/web/templates/hierarchy.html
git commit -m "feat(web): hierarchy.html 支持节点展开/收起交互"
```

---

## 验收标准

1. 访问 `/graph` 页面，顶部显示「层级视图」和「关系图谱」两个 Tab ✓
2. 默认展示「层级视图」（第一层 Tab active） ✓
3. 层级视图以树状从顶往下排列，节点颜色按 entity_type ✓
4. 点击「关系图谱」Tab，切换显示原有 force 布局图谱 ✓
5. Tab 切换不刷新页面 ✓
6. 双击层级视图节点跳转到 entity_timeline 页面 ✓
7. `/api/entity/hierarchy` 返回正确的 JSON 格式 ✓
