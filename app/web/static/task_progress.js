// 任务进度浮窗
let taskPollInterval = null;

function showTaskWidget() {
  const mini = document.getElementById('task-progress-mini');
  const widget = document.getElementById('task-progress-widget');
  const progressSection = document.getElementById('progress-section');
  if (mini) mini.style.display = 'none';
  if (widget) widget.classList.add('open');
  // 打开时先隐藏进度条，等 API 返回后再决定是否显示
  if (progressSection) progressSection.style.display = 'none';
  fetchTaskStatus();
  // 开始轮询
  if (!taskPollInterval) {
    taskPollInterval = setInterval(fetchTaskStatus, 2000);
  }
}

function hideTaskWidget() {
  const mini = document.getElementById('task-progress-mini');
  const widget = document.getElementById('task-progress-widget');
  if (widget) widget.classList.remove('open');
  if (mini) mini.style.display = 'flex';
}

function toggleTaskWidget() {
  const widget = document.getElementById('task-progress-widget');
  if (widget && widget.classList.contains('open')) {
    hideTaskWidget();
  } else {
    showTaskWidget();
  }
}

// 点击外部关闭
document.addEventListener('click', function(e) {
  const widget = document.getElementById('task-progress-widget');
  const mini = document.getElementById('task-progress-mini');
  if (widget && widget.classList.contains('open') &&
      !widget.contains(e.target) &&
      (!mini || !mini.contains(e.target))) {
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
  '处理中': { label: '处理中', icon: '⟳', cls: '运行中' },
  '清洗中': { label: '清洗中', icon: '⟳', cls: '运行中' },
  '抽取中': { label: '抽取中', icon: '⟳', cls: '运行中' },
  '审核中': { label: '审核中', icon: '⟳', cls: '运行中' },
  '链接中': { label: '链接中', icon: '⟳', cls: '运行中' },
  '已完成': { label: '已完成', icon: '✓', cls: 'done' },
  '失败': { label: '失败', icon: '✗', cls: 'error' },
  '内容为空': { label: '空内容', icon: '⊘', cls: '内容为空' },
  '清洗后为空': { label: '空内容', icon: '⊘', cls: '内容为空' },
  '待处理': { label: '待处理', icon: '○', cls: 'pending' },
};

function renderTaskStatus(data) {
  const { tasks, summary } = data;

  // 总体进度（有多个运行中任务时显示，1个或不显示时隐藏）
  const runningCount = summary.running || 0;
  const progressSection = document.getElementById('progress-section');
  const progressText = document.getElementById('progress-text');
  const progressFill = document.getElementById('progress-fill');
  if (runningCount > 1 && progressSection) {
    progressSection.style.display = 'block';
    if (progressText) progressText.textContent = `${summary.done}/${summary.total}`;
    const pct = (summary.done / summary.total * 100);
    if (progressFill) progressFill.style.width = pct + '%';
  } else if (progressSection) {
    progressSection.style.display = 'none';
  }

  // 更新徽章
  const miniBadge = document.getElementById('mini-badge');
  const widgetBadge = document.getElementById('widget-badge');
  if (summary.failed > 0) {
    if (miniBadge) {
      miniBadge.textContent = summary.failed;
      miniBadge.style.display = 'inline';
      miniBadge.classList.remove('成功');
    }
    if (widgetBadge) {
      widgetBadge.textContent = summary.failed;
      widgetBadge.style.display = 'inline';
    }
  } else {
    if (miniBadge) miniBadge.style.display = 'none';
    if (widgetBadge) widgetBadge.style.display = 'none';
  }

  // 清空已完成按钮（有待清空时显示）
  const clearBtn = document.getElementById('btn-clear-done');
  const hasDone = tasks.some(t => t.status === '已完成' || t.status === '失败' || t.status === '内容为空' || t.status === '清洗后为空');
  if (clearBtn) clearBtn.style.display = hasDone ? 'inline' : 'none';

  // 显示/隐藏小标签
  const mini = document.getElementById('task-progress-mini');
  const widget = document.getElementById('task-progress-widget');
  if (summary.total === 0) {
    if (mini) mini.style.display = 'none';
  } else if (!widget || !widget.classList.contains('open')) {
    // 浮窗未打开时显示 mini（有待处理/已完成/失败任务时）
    if (mini) mini.style.display = 'flex';
  }

  // 渲染任务列表
  const listEl = document.getElementById('task-list');
  if (!listEl) return;

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
    if (task.status === '已完成') {
      meta += `<span>提取 ${task.facts_count || 0} 条</span>`;
    } else if (!['失败', '内容为空', '清洗后为空'].includes(task.status)) {
      meta += `<span>${timeAgo}</span>`;
    }

    const isRunning = ['处理中', '清洗中', '抽取中', '审核中', '链接中'].includes(task.status);
    const iconHtml = isRunning ? `<span class="spin">${statusInfo.icon}</span>` : statusInfo.icon;

    html += `
      <div class="task-item">
        <div class="task-icon ${statusInfo.cls}">
          ${iconHtml}
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
  if (mini) mini.style.display = 'flex';
  showTaskWidget();
  fetchTaskStatus();
}

// 清空已完成/失败任务
function clearDoneTasks() {
  fetch('/api/tasks/clear', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.error) { console.error(data.error); return; }
      fetchTaskStatus();
    })
    .catch(err => console.error('清空失败:', err));
}
