# Sources

| Name | RSS URL | Tier | Category | Paywall | Kind |
|------|---------|------|----------|---------|------|
| 매일경제 | https://www.mk.co.kr/rss/30000001/ | domestic | markets | no | rss |
| 한국경제 | https://www.hankyung.com/feed/all-news | domestic | markets | no | rss |
| 연합인포맥스 | https://news.einfomax.co.kr/rss/allArticle.xml | domestic | markets | no | rss |
| 한겨레 | https://www.hani.co.kr/rss/ | domestic | markets | no | rss |
| Yahoo Finance | https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,^DJI&region=US&lang=en-US | international | markets | no | rss |
| Financial Times | https://www.ft.com/rss/home | international | markets | yes | rss |
| Federal Reserve | https://www.federalreserve.gov/feeds/press_all.xml | international | markets | no | rss |
| Bloomberg Markets | https://feeds.bloomberg.com/markets/news.rss | international | markets | yes | rss |
| OpenAI Blog | https://openai.com/news/rss.xml | international | tech | no | rss |
| Claude Code Releases | https://github.com/anthropics/claude-code/releases.atom | international | tech | no | rss |
| Google DeepMind Blog | https://deepmind.google/blog/rss.xml | international | tech | no | rss |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | international | tech | no | rss |
| Hacker News | https://news.ycombinator.com/rss | tech |  | no | rss |
| Ars Technica | https://feeds.arstechnica.com/arstechnica/index | tech |  | no | rss |
| MIT Technology Review | https://www.technologyreview.com/feed/ | tech |  | no | rss |
| Bloomberg Technology | https://feeds.bloomberg.com/technology/news.rss | tech |  | yes | rss |
| Anthropic News | https://www.anthropic.com/news | international | tech | no | html-index |
| Anthropic Research | https://www.anthropic.com/research | international | tech | no | html-index |

Yahoo Finance: `finance.yahoo.com/news/rssindex` went 404 on 2026-10-06 (and had
already stopped serving fresh items around 2026-09-24). The legacy
`feeds.finance.yahoo.com/rss/2.0/headline` endpoint still works; it is scoped by
ticker but `^GSPC,^IXIC,^DJI` returns general US market headlines. It caps at
~19 entries per fetch, far below the old index feed's volume.

Anthropic ships no feed: rss.xml, news/rss.xml, feed.xml, news/feed and
research/rss.xml all 404 (checked 2026-11-12; engineering/rss.xml 308s to
research/rss.xml, which also 404s). Until these two rows existed its
announcements reached us only when someone posted them to Hacker News, which had
missed 17 of the 25 posts then on /news. The rows are its listing pages scraped
into entries instead (`collector/html_index.py`): each tile carries date, title
and summary, and the post URL doubles as the guid a Hacker News item would
carry, so a post that arrives both ways is stored once. /engineering is not
listed separately because engineering posts also appear on the /research index.
