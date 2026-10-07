# Sources

| Name | RSS URL | Tier | Category | Paywall |
|------|---------|------|----------|---------|
| 매일경제 | https://www.mk.co.kr/rss/30000001/ | domestic | markets | no |
| 한국경제 | https://www.hankyung.com/feed/all-news | domestic | markets | no |
| 연합인포맥스 | https://news.einfomax.co.kr/rss/allArticle.xml | domestic | markets | no |
| 한겨레 | https://www.hani.co.kr/rss/ | domestic | markets | no |
| Yahoo Finance | https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,^DJI&region=US&lang=en-US | international | markets | no |
| Financial Times | https://www.ft.com/rss/home | international | markets | yes |
| Federal Reserve | https://www.federalreserve.gov/feeds/press_all.xml | international | markets | no |
| Bloomberg Markets | https://feeds.bloomberg.com/markets/news.rss | international | markets | yes |
| OpenAI Blog | https://openai.com/news/rss.xml | international | tech | no |
| Claude Code Releases | https://github.com/anthropics/claude-code/releases.atom | international | tech | no |
| Google DeepMind Blog | https://deepmind.google/blog/rss.xml | international | tech | no |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | international | tech | no |
| Hacker News | https://news.ycombinator.com/rss | tech |  | no |
| Ars Technica | https://feeds.arstechnica.com/arstechnica/index | tech |  | no |
| MIT Technology Review | https://www.technologyreview.com/feed/ | tech |  | no |
| Bloomberg Technology | https://feeds.bloomberg.com/technology/news.rss | tech |  | yes |

Yahoo Finance: `finance.yahoo.com/news/rssindex` went 404 on 2026-10-06 (and had
already stopped serving fresh items around 2026-09-24). The legacy
`feeds.finance.yahoo.com/rss/2.0/headline` endpoint still works; it is scoped by
ticker but `^GSPC,^IXIC,^DJI` returns general US market headlines. It caps at
~19 entries per fetch, far below the old index feed's volume.
