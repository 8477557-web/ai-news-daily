# AI News Daily

每日 AI 科技新闻自动聚合摘要系统。从 9 个新闻源抓取 AI 相关文章，经主题聚类与交叉验证后，由 AI 生成结构化简报，通过 QQ 邮箱发送并发布到 GitHub Pages。

## 功能特点

- **9 大数据源**：GNews、DuckDuckGo、百度新闻、Hacker News、36氪、机器之心、IT之家、TechCrunch、V2EX
- **AI 智能摘要**：DeepSeek Chat 模型生成三板块简报（今日必读、趋势与解读、工具与深读）
- **主题聚类**：基于关键词重叠度自动聚类，多源交叉验证加权排序
- **多输出形式**：Markdown 归档 + HTML 站点 + QQ 邮件
- **全自动运行**：GitHub Actions 每日定时触发，自动发布到 GitHub Pages

## 快速开始

### GitHub Actions（推荐）

1. Fork 本仓库
2. 在仓库 Settings > Secrets 中配置：
   - `DEEPSEEK_API_KEY` — [DeepSeek](https://platform.deepseek.com/) API Key（必需，用于 AI 摘要）
   - `GNEWS_API_KEY` — [GNews](https://gnews.io/) API Key（可选）
   - `QQ_EMAIL` — QQ 邮箱地址（可选，用于邮件发送）
   - `QQ_SMTP_CODE` — QQ 邮箱 SMTP 授权码（可选）
3. 在 Actions 页面启用工作流
4. 每天北京时间 8:10 自动运行

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export DEEPSEEK_API_KEY="sk-xxx"  # DeepSeek key
export GNEWS_API_KEY="xxx"        # 可选
export QQ_EMAIL="xxx"             # 可选
export QQ_SMTP_CODE="xxx"         # 可选

# 运行主脚本
python .github/scripts/fetch_ai_news.py

# 生成静态站点
python .github/scripts/generate_html.py
python .github/scripts/generate_page.py
```

## 项目结构

```
.github/
  scripts/
    config.json              # 配置文件（数据源、LLM、输出选项）
    fetch_ai_news.py         # 主脚本（数据采集 + AI 摘要）
    siliconflow_client.py    # SiliconFlow API 客户端
    generate_html.py         # Markdown → HTML 详情页
    generate_page.py         # 首页列表 + 分页生成
  workflows/
    daily-ai-news.yml        # GitHub Actions 工作流
digests/                     # 归档目录（Markdown + HTML + JSON）
requirements.txt             # Python 依赖
README.md                    # 项目说明
```

## 配置说明

编辑 `.github/scripts/config.json`：

| 配置项 | 说明 |
|--------|------|
| `llm.model` | DeepSeek 模型名称 |
| `llm.base_url` | API 地址 |
| `sources.*.enabled` | 数据源开关 |
| `output.send_email` | 是否发送邮件 |
| `output.generate_site` | 是否生成站点 |

## 数据源

| 来源 | 类型 | 需要 API Key |
|------|------|-------------|
| GNews | API | 是 |
| DuckDuckGo | 免费搜索 | 否 |
| 百度新闻 | 网页抓取 | 否 |
| Hacker News | 免费 API | 否 |
| 36氪 | RSS | 否 |
| 机器之心 | RSS | 否 |
| IT之家 | RSS | 否 |
| TechCrunch | RSS | 否 |
| V2EX | API | 否 |

## GitHub Pages 部署

1. 在仓库 Settings > Pages 中启用
2. Source 选择 `main` 分支，目录选择 `/ (root)`
3. 访问 `https://<username>.github.io/<repo>/`

## 致谢

参考 [Terence699/daily-tech-news](https://github.com/Terence699/daily-tech-news) 的项目结构和 AI 摘要生成方案。
