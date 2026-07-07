# 报奖信息监测系统 - 项目规范

## 项目概述
定时抓取规划行业报奖信息，通过关键词+AI筛选后推送到微信。

## 目录结构
- `config.yaml` — 所有配置（不进git）
- `main.py` — 程序入口
- `crawlers/` — 爬虫模块，每个文件一种爬虫类型
- `filters/` — 筛选模块（关键词+AI）
- `notifiers/` — 通知模块（PushPlus）
- `storage/` — 数据存储（SQLite）
- `utils/` — 工具模块（日志、HTTP客户端）
- `data/` — 运行数据（不进git）
- `logs/` — 日志文件（不进git）

## 新增网站流程
1. 在 config.yaml 的 sources.websites 中添加一条配置
2. 设置 name、url、type（gov_general 或 org_general）、enabled: true
3. 如果网站结构特殊，通用爬虫无法解析，在 crawlers/ 下新建适配器继承 BaseCrawler

## 配置修改规范
- 关键词增删：直接改 config.yaml 的 filter.keywords / filter.exclude_keywords
- 临时关闭某个源：设 enabled: false
- 切换AI模型：改 filter.ai.model

## 安全约定
- PushPlus token 只放 config.yaml，不进git
- config.yaml 在 .gitignore 中排除
- 日志和数据文件不进git

## 运行方式
- 手动运行：`python main.py`
- 定时运行：双击 setup_task.bat 注册Windows计划任务（每天09:00和21:00）

## 验证命令
```bash
pip install -r requirements.txt
python -c "from utils.logger import setup_logger; setup_logger(); print('OK')"
python main.py
```
