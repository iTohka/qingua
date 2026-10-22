# 参照记录

这份记录解释“轻刮”为什么长这样。两个参照程序都只做了**静态清点**：没有加载程序集、没有执行目标代码、没有调用它们的刮削接口。

## 1. 易影视 eVideo 3.2.0.13

### 观察到的事实

- 目录：`易影视_eVideo3.2.0.13_x64`
- `eVideo.dll`：2,891,264 字节，SHA-256 `1553de0fb4f7186a75542f136b265b5d4eee1e7e21371ea51d6e3b113ee6cb2f`
- 程序集版本：`3.2.0.13`；目标框架：`.NETCoreApp,Version=v6.0`
- PE/CLI：x86-64，IL-only；模块 MVID `2a65cc5e-40ed-4542-8e28-b3e32c91a130`
- 元数据清点：1,344 个 TypeDef、10,288 个 MethodDef、764 个 TypeRef、63 个 AssemblyRef
- `eVideo.deps.json` 中出现 `Ookii.Dialogs.Wpf`、`SkiaSharp.Views.WPF`，可确认是 WPF 桌面应用；目录中同时带有 FFmpeg/ffme、字幕与播放相关依赖。
- 类型清单中可确认的刮削命名空间：
  - `ScraperLib.Scrapers.DouBan.DouBanScraper`
  - `ScraperLib.Scrapers.TMDb.TMDbScarper`
  - `ScraperLib.Scrapers.MaoYan.MaoYanScraper`
  - `ScraperLib.Scrapers.WMDb.WMDbScraper`
  - `ScraperLib.Scrapers.So360.So360Scraper`
- `ScraperLib.Configuration` 的字段包括：
  - `TMDB_ApiKey`、`TMDB_DefaultLanguage`
  - `WebProxy`
  - `TianApi_ApiKey`、`Assrt_ApiKey`、`FanartTv_ApiKey`

### 对轻刮的影响

eVideo 的体量来自“媒体库 + 播放器 + 多刮削源 + 字幕/代理配置”的组合，而不是单一刮削流程。轻刮只保留：

- 电影 / 剧集两种媒体
- 豆瓣 + 番库 + 哔哩哔哩 + TMDb 四个来源
- 封面 + 简介 + NFO
- 一个明确的“应用”动作

## 2. tinyMediaManager 5.0.13

### 观察到的事实

- `version` 文件：`5.0.13`，build `12cd398`，打包日期 2024-10-28
- Java 应用，自带 `jre/`；`tmm.jar` 6,788,595 字节
- `tmm.jar` 中可见的 scraper 包包括：
  - `tmdb`、`imdb`、`thetvdb`、`tvmaze`、`anidb`、`ofdb`、`moviemeter`、`mpdbtv`
  - `fanarttv`、`trakt`、`opensubtitles`、`opensubtitles_com`、`kodi`
  - `universal_movie`、`universal_tvshow`、`rating`、`hdtrailersnet`
- `messages_zh.properties` 中可直接看到：
  - “元数据刮削 / 艺术图刮削 / 预告片刮削 / 字幕刮削”
  - “自动刮削匹配 / 搜索无果时启用其他刮削器”
  - “相似度指数”，提示默认 75，100 = 完全匹配
  - 大量重命名 token：`$T` 片名、`$O` 原片名、`$Y` 年份、`$M` 系列片名、`$R` 分辨率、`$A` 音频、`$V` 视频编码、`$S` 媒体来源等
  - NFO 格式、NFO 文件名、艺术图文件名、季文件夹名等独立设置
- `MediaSearchResult` 同时持有 `score`、`title`、`originalTitle`、`year`、`ids`、`posterUrl`、`metadata`，说明其搜索/打分/详情是分层的。

### 对轻刮的影响

tinyMediaManager 证明了这些能力是“专业用户需要”的：

- 多源兜底
- 搜索相似度阈值
- 重命名预览
- NFO/艺术图命名设置

轻刮保留了其中对“快速刮干净”最关键的部分：

- 自动兜底 + 置信度阈值
- 应用前预览候选
- NFO 标准字段
- 整理前完整预览

刻意没有做：

- 预告片、字幕、Trailer provider
- 每个字段的精细开关
- 复杂的命名 token 编辑器
- 媒体库同步、Kodi 连接、Trakt 同步
- 多用户/账号体系

## 3. 设计结论

| 需求 | eVideo | tinyMediaManager | 轻刮 |
|---|---|---|---|
| 扫描目录 | 有 | 有 | 有 |
| 电影/剧集识别 | 有 | 有 | 有 |
| 文件名清洗 | 有 | 有 | 有 |
| 多源搜索 | 很多 | 十余个 | 豆瓣 + 番库 + B站 + TMDb |
| 自动匹配 | 有 | 有 | 有，低分进待确认 |
| 海报/背景图 | 有 | 有 | 有 |
| NFO | 有 | 有，字段极全 | 有，主流播放器够用 |
| 重命名 | 有 | 有，token 极多 | 有，固定安全模板 + 预览 |
| 播放器 | 有 | 无 | 无 |
| 字幕/预告片 | 有入口 | 有 | 无 |
| 安装/依赖 | .NET 6 + 大量 DLL | 自带 JRE + 大量 JAR | Python 标准库 |

轻刮的目标不是“功能更多”，而是让第一次使用的人在 3 分钟内完成：
**添加目录 → 扫描 → 一键刮削 → 去播放器里看结果。**

## 4. 证据边界

- eVideo：REA 静态 managed 元数据清点，未加载程序集、未执行、未调用其网络接口。会话 Evidence 包括 `ev_9d22b8a0a1cc5a8aa0d8b49c68dcb8b915209701e6260c340ff23733cb5add19`（程序集摘要）、`ev_a2067a40353343affcfbb3750053320cb5d3b0c37d235c80a1e7448d924cc8e9`（scraper 类型摘要）、`ev_3cdf23092d07d7f58ea63a53d921a2f5621a32f0f703070c1b9ca5a61a480251`（Configuration 字段摘要）。
- tinyMediaManager：JAR 条目清单、`version` 文件、`messages_zh.properties` 文本；未运行程序。
- 以上事实只支持“这些类/字段/文案存在”，不证明运行时行为、抓取成功率或数据准确性。
