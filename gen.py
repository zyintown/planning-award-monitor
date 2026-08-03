import yaml, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawlers.gov_general import GovGeneralCrawler
from crawlers.org_general import OrgGeneralCrawler
from crawlers.cacp_api import CacpApiCrawler
from crawlers.chsla_api import ChslaApiCrawler
from utils.logger import setup_logger
setup_logger(level='WARNING')

cfg = yaml.safe_load(open('config.yaml', 'r', encoding='utf-8'))
sites = [s for s in cfg['sources']['websites'] if s.get('enabled', True)]
cm = {'gov_general': GovGeneralCrawler, 'org_general': OrgGeneralCrawler, 'cacp_api': CacpApiCrawler, 'chsla_api': ChslaApiCrawler}

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
s = sites[idx]
name = s['name']
url = s['url']
st = s.get('type', 'org_general')
pg = s.get('pagination', {})
mode = pg.get('mode', 'auto')
maxp = 1 if mode == 'none' else 3
cr = cm.get(st, OrgGeneralCrawler)(name=name, url=url, config=cfg, pagination=pg, max_pages_override=maxp)
lines = []
total = 0
lines.append('# 渠道测试: ' + name)
lines.append('')
lines.append('- 类型: ' + st)
lines.append('- 基础URL: ' + url)
lines.append('- 翻页模式: ' + mode)
if pg.get('url_template'):
    lines.append('- URL模板: ' + pg['url_template'])
if pg.get('first_page_url'):
    lines.append('- 首页URL: ' + pg['first_page_url'])
lines.append('- 测试页数: ' + str(maxp))
lines.append('')

current_url = url
for pn in range(1, maxp + 1):
    if mode == 'construct':
        pu = cr._build_page_url(pn)
    else:
        pu = current_url
    lines.append('---')
    lines.append('## 第 ' + str(pn) + ' 页')
    lines.append('')
    lines.append('**URL**: ' + str(pu))
    lines.append('')
    try:
        html = cr._request_page(pu)
        if not html:
            lines.append('> 页面请求失败')
            lines.append('')
            continue
        if mode not in ('construct', 'none') and pn < maxp:
            next_url = cr._find_next_page(html, pu)
            if not next_url or next_url == pu:
                break
            current_url = next_url
        arts = cr._parse(html)
        lines.append('**解析到 ' + str(len(arts)) + ' 条文章**')
        lines.append('')
        if arts:
            for i, a in enumerate(arts, 1):
                t = a.title.replace('|', '/')
                d = a.publish_date or '-'
                lines.append(str(i) + '. [' + d + '] ' + t)
                lines.append('   ' + a.url)
            lines.append('')
            total += len(arts)
        else:
            lines.append('> 未解析到任何文章')
            lines.append('')
    except Exception as e:
        lines.append('> 异常: ' + type(e).__name__ + ': ' + str(e))
        lines.append('')
        break

lines.insert(7, '- 总计抓取: ' + str(total) + ' 条文章')
lines.insert(8, '')
result = '\n'.join(lines)
with open('test_result_temp.md', 'w', encoding='utf-8') as f:
    f.write(result)
print(result)
