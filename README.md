# 我的股票监控 📈

一个属于你自己的股票监控网站 + 自动邮件告警系统。**全程免费、不用自己开机。**

- **实时看板网页**：用 GitHub Pages 托管，随时打开看你关心的 9 只股票的价格、当日涨跌、距 52 周高点的回撤、以及消息面新闻。
- **自动邮件告警**：GitHub Actions 每 15 分钟（交易时段）自动拉一次行情，跌破你设的阈值时，用你的 Gmail 给你发邮件。

监控的股票：NVDA、META、AVGO、AMD、RKLB、IREN、MilDef(MILDEF.ST)、Invisio(IVSO.ST)、Norditek(NOTEK.ST)。

> ⚠️ **NORDIT 请核对**：我按 “Norditek Group AB = `NOTEK.ST`” 填的。若不对，去 https://finance.yahoo.com 搜到正确公司，把 `config.json` 里的 symbol 改成 Yahoo 显示的代码即可。

---

## 告警规则（可在 `config.json` 里改）

1. **回撤分档**：距 52 周最高点每**首次**跌破一个新档位（默认 5% / 10% / 15% / 20% / 25% / 30% / 40% / 50%）发一封邮件。
   例如某股先跌破 5% 发一封，继续恶化到跌破 10% 再发一封 —— 不会同一情况反复刷屏。
2. **今日大跌**：某股当日（相对昨收）跌幅 ≥ 5% 时，额外发一封「今日大跌」（每股每天最多一封）。

> 📌 **首次运行的邮件**：第一次跑时，所有当前已低于 52 周高点 5% 以上的股票都会在**一封**邮件里列出来（这是建立基线）。之后只在**新发生**的下跌事件时才发邮件。

---

## 一次性配置（约 10 分钟）

### 第 1 步：生成 Gmail「应用专用密码」

普通 Gmail 登录密码不能用于程序发信，需要专用密码：

1. 打开 https://myaccount.google.com/security ，确保**两步验证（2-Step Verification）已开启**（没开就先开，这是前提）。
2. 打开 https://myaccount.google.com/apppasswords 。
3. 应用名称随便填（比如 `stock-monitor`），点生成。
4. 会得到一串 **16 位密码**（形如 `abcd efgh ijkl mnop`）。**复制保存好**，第 3 步要用。（空格可留可去，脚本会自动去掉。）

### 第 2 步：创建 GitHub 仓库并上传文件

1. 登录 https://github.com ，点右上角 `+` → **New repository**。
2. 名字填 `stock-monitor`，选 **Public**（公开仓库的 Actions 完全免费；你的密码不在代码里，而是存在下一步的加密 Secrets 中，安全）。点 **Create repository**。
3. 把本文件夹里的所有文件上传上去：仓库页面点 **Add file → Upload files**，把这些**连同文件夹结构**一起拖进去：
   ```
   config.json
   monitor.py
   state.json
   README.md
   .gitignore
   docs/index.html
   docs/data/quotes.json
   .github/workflows/monitor.yml
   ```
   > 网页拖拽会保留斜杠路径。若某个文件夹没上去，可分批上传：先传根目录文件，再进 `docs/` 传，再进 `.github/workflows/` 传。（用 Git 命令行的话，直接 `git init && git add . && git commit && git push` 最省事。）
4. 点 **Commit changes**。

### 第 3 步：添加两个密钥（Secrets）

1. 仓库页面 → **Settings** → 左侧 **Secrets and variables** → **Actions** → **New repository secret**。
2. 添加第一个：Name = `GMAIL_USER`，Secret = 你的完整 Gmail 地址（如 `zhangwhthu@gmail.com`）。
3. 再点 **New repository secret** 添加第二个：Name = `GMAIL_APP_PASSWORD`，Secret = 第 1 步那 16 位应用专用密码。
4. （可选）想发到别的邮箱：改 `config.json` 里的 `"alert_to"`。留空则发到你自己的 Gmail。

### 第 4 步：开启网页（GitHub Pages）

1. 仓库 → **Settings** → 左侧 **Pages**。
2. Source 选 **Deploy from a branch**；Branch 选 **main**，文件夹选 **/docs**，点 **Save**。
3. 等 1–2 分钟，页面顶部会显示你的网址：**`https://<你的用户名>.github.io/stock-monitor/`** —— 这就是你的股票看板，收藏它。

### 第 5 步：跑第一次 + 收测试邮件

1. 仓库 → **Actions** 标签页。若提示 “Workflows aren't running”，点绿色按钮启用。
2. 左侧选 **stock-monitor** → 右侧 **Run workflow** → **Run workflow**（手动触发一次，不用等定时）。
3. 约 1 分钟后跑完（绿勾）。若有股票满足告警条件，你的邮箱会收到一封告警邮件（**检查垃圾邮件箱**，把发件人加白名单）。
4. 打开第 4 步的网址，看板应显示真实行情了。

搞定！之后**周一至周五交易时段每 15 分钟**自动运行，你什么都不用管。

---

## 日常使用

- **看行情**：随时打开你的看板网址。网页每 60 秒自己刷新，后台每 ~15 分钟更新一次数据。
- **加/减股票、改阈值**：编辑 `config.json`（GitHub 网页上直接点文件 → 铅笔图标改 → Commit 即可生效）。
  - 加股票：在 `tickers` 里加一行 `{ "symbol": "TSLA", "name": "Tesla" }`。美股直接用代码，瑞典股加 `.ST`，港股加 `.HK`。
  - 改灵敏度：`drawdown_bands_pct` 是回撤档位；`intraday_plunge_pct` 是当日大跌阈值（设 0 关闭）。
- **想改运行频率**：编辑 `.github/workflows/monitor.yml` 里的 `cron`。注意 GitHub 定时是 UTC 时间，且为“尽力而为”，偶尔会延迟几分钟。

---

## 本地测试（可选）

不想等 GitHub，也可在自己电脑上直接跑（需要 Python 3，**无需安装任何第三方库**）：

```bash
# 不带密码 = 演练模式：只拉行情、写 quotes.json、打印将发的告警，不真正发邮件
python monitor.py

# 带上 Gmail 才会真正发邮件
export GMAIL_USER="zhangwhthu@gmail.com"
export GMAIL_APP_PASSWORD="abcdefghijklmnop"
python monitor.py

# 本地预览看板网页
cd docs && python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

---

## 常见问题

- **邮件没收到？** 先看 Actions 运行日志有没有 `[email] 已发送`；再检查垃圾邮件箱；确认两步验证已开、用的是**应用专用密码**而不是登录密码。
- **某只股票显示「拉取失败」？** 多半是代码不对。去 Yahoo Finance 搜公司名，用它地址栏/标题里的代码（瑞典股带 `.ST`），改 `config.json`。
- **Yahoo 偶尔限流？** 脚本已带重试和温和限速；偶发失败下一轮（15 分钟后）会自动恢复，不影响告警逻辑。
- **看板数据有点旧？** 正常。数据每 ~15 分钟更新一次；非交易时段（周末/盘后）不更新，页面会标注“数据较旧”。这套系统用于捕捉“大跌”足够，不适合做秒级盯盘。
- **想完全私有？** 私有仓库也能用，但 Actions 每月有 2000 分钟免费额度；本项目按当前频率约用 1000–1200 分钟/月，够用。公开仓库则无限免费。
