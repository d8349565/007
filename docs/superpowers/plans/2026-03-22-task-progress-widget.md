# 任务进度浮窗实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/import` 页面添加任务进度浮窗，实时显示文档处理进度，平时缩小成小标签，点击展开查看详情。

**Architecture:** 前端浮窗组件通过轮询 `/api/tasks/status` 获取任务状态，后端从 `source_document` 表查询处理中/失败的任务，返回 JSON 数据。前端每 2 秒轮询一次，实时更新进度。

**Tech Stack:** Flask, SQLite, Vanilla JS, CSS

---

## 文件结构

| 操作 | 文件路径 | 职责 |
|------|----------|------|
| 新增 | `app/web/static/task_progress.css` | 浮窗样式 |
| 新增 | `app/web/templates/_task_progress_widget.html` | 浮窗 HTML 组件 |
| 新增 | `app/services/task_tracker.py` | 任务状态查询服务 |
| 新增 | `app/web/api_tasks.py` | 任务状态 API 路由 |
| 修改 | `app/web/review_app.py` | 注册 API 蓝图 |
| 修改 | `app/web/templates/_base.html` | 添加浮动容器 |
| 修改 | `app/web/templates/import.html` | 引入浮窗组件和 JS |
| 修改 | `app/services/pipeline.py` | 添加 error_message 字段更新 |

---

## Task 1: 创建任务状态 API

**Files:**
- Create: `app/web/api_tasks.py`
- Modify: `app/web/review_app.py:1-50` (添加 blueprint 注册)

- [ ] **Step 1: 创建 api_tasks.py**

```python
"""任务状态 API"""
from flask import Blueprint, jsonify
from app.services.task_tracker import get_processing_tasks

api_tasks_bp = Blueprint('api_tasks', __name__, url_prefix='/api/tasks')


@api_tasks_bp.route('/status', methods=['GET'])
def get_tasks_status():
    """获取当前处理中/失败的任务列表"""
    tasks, summary = get_processing_tasks()
    return jsonify({
        'tasks': tasks,
        'summary': summary
    })
```

- [ ] **Step 2: 在 review_app.py 注册蓝图**

在 `from flask import ...` 后添加：
```python
from app.web.api_tasks import api_tasks_bp
```

在 `app = create_app()` 函数末尾 `return app` 前添加：
```python
    app.register_blueprint(api_tasks_bp)
```

- [ ] **Step 3: 运行验证**

Run: `python -c "from app.web.api_tasks import api_tasks_bp; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

---

## Task 2: 创建任务状态查询服务

**Files:**
- Create: `app/services/task_tracker.py`

- [ ] **Step 1: 创建 task_tracker.py**

```python
"""任务状态跟踪 - 从 source_document 查询处理中/失败的任务"""
from app.models.db import get_connection


def get_processing_tasks(limit: int = 20) -> tuple[list[dict], dict]:
    """获取所有处理中/失败/刚完成的任务"""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                id,
                title,
                status,
                updated_at,
                error_message,
                (SELECT COUNT(*) FROM fact_atom WHERE document_id = source_document.id) AS facts_count
            FROM source_document
            WHERE status IN ('processing', 'cleaning', 'extracting', 'reviewing', 'linking', 'failed', 'processed')
            ORDER BY
                CASE status
                    WHEN 'failed' THEN 0
                    WHEN 'processing' THEN 1
                    WHEN 'cleaning' THEN 2
                    WHEN 'extracting' THEN 3
                    WHEN 'reviewing' THEN 4
                    WHEN 'linking' THEN 5
                    WHEN 'processed' THEN 6
                    ELSE 7
                END,
                updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        tasks = [dict(row) for row in rows]

        # 计算汇总
        total = len(tasks)
        done = sum(1 for t in tasks if t['status'] == 'processed')
        failed = sum(1 for t in tasks if t['status'] == 'failed')
        running = sum(1 for t in tasks if t['status'] in ('processing', 'cleaning', 'extracting', 'reviewing', 'linking'))
        pending = 0  # 当前不追踪 pending 状态

        summary = {
            'total': total,
            'done': done,
            'failed': failed,
            'running': running,
            'pending': pending
        }

        return tasks, summary
    finally:
        conn.close()
```

- [ ] **Step 2: 测试查询函数**

Run: `python -c "from app.services.task_tracker import get_processing_tasks; tasks, s = get_processing_tasks(); print(f'任务数: {s[\"total\"]}, 失败: {s[\"failed\"]}')"`
Expected: 输出当前数据库中的任务统计

- [ ] **Step 3: Commit**

```bash
git add app/services/task_tracker.py app/web/api_tasks.py app/web/review_app.py
git commit -m "feat(task-progress): 添加任务状态 API 和查询服务"
```

---

## Task 3: 添加 error_message 字段支持

**Files:**
- Modify: `app/models/schema.sql`
- Modify: `app/services/pipeline.py:_mark_document_status`

- [ ] **Step 1: 检查 schema.sql 是否已有 error_message 字段**

Run: `grep -n "error_message" app/models/schema.sql`
Expected: 无输出表示需要添加

- [ ] **Step 2: 在 source_document 表添加 error_message 字段**

在 `schema.sql` 的 `source_document` 表中添加：
```sql
    error_message  TEXT,
```

在 `updated_at` 字段后添加。

- [ ] **Step 3: 修改 _mark_document_status 支持 error_message**

在 `pipeline.py` 中修改：
```python
def _mark_document_status(document_id: str, status: str, error_message: str = None) -> None:
    """更新文档的处理状态"""
    conn = get_connection()
    try:
        if error_message:
            conn.execute(
                "UPDATE source_document SET status=?, error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, error_message, document_id),
            )
        else:
            conn.execute(
                "UPDATE source_document SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, document_id),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 在 full_extractor.py 失败时记录错误**

在 `full_extractor.py` 的异常处理中：
```python
except Exception as e:
    _record_task_end(task_id, "failed", error=str(e))
    logger.error("全文抽取调用失败 [doc=%s]: %s", document_id[:8], e)
    # 记录错误到文档
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE source_document SET error_message=? WHERE id=?",
            (str(e)[:500], document_id),  # 限制长度
        )
        conn.commit()
    finally:
        conn.close()
    return []
```

- [ ] **Step 5: Commit**

---

## Task 4: 创建浮窗 CSS 样式

**Files:**
- Create: `app/web/static/task_progress.css`

- [ ] **Step 1: 写入 CSS 样式**

```css
/* ============================================================
 * 任务进度浮窗样式
 * ============================================================ */

/* 最小化小标签 */
.task-progress-mini {
  position: fixed;
  top: 60px;
  right: 16px;
  z-index: 1000;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: 20px;
  padding: 8px 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  box-shadow: var(--shadow-md);
  transition: background 0.2s, transform 0.2s;
  font-size: 13px;
  color: var(--text-muted);
}

.task-progress-mini:hover {
  background: var(--bg-card-hover);
  transform: translateY(-1px);
}

.task-progress-mini .mini-icon {
  font-size: 16px;
}

.task-progress-mini .mini-badge {
  background: var(--color-danger);
  color: #fff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 10px;
  font-weight: 500;
}

.task-progress-mini .mini-badge.success {
  background: var(--color-success);
}

/* 展开浮窗 */
.task-progress-widget {
  position: fixed;
  top: 60px;
  right: 16px;
  width: 320px;
  max-height: 420px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  z-index: 1000;
  display: none;
  flex-direction: column;
}

.task-progress-widget.open {
  display: flex;
}

/* 浮窗头部 */
.widget-header {
  background: var(--bg-card);
  padding: 12px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.widget-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.widget-title .badge {
  background: var(--color-danger);
  color: #fff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 10px;
  font-weight: normal;
}

.widget-btn-minimize {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 16px;
  line-height: 1;
}

.widget-btn-minimize:hover {
  background: var(--bg-card-hover);
  color: var(--text-primary);
}

/* 浮窗内容 */
.widget-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}

/* 进度条 */
.progress-section {
  margin-bottom: 12px;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.progress-bar {
  height: 6px;
  background: var(--border-subtle);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-primary), var(--accent-primary-hover));
  border-radius: 3px;
  transition: width 0.3s ease;
}

/* 任务列表 */
.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-item {
  background: var(--bg-card);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.task-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}

.task-icon.pending { background: var(--border-subtle); color: var(--text-muted); }
.task-icon.running { background: var(--accent-glow); color: var(--accent-primary); }
.task-icon.done { background: var(--color-success-bg); color: var(--color-success); }
.task-icon.error { background: var(--color-danger-bg); color: var(--color-danger); }
.task-icon.empty { background: var(--color-warning-bg); color: var(--color-warning); }

/* 运行中图标动画 */
.task-icon.running .spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.task-content {
  flex: 1;
  min-width: 0;
}

.task-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.task-meta {
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 8px;
  align-items: center;
}

.task-status {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}

.task-status.pending { background: var(--border-subtle); color: var(--text-muted); }
.task-status.running { background: var(--accent-glow); color: var(--accent-primary); }
.task-status.done { background: var(--color-success-bg); color: var(--color-success); }
.task-status.error { background: var(--color-danger-bg); color: var(--color-danger); }
.task-status.empty { background: var(--color-warning-bg); color: var(--color-warning); }

/* 错误详情 */
.error-detail {
  background: var(--color-danger-bg);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 6px;
  padding: 8px 10px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--color-danger);
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 80px;
  overflow-y: auto;
}

/* 折叠提示 */
.task-overflow {
  text-align: center;
  padding: 8px;
  color: var(--text-muted);
  font-size: 12px;
  background: var(--bg-card);
  border-radius: 8px;
  margin-top: 8px;
}

/* 空状态 */
.task-empty {
  text-align: center;
  padding: 20px;
  color: var(--text-muted);
  font-size: 13px;
}
```

- [ ] **Step 2: Commit**

---

## Task 5: 创建浮窗 HTML 组件

**Files:**
- Create: `app/web/templates/_task_progress_widget.html`

- [ ] **Step 1: 写入 HTML 组件**

```html
<!-- 任务进度浮窗组件 -->
<div id="task-progress-mini" class="task-progress-mini" onclick="showTaskWidget()" style="display: none;">
  <span class="mini-icon">⚡</span>
  <span class="mini-text">任务进度</span>
  <span class="mini-badge" id="mini-badge" style="display: none;">0</span>
</div>

<div id="task-progress-widget" class="task-progress-widget">
  <div class="widget-header">
    <div class="widget-title">
      <span>⚡</span>
      <span>任务进度</span>
      <span class="badge" id="widget-badge" style="display: none;">0</span>
    </div>
    <button class="widget-btn-minimize" onclick="hideTaskWidget()">−</button>
  </div>
  <div class="widget-body">
    <!-- 总体进度 -->
    <div class="progress-section">
      <div class="progress-header">
        <span>总进度</span>
        <span id="progress-text">0/0</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="task-list" id="task-list">
      <div class="task-empty">暂无处理中的任务</div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Commit**

---

## Task 6: 添加浮窗 JavaScript

**Files:**
- Modify: `app/web/templates/import.html` (添加 JS)

- [ ] **Step 1: 添加 JavaScript 到 import.html 的 scripts block**

```javascript
// 任务进度浮窗
let taskPollInterval = null;

function showTaskWidget() {
  document.getElementById('task-progress-mini').style.display = 'none';
  document.getElementById('task-progress-widget').classList.add('open');
  fetchTaskStatus();
  // 开始轮询
  if (!taskPollInterval) {
    taskPollInterval = setInterval(fetchTaskStatus, 2000);
  }
}

function hideTaskWidget() {
  document.getElementById('task-progress-widget').classList.remove('open');
  document.getElementById('task-progress-mini').style.display = 'flex';
}

function toggleTaskWidget() {
  const widget = document.getElementById('task-progress-widget');
  if (widget.classList.contains('open')) {
    hideTaskWidget();
  } else {
    showTaskWidget();
  }
}

// 点击外部关闭
document.addEventListener('click', function(e) {
  const widget = document.getElementById('task-progress-widget');
  const mini = document.getElementById('task-progress-mini');
  if (widget.classList.contains('open') &&
      !widget.contains(e.target) &&
      !mini.contains(e.target)) {
    hideTaskWidget();
  }
});

function fetchTaskStatus() {
  fetch('/api/tasks/status')
    .then(r => r.json())
    .then(data => {
      renderTaskStatus(data);
    })
    .catch(err => console.error('获取任务状态失败:', err));
}

// 状态显示映射
const STATUS_MAP = {
  'processing': { label: '处理中', icon: '⟳', cls: 'running' },
  'cleaning': { label: '清洗中', icon: '⟳', cls: 'running' },
  'extracting': { label: '抽取中', icon: '⟳', cls: 'running' },
  'reviewing': { label: '审核中', icon: '⟳', cls: 'running' },
  'linking': { label: '链接中', icon: '⟳', cls: 'running' },
  'processed': { label: '已完成', icon: '✓', cls: 'done' },
  'failed': { label: '失败', icon: '✗', cls: 'error' },
  'empty': { label: '空内容', icon: '⊘', cls: 'empty' },
  'empty_after_clean': { label: '空内容', icon: '⊘', cls: 'empty' },
};

function renderTaskStatus(data) {
  const { tasks, summary } = data;

  // 更新总体进度
  const progressText = document.getElementById('progress-text');
  const progressFill = document.getElementById('progress-fill');
  progressText.textContent = `${summary.done}/${summary.total}`;
  const pct = summary.total > 0 ? (summary.done / summary.total * 100) : 0;
  progressFill.style.width = pct + '%';

  // 更新徽章
  const miniBadge = document.getElementById('mini-badge');
  const widgetBadge = document.getElementById('widget-badge');
  if (summary.failed > 0) {
    miniBadge.textContent = summary.failed;
    miniBadge.style.display = 'inline';
    miniBadge.classList.remove('success');
    widgetBadge.textContent = summary.failed;
    widgetBadge.style.display = 'inline';
  } else {
    miniBadge.style.display = 'none';
    widgetBadge.style.display = 'none';
  }

  // 显示/隐藏小标签
  const mini = document.getElementById('task-progress-mini');
  const widget = document.getElementById('task-progress-widget');
  if (summary.total === 0) {
    mini.style.display = 'none';
    widget.classList.remove('open');
    return;
  }

  // 如果浮窗没打开且没有失败任务，隐藏小标签
  if (!widget.classList.contains('open') && summary.failed === 0) {
    mini.style.display = 'none';
  } else if (!widget.classList.contains('open')) {
    mini.style.display = 'flex';
  }

  // 渲染任务列表
  const listEl = document.getElementById('task-list');
  if (tasks.length === 0) {
    listEl.innerHTML = '<div class="task-empty">暂无处理中的任务</div>';
    return;
  }

  let html = '';
  const maxDisplay = 10;
  const displayTasks = tasks.slice(0, maxDisplay);

  for (const task of displayTasks) {
    const statusInfo = STATUS_MAP[task.status] || { label: task.status, icon: '?', cls: 'pending' };
    const timeAgo = getTimeAgo(task.updated_at);

    let meta = `<span class="task-status ${statusInfo.cls}">${statusInfo.label}</span>`;
    if (task.status === 'processed') {
      meta += `<span>提取 ${task.facts_count || 0} 条</span>`;
    } else if (task.status !== 'failed') {
      meta += `<span>${timeAgo}</span>`;
    }

    html += `
      <div class="task-item">
        <div class="task-icon ${statusInfo.cls}">
          ${task.status === 'processing' || task.status === 'cleaning' || task.status === 'extracting' || task.status === 'reviewing' || task.status === 'linking' ? '<span class="spin">' + statusInfo.icon + '</span>' : statusInfo.icon}
        </div>
        <div class="task-content">
          <div class="task-title" title="${escapeHtml(task.title || '无标题')}">${escapeHtml(task.title || '无标题')}</div>
          <div class="task-meta">${meta}</div>
          ${task.error_message ? `<div class="error-detail">${escapeHtml(task.error_message)}</div>` : ''}
        </div>
      </div>
    `;
  }

  // 超过10条显示折叠提示
  if (tasks.length > maxDisplay) {
    html += `<div class="task-overflow">还有 ${tasks.length - maxDisplay} 个任务</div>`;
  }

  listEl.innerHTML = html;
}

function getTimeAgo(timestamp) {
  if (!timestamp) return '';
  const now = new Date();
  const then = new Date(timestamp);
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return '约' + diff + '秒';
  if (diff < 3600) return '约' + Math.floor(diff / 60) + '分钟';
  return '约' + Math.floor(diff / 3600) + '小时';
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// 页面加载时检查是否有处理中的任务
document.addEventListener('DOMContentLoaded', function() {
  fetchTaskStatus();
  // 启动轮询
  taskPollInterval = setInterval(fetchTaskStatus, 2000);
});

// 导入开始时立即显示
function onImportStarted() {
  const mini = document.getElementById('task-progress-mini');
  mini.style.display = 'flex';
  showTaskWidget();
  fetchTaskStatus();
}
```

- [ ] **Step 2: Commit**

---

## Task 7: 修改 import.html 引入浮窗

**Files:**
- Modify: `app/web/templates/import.html`

- [ ] **Step 1: 在 import.html 中引入组件和样式**

在 `{% extends "_base.html" %}` 之后添加：
```html
{% block head %}
<link rel="stylesheet" href="{{ url_for('static', filename='task_progress.css') }}">
{% endblock %}
```

在 `{% block scripts %}` 开头添加：
```html
{{ super() }}
<script src="{{ url_for('static', filename='task_progress.js') }}"></script>
```

在表单提交成功后调用 `onImportStarted()`：
```javascript
// 在 submitPaste, submitFile, submitUrl 成功回调中添加
if (data.processing) onImportStarted();
```

- [ ] **Step 2: 测试页面加载**

Run: `python -m app.main web` 并访问 http://localhost:5000/import
Expected: 页面正常加载，右上角有浮窗

- [ ] **Step 3: Commit**

---

## Task 8: 集成测试

**Files:**
- Modify: `app/web/templates/_base.html` (可选：全局引入)

- [ ] **Step 1: 验证浮窗功能**

1. 在导入页面导入一篇文章
2. 观察右上角是否出现小标签
3. 点击小标签展开浮窗
4. 观察任务状态变化

- [ ] **Step 2: 验证错误显示**

1. 导入一篇文章，处理失败
2. 观察失败任务是否显示红色 ✗
3. 展开错误详情是否可见

- [ ] **Step 3: Commit**

---

## 验证命令

```bash
# 1. 语法检查
python -m py_compile app/web/api_tasks.py
python -m py_compile app/services/task_tracker.py

# 2. 运行测试
pytest tests/ -v

# 3. 启动 Web 服务测试
python -m app.main web
```
