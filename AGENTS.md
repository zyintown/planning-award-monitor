# 报奖信息监测系统 - 项目规范

## 项目概述
定时抓取规划行业报奖信息，通过关键词+AI筛选后推送到飞书群。

## 目录结构
- `config.yaml` — 所有配置（不进git）
- `main.py` — 程序入口
- `gen.py` — 爬虫测试脚本（逐渠道调试用）
- `crawlers/` — 爬虫模块，每个文件一种爬虫类型
- `filters/` — 筛选模块（关键词+AI）
- `notifiers/` — 通知模块（飞书 Webhook）
- `storage/` — 数据存储（SQLite）
- `utils/` — 工具模块（日志、HTTP客户端）
- `tests/` — 自动化测试，按模块命名 `test_*.py`；不得调用真实飞书 Webhook
- `data/` — 运行数据（不进git）
- `data/backups/` — 数据库迁移前备份（不进git）
- `data/reports/` — 历史待处理记录等人工审核清单（不进git）
- `data/test-runs/` — 自动化测试生成的临时数据库与验证产物（不进git，可按周期人工清理）
- `data/dry-runs/` — `--dry-run` 使用的生产库副本，验证结束后保留供审计（不进git）
- `logs/` — 日志文件（不进git）

## 爬虫类型
| type 值 | 适用场景 | 文件 |
|---------|---------|------|
| `gov_general` | 政府网站通用解析 | `crawlers/gov_general.py` |
| `org_general` | 学会/协会网站通用解析 | `crawlers/org_general.py` |
| `cacp_api` | 中国城市规划协会 JSONP API | `crawlers/cacp_api.py` |
| `chsla_api` | 中国风景园林学会签名 API | `crawlers/chsla_api.py` |

微信公众号通过 `sogo_wechat` 爬虫按公众号名称在搜狗微信搜索抓取。
`feeddd_fallback.py` 是旧的降级爬虫，feeddd.org 已停服，不再调用，文件保留仅供参考。

## 新增网站流程
1. 在 config.yaml 的 sources.websites 中添加一条配置
2. 设置 name、url、type（见上方爬虫类型表）、enabled: true
3. 如果网站结构特殊，通用爬虫无法解析，在 crawlers/ 下新建适配器继承 BaseCrawler
4. 用 `python gen.py` 逐渠道测试抓取结果

## 配置修改规范
- 关键词增删：直接改 config.yaml 的 filter.keywords / filter.exclude_keywords
- 临时关闭某个源：设 enabled: false
- 切换AI模型：改 filter.ai.provider、filter.ai.model 及对应的 fallback 配置
- Agnes Key 仅通过 `filter.ai.api_key_file` 指向项目根目录 `local.env`；程序不读取进程或系统环境变量

## 安全约定
- 飞书 webhook_url 和 secret 只放 config.yaml，不进git
- config.yaml 在 .gitignore 中排除
- Agnes Key 只放项目根目录 `local.env`，该文件已在 .gitignore 中排除；不得进入代码、日志、文档或提交记录
- 日志和数据文件不进git

## 运行方式
- 手动运行：`python main.py`
- 逐渠道测试：`python gen.py`
- 定时运行：双击 setup_task.bat 注册Windows计划任务（每天09:00和21:00）

## 验证命令
```bash
pip install -r requirements.txt
python -c "from utils.logger import setup_logger; setup_logger(); print('OK')"
python -m unittest discover -s tests -v
python main.py
```

涉及通知链路的验证默认使用 fake notifier 或 `--dry-run`，不得向真实飞书群发送测试消息。

## 已知问题
- ~~广东省国土空间规划协会（gdtspa.org.cn）SSL 证书过期~~ 已通过 `ssl_verify: false` + `ssl_skip_domains` 修复，待网站续证书后可删除该配置
- 搜狗微信搜索有反爬机制，连续快速请求会触发限流；生产环境每天1-2次不会触发
- 公众号 `no_match` 表示搜狗结果未命中账号白名单，不计入连续网络失败告警；只有 `failed/partial` 计入连续失败
- “资源中国”和“华夏建设科学技术奖励委员会”停用单账号直搜，但保留在全局主题检索白名单中
- AI 支持 `filter.ai.max_workers` 受控并发；2026-07-10 本机两轮基准选择最小达标值2（比单线程提升36.2%，4路仅再快约1.8%）；硬件或模型变化后用 `storage/benchmark_ai.py` 复测
