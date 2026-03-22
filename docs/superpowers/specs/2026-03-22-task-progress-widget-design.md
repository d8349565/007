# 任务进度浮窗设计文档

**日期**: 2026-03-22
**功能**: 导入页面任务进度实时显示

---

## 1. 功能概述

在 `/import` 页面右下角添加一个可折叠的任务进度浮窗，实时显示文档处理进度。默认情况下显示一个悬浮小标签，点击展开查看详情。

---

## 2. UI 设计

### 2.1 最小化状态（小标签）
- **位置**: 页面右上角，固定定位 `top: 60px; right: 16px`
- **样式**: 圆角胶囊形，背景 `#323232`，边框 `#404040`
- **内容**: 图标 `⚡` + "任务进度" + 失败任务数量（红色徽章）
- **交互**: 点击展开浮窗

### 2.2 展开状态（浮窗）
- **尺寸**: 宽度 `320px`，最大高度 `400px`
- **位置**: 页面右上角，固定定位
- **样式**: 深色卡片风格，圆角 `12px`，阴影

#### 浮窗头部
- 图标 `⚡` + "任务进度" + 失败数量徽章
- 右侧最小化按钮 `−`

#### 总体进度条
- 显示已完成/总数，如 "5/7 完成"
- 蓝色渐变进度条

#### 任务列表
- **最大显示**: 10 条任务
- **超出处理**: 超过10条时，底部显示 "还有 N 个任务" 折叠提示
- **任务项内容**:
  - 状态图标（○待处理 ⟳进行中 ✓完成 ✗失败）
  - 文档标题（超长截断）
  - 状态标签 + 耗时/结果说明
  - 失败任务：展开显示错误详情

### 2.3 错误详情
- 红色边框红色背景警告框
- 显示完整错误信息
- 最大高度 `80px`，超出滚动

### 2.4 交互行为
- **点击外部**: 自动收起浮窗（保持最小化标签）
- **点击最小化按钮**: 收起成小标签
- **点击小标签**: 展开浮窗
- **实时更新**: 每2秒轮询后端 API 获取最新状态

---

## 3. 后端 API 设计

### 3.1 获取任务状态
```
GET /api/tasks/status

Response:
{
  "tasks": [
    {
      "document_id": "xxx",
      "title": "佐敦全球第一",
      "status": "linking",  // cleaning|extracting|reviewing|linking|processed|failed|empty
      "error_message": null,
      "started_at": "2026-03-22T15:00:00",
      "updated_at": "2026-03-22T15:00:15",
      "facts_count": 3
    }
  ],
  "summary": {
    "total": 7,
    "done": 5,
    "failed": 2,
    "running": 0,
    "pending": 0
  }
}
```

### 3.2 数据库表（可选）
利用现有的 `source_document.status` 字段，新增 `processing` 等状态值：
- `processing` - 开始处理
- `cleaning` - 清洗中
- `extracting` - 抽取中
- `reviewing` - 审核中
- `linking` - 链接中
- `processed` - 已完成
- `failed` - 失败

---

## 4. 技术实现

### 4.1 文件变更
1. **新增** `app/web/templates/_task_progress_widget.html` - 浮窗组件
2. **新增** `app/web/static/task_progress.css` - 浮窗样式
3. **新增** `app/web/templates/_base_with_task_widget.html` - 包含浮窗的基类模板
4. **修改** `app/web/review_app.py` - 添加 `/api/tasks/status` 接口
5. **修改** `app/web/templates/import.html` - 引入浮窗组件
6. **修改** `app/web/static/style-dark-dashboard.css` - 添加浮窗样式

### 4.2 前端轮询
- 页面加载时启动定时器，每 2 秒调用一次 `/api/tasks/status`
- 停止处理时关闭定时器
- 失败任务显示红色徽章

### 4.3 状态映射
| 数据库 status | 浮窗显示 |
|--------------|----------|
| ACTIVE | ○ 待处理 |
| processing | ○ 处理中 |
| cleaning | ⟳ 清洗中 |
| extracting | ⟳ 抽取中 |
| reviewing | ⟳ 审核中 |
| linking | ⟳ 链接中 |
| processed | ✓ 已完成 |
| failed | ✗ 失败 |
| empty | ⊘ 空内容 |

---

## 5. 优先级

1. **P0**: 浮窗基本显示（展开/收起）
2. **P0**: 任务列表显示
3. **P0**: 状态图标和颜色
4. **P1**: 总体进度条
5. **P1**: 失败任务红色徽章
6. **P2**: 错误详情展开
7. **P2**: 超过10条折叠显示
8. **P3**: 实时轮询更新

---

## 6. 不在本次范围内

- 不实现任务取消功能
- 不实现单个任务重试（已有 reprocess 按钮）
- 不实现通知推送（仅视觉提示）
