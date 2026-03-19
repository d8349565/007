# /review 页面重新设计规格书

> **日期:** 2026-03-19
> **状态:** 设计中

## 1. 设计目标

优化 `/review` 页面表格展示，在保留全部字段信息的前提下，提升信息可读性和审核效率。

## 2. 核心问题

- 当前 15 列表格横向挤压严重，核心事实不突出
- 单元格内容被截断，难以判断数据质量
- 操作按钮在最后一列，需要滚动才能操作

## 3. 解决方案

### 3.1 紧凑表格布局

**保留列（7列）：**

| 列名 | 宽度 | 说明 |
|------|------|------|
| # | 40px | 序号 |
| 类型 | 90px | 事实类型 Badge |
| 主体 | 自适应 | 主语（核心信息） |
| 谓词 | 自适应 | 谓词 |
| 数值/单位 | 120px | value_num + unit 合并显示 |
| 状态 | 80px | 审核状态 Badge |
| 操作 | 60px | 下拉菜单入口 |

**移至气泡详情（点击 📎 图标）：**
- 客体 (object_text)
- 货币 (currency)
- 时间 (time_expr)
- 地点 (location_text)
- 置信度 (confidence_score)
- 限定词 (qualifier_json)
- 证据原文 (evidence_text)

### 3.2 气泡详情弹窗

点击"📎"图标显示气泡弹窗：

```html
┌────────────────────────────────────┐
│ 客体: 船舶涂料项目                 │
│ 货币: CNY                          │
│ 时间: 2024年                       │
│ 地点: 全国                         │
│ 置信度: 0.92                       │
│ 限定词: {"metric_name": "销售..."} │
│ 证据: "根据财报显示，..."          │
└────────────────────────────────────┘
```

**交互：**
- 点击 📎 切换显示/隐藏
- 点击页面其他区域关闭气泡
- 气泡位置：位于 📎 图标下方或右侧，根据空间自动调整

### 3.3 操作下拉菜单

使用 HTML `<select>` 下拉菜单：

```html
<select onchange="this.value && this.form.submit()">
  <option value="">操作...</option>
  <option value="HUMAN_PASS">✓ 通过</option>
  <option value="HUMAN_REJECTED">✗ 拒绝</option>
</select>
```

### 3.4 响应式设计

- 桌面端：表格紧凑展示
- 移动端：水平滚动，支持触摸操作

## 4. 技术实现

### 4.1 HTML 结构

```html
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>类型</th>
      <th>主体</th>
      <th>谓词</th>
      <th>数值</th>
      <th>状态</th>
      <th>详情</th>
      <th>操作</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td><span class="badge">财务指标</span></td>
      <td>船舶涂料</td>
      <td>销售收入为</td>
      <td>98.12 亿元</td>
      <td><span class="badge">待审核</span></td>
      <td>
        <button class="btn-detail" onclick="toggleDetail(this)">📎</button>
        <div class="detail-popup hidden">
          <!-- 详情内容 -->
        </div>
      </td>
      <td>
        <select onchange="if(this.value) this.form.submit()">
          <option value="">—</option>
          <option value="HUMAN_PASS">通过</option>
          <option value="HUMAN_REJECTED">拒绝</option>
        </select>
      </td>
    </tr>
  </tbody>
</table>
```

### 4.2 CSS 样式

```css
table { width: 100%; border-collapse: collapse; }
td, th { padding: 8px 12px; font-size: 13px; vertical-align: top; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; }
.detail-popup {
  position: absolute;
  background: white;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  z-index: 100;
  min-width: 250px;
}
.detail-popup.hidden { display: none; }
.detail-row { margin-bottom: 6px; font-size: 12px; }
.detail-label { color: #666; margin-right: 8px; }
```

### 4.3 JavaScript

```javascript
function toggleDetail(btn) {
  const popup = btn.nextElementSibling;
  popup.classList.toggle('hidden');
  // 点击其他区域关闭
  document.addEventListener('click', function closePopup(e) {
    if (!btn.contains(e.target) && !popup.contains(e.target)) {
      popup.classList.add('hidden');
      document.removeEventListener('click', closePopup);
    }
  });
}
```

## 5. 文件变更

- `app/web/templates/review.html` — 重写表格和样式

## 6. 预期效果

- 表格从 15 列减少到 8 列，无需横向滚动即可查看核心信息
- 点击 📎 气泡可查看完整字段信息
- 操作入口使用下拉菜单，节省空间
- 移动端友好，支持水平滚动
