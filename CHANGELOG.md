# 更新日志

所有对 PaperCrawler 项目的显著修改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased]

### 新增 (Added)
- 领域相关性判定模块 `papercrawler.classify.DomainFilter`(基于 title+abstract 关键词 + 模糊匹配)
- 自动多标签分类模块 `papercrawler.classify.Categorizer`(一篇可同时属多类)
- CSV 导出器 `papercrawler.export.CSVWriter`(UTF-8 BOM, Excel 友好)
- 新增 CLI 选项:`--interest` / `--interest-threshold` / `--categorize` / `--csv`
- 配置文件新增 `[interest]` 节(描述、must_have / should_have / exclude 关键词权重、自定义分类)
- `PaperMetadata` 模型新增 `interest_score` 与 `categories` 字段
- GitHub 仓库: https://github.com/szy120320/papercrawler(remote 使用 `ghfast.top` 镜像绕过 github.com 封锁)

### 变更 (Changed)
- 项目重命名:`paper-dl` → `PaperCrawler`
- 包目录重命名:`paper_dl/` → `papercrawler/`
- 配置文件名:`paper_dl.toml` → `papercrawler.toml`
- CLI 命令名:`paper-dl` → `papercrawler`
- `README.md` 完全重写,突出领域感知新特性
- `papercrawler.toml.example` 新增完整 `[interest]` 配置示例
- `.gitignore` 新增规则:`_*.py` 拦截所有以下划线开头的 Python 临时脚本
- 远程仓库 URL:`https://ghfast.top/https://github.com/szy120320/papercrawler.git`(因 github.com 在本机网络下不可直连)

### 修复 (Fixed)
- 无

### 安全 (Security)
- 清理了首次提交中误包含的本地临时脚本(`_setup_github.py` / `_test_github.py` / `_test_proxy.py`),这些是诊断 / 配置辅助脚本,不应入库

---

## 版本标签说明

- **Major(X.0.0)**:不兼容的 API 修改
- **Minor(0.X.0)**:向下兼容的功能新增
- **Patch(0.0.X)**:向下兼容的 bug 修复

预发布版本与构建元数据见 [semver.org](https://semver.org/lang/zh-CN/)。
