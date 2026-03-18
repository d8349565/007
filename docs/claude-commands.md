# Claude Code 命令使用大全

## 常用 Slash 命令

| 命令 | 说明 |
|------|------|
| `/help` | 获取帮助 |
| `/compact` | 压缩对话上下文 |
| `/clear` | 清除当前对话 |
| `/resume` | 继续之前被中断的任务 |
| `/review` | 代码审查（需安装superpowers插件） |
| `/brainstorm` | 头脑风暴（需安装superpowers插件） |
| `/test` | 测试相关（需安装superpowers插件） |

## 已安装的插件 (Plugins)

### Superpowers 插件 (v5.0.5)
官方技能库，包含多个专业技能：

| 技能/命令 | 用途 |
|-----------|------|
| `brainstorming` | 头脑风暴，视觉化思考 |
| `test-driven-development` | TDD开发模式 |
| `systematic-debugging` | 系统化调试 |
| `subagent-driven-development` | 子Agent开发模式 |
| `writing-plans` | 编写计划文档 |
| `receiving-code-review` | 接收代码审查 |
| `requesting-code-review` | 请求代码审查 |
| `finishing-a-development-branch` | 完成开发分支 |
| `executing-plans` | 执行计划 |
| `dispatching-parallel-agents` | 并行调度Agent |
| `using-git-worktrees` | Git Worktree使用 |

### 使用方式
```bash
# 触发技能
/brainstorm <主题>
/review
/test
```

## MCP 服务器 (Model Context Protocol)

当前项目**未启用**MCP服务器。

### 可用 MCP (需单独配置)
- **github**: GitHub API集成
- **gitlab**: GitLab API集成
- **slack**: Slack消息
- **linear**: Linear项目管理
- **asana**: Asana任务管理
- **firebase**: Firebase云服务
- **context7**: 上下文搜索
- **playwright**: 浏览器自动化
- **stripe**: 支付集成

## 常用工具 (Tools)

| 工具 | 说明 |
|------|------|
| `Read` | 读取文件 |
| `Edit` | 编辑文件 |
| `Write` | 写入/创建文件 |
| `Bash` | 执行终端命令 |
| `Grep` | 搜索代码内容 |
| `Glob` | 按模式查找文件 |
| `Agent` | 启动子Agent |
| `WebFetch` | 获取网页内容 |
| `WebSearch` | 搜索网络 |
| `TodoWrite` | 管理待办事项 |

## Agent 子代理

| Agent类型 | 用途 |
|-----------|------|
| `Explore` | 代码库探索 |
| `Plan` | 设计实现计划 |
| `general-purpose` | 通用任务 |

## 快捷键

| 快捷键 | 说明 |
|--------|------|
| `Ctrl+C` | 中断当前操作 |
| `Ctrl+L` | 清除屏幕 |
| `Tab` | 自动补全 |
| `双击Esc` | 恢复代码 |

## 项目配置

### 当前配置
- **主题**: light (浅色)
- **工作目录**: F:\Python\007
- **Git仓库**: d8349565/007

### 权限配置
已在 `settings.local.json` 中配置：
- `Bash(gh pr:*)` - GitHub PR操作
- `Bash(sqlite3:*)` - SQLite操作
- `Bash(curl:*)` - 网络请求
- `python` 命令

## 最佳实践

1. **编码问题**: Windows下使用 `PYTHONIOENCODING=utf-8`
2. **后台运行**: 长时间任务使用 `run_in_background=true`
3. **复杂任务**: 使用 `/brainstorm` 或 `EnterPlanMode`
4. **代码审查**: 使用 `/review` 或 `Agent` + code-reviewer
5. **调试**: 使用 `systematic-debugging` 技能
