# 报奖信息监测系统

每天定时抓取规划行业报奖信息，经关键词和 AI 筛选后推送到飞书群。当前 AI 优先使用 Agnes，Agnes 不可用时回退本地 Ollama。系统重点保证：单个渠道失败不拖垮全流程、处理中断后可以续跑、推送失败不会永久漏报。

## 快速开始

前置条件：Python 3.10+、项目根目录的 `local.env`（Agnes Key）、本地 Ollama 及配置中的 fallback 模型、飞书群机器人 Webhook。

```powershell
cd "G:\AI\报奖信息监测"
pip install -r requirements.txt
Copy-Item config.yaml.example config.yaml
```

编辑 `config.yaml`，只在该文件中填写真实 Webhook 和可选加签密钥；该文件已被 git 忽略。

```yaml
notification:
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/你的地址"
    secret: ""
```

AI 配置已经在 `config.yaml` 中设为 Agnes 主用、Ollama fallback。Agnes Key 只从项目根目录的 `local.env` 读取，不从进程或系统环境变量读取；`local.env` 已加入 `.gitignore`，不要把真实 Key 写入 README、示例配置、日志或提交记录。文件使用普通 `key=value` 行格式，例如：

```text
key1=替换为真实Key
key2=替换为真实Key
```

Ollama 仅作为 Agnes 请求失败、返回空内容或 Key 不可用时的本地 fallback；两者都不可用时，文章保持 `ai_pending`，不会被当作通过。

## 安全验证

自动化测试和 dry-run 都不会向真实飞书群发送消息。

```powershell
python -c "from utils.logger import setup_logger; setup_logger(); print('OK')"
python -m unittest discover -s tests -v
python main.py --dry-run
```

`--dry-run` 会把生产数据库复制到 `data/dry-runs/`，在副本上运行抓取、去重和筛选，并禁止调用飞书；生产库不会被写入。也可以只测指定渠道：

```powershell
python main.py --dry-run --source "website:广东省住房和城乡建设厅"
python main.py --dry-run --source "wechat:中国城市规划学会"
python main.py --dry-run --source gov_general
```

不加前缀的同名机构会同时匹配网站和公众号；需要只测一种渠道时使用 `website:` 或 `wechat:`。

## 正式运行

```powershell
python main.py
```

双击 `setup_task.bat` 可注册每天 09:00 和 21:00 的 Windows 计划任务。`run.bat` 优先使用项目 `.venv`，不存在时才使用系统 PATH 中的 `pythonw`。

退出码：

- `0`：全部完成且无错误。
- `1`：主流程失败。
- `2`：配置无效。
- `3`：已有实例运行，未获取到进程锁。
- `4`：主流程完成，但至少一个渠道、AI或推送处于失败/待重试状态。

公众号健康状态中，`no_match` 表示搜狗返回了结果但没有命中白名单账号；这是语义过滤结果，不计入连续网络失败告警。连续失败告警只统计 `failed` 和 `partial`。当前“资源中国”和“华夏建设科学技术奖励委员会”已停用单账号直搜，但仍保留在全局主题检索白名单中。

## 处理流程

```text
渠道抓取并记录健康状态
  → 90天窗口去重，新增记录进入 discovered
  → 抓取新增文章详情正文
  → P3标题门禁（拒绝获奖结果、非奖项、非行业）
  → 关键词筛选
  → AI严格JSON判断并抽取奖项名称、截止日期、申报对象
  → ready_to_push
  → 飞书分批推送
  → pushed / push_failed（下次精确重试）
```

新流程状态：

- `discovered`：已发现，尚未完成筛选。
- `title_gate_rejected`：P3 标题门禁判定无关（获奖结果、非奖项等）。
- `keyword_rejected`：未通过关键词筛选。
- `ai_pending`：等待 AI 判断或 AI 暂不可用，下次重试。
- `ai_rejected`：AI 判定不是可申报的报奖通知。
- `ready_to_push`：等待推送。
- `push_failed`：飞书推送失败，下次重试。
- `pushed`：推送完成。

历史 v1 记录不会自动进入新重试队列。历史 `ai_confirmed` 人工审核清单可重新导出：

```powershell
python storage/export_legacy_review.py
```

## P2 人工评估

只读导出完整历史样本和优先标注队列：

```powershell
python storage/export_evaluation_review.py
```

优先队列包含全部 AI 阶段样本和按来源分层抽取的关键词负例。在 `人工审核结论` 中填写 `是`、`否` 或 `不确定` 后生成质量报告：

```powershell
python storage/evaluate_labels.py "data/reports/P2人工标注优先队列_20260710.csv"
```

报告输出加权混淆矩阵、准确率、精确率、召回率、F1、95% 区间以及误报/漏报清单。标注未完成时会明确标记为临时结果。

## 结构化抽取与 AI 并发

- schema v3 使用独立的 `article_extractions` 表保存奖项名称、截止日期、申报对象、模型、提示词版本和原始响应。
- AI 提示词包含判断日期和发布日期；过期奖项应判定为否。
- `filter.ai.max_workers` 只允许 1～4。运行只读基准：

```powershell
python storage/benchmark_ai.py --sample-size 6 --workers 1 2 4 --repeats 2
```

2026-07-10 在本机 `qwen3.5:latest` + RTX 3080 上做两轮复测：单线程中位数 8.438 秒，2 路 6.195 秒（提升 36.2%），4 路 6.086 秒（提升 38.6%），判断完全一致且错误为 0。4 路相比 2 路收益很小，因此按“最小满足门槛”原则选择生产 `max_workers: 2`。

## 可靠性设计

- 数据库 schema 自动迁移前备份到 `data/backups/`。
- SSL 证书过期的网站可通过 `ssl_verify: false` + `ssl_skip_domains` 跳过验证，待网站续证书后删除。
- URL 规范化并结合“标题+来源”在配置窗口内去重，不再受永久 URL 唯一约束影响。
- 每个渠道独立记录状态、数量、耗时、详情成功数和错误；历史正常渠道突然返回0条会标记异常。
- `source_semantics.jsonl` 保存公众号原始结果数、账号匹配数和补抓窗口数，便于区分网络失败、空结果、异常和 `no_match`。
- 公众号默认补抓最近3天，日期缺失时保守保留。
- AI 返回必须是合法结构化 JSON；布尔值、日期或申报对象类型错误时留在 `ai_pending`，不会盲目群发。
- 飞书按批次落库，部分成功时只重试失败批次。
- 文件锁由操作系统持有，进程异常退出后自动释放。

## 调试与测试

逐网站查看原始解析结果：

```powershell
python gen.py 0
```

自动化测试覆盖数据库迁移、90天去重、AI严格解析、渠道失败识别、详情正文提取、公众号补抓和推送失败重试：

```powershell
python -m unittest discover -s tests -v
```

## 目录

```text
├── main.py                 # 主流程和 CLI
├── config.yaml.example     # 无密钥配置模板
├── crawlers/               # 网站/API/公众号爬虫
├── filters/                # 关键词和 AI 筛选
├── notifiers/              # 飞书通知
├── storage/                # SQLite、人工评估、历史清单导出与 AI 基准
├── utils/                  # 日志、HTTP、规范化、进程锁
├── tests/                  # 自动化测试，不调用真实飞书
├── data/backups/           # 数据库迁移前备份，不进 git
├── data/dry-runs/          # dry-run 数据库副本，不进 git
├── data/reports/           # 人工审核清单，不进 git
└── logs/                   # 运行日志，不进 git
```

信息源和适配器细节见 `docs/superpowers/specs/2026-07-07-award-info-monitor-design.md`。
