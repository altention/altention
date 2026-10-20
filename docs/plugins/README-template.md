# 插件 README 的寫法

外部插件（自己的 repo，裝在 `~/altention/plugins/<名稱>`）的 README 照這份寫，看起來跟主專案的
[README.md](../../README.md) 是同一套東西。讀的人是第一次看到這個插件、想知道要不要裝的使用者，不是寫插件的人。

## 規則

**檔案**

- `README.md`：英文，是主體。開頭一句話之後放一行 `[繁體中文說明](README.zh-TW.md)`。
- `README.zh-TW.md`：繁體中文，段落跟英文版一樣、順序一樣，開頭放 `[English](README.md)`。兩份說的事要一樣，一份改了另一份也改。
- 截圖或示意圖放在 repo 裡（例如 `docs/screenshot.png`），用相對路徑引用。圖裡不要有真的頻道名、機器名、帳號或對話內容，用假的值。

**段落**（照這個順序，標題用下面的字；某段真的沒東西也留著，寫一句「沒有」）

1. 標題和一句話：它做什麼，接一張截圖或示意圖。
2. What it does／它做什麼：使用者得到什麼，幾個重點。
3. Requirements／需求：要先開哪些插件（plugin.json 的「需要」）、要哪些系統套件，每個都寫 Debian/Ubuntu 和 macOS 怎麼裝。
4. Install／安裝：見下面的「Install 段落」。
5. Use／用法：使用者怎麼叫它（說什麼話、哪個指令或按鈕）、在哪個頻道。
6. Settings／設定：實例 `plugins.json` 裡的每個鍵、預設值、改了會怎樣，用一段 JSON 示範。排程的名字和預設時間也寫在這裡。寫怎麼在某台關掉（`disabled_on`）時，一起寫要自己刪掉手動連的 skill（核心之後會自動做）。
7. Data and privacy／資料與隱私：三件事都要回答：
   - 資料存在哪（資料夾的完整預設路徑）。
   - 會不會進 git、推到哪裡（不會、只在本機的 git、會推到使用者自己的私有備份）。
   - 會不會花訂閱額度：什麼時候花、大概多少，以及怎麼關掉。
8. Known limitations／已知限制：還沒做的、做不到的，用使用者看得懂的話說。
9. License／授權：預設是 MIT。repo 放一份 `LICENSE`，照引擎的寫（第一行 `MIT License`，版權行 `Copyright (c) 2026 The Altention authors`）；README 寫「MIT，見 LICENSE」。第三方插件可以自己選授權，但要寫明，並附上那份授權的 `LICENSE`。

**不寫**

- 內部代號：C-xx、I-xx、S1、U3、「3c」這類階段編號、審查頻道的名字。改寫成使用者看得懂的話，例如：
  - 不寫「C-61 之前要手動」，寫「目前要手動做；之後核心會自動做」。
  - 不寫「按鈕作答等 3c」，寫「目前只能打字作答，用按鈕作答還不支援」。
- token、金鑰這類秘密的值：插件用到秘密時，只寫它要放在哪個路徑（plugin.json 的 `secrets`），不寫值本身，截圖裡也不能出現。
- 機器名、帳號、頻道 ID、私人網址、只在某一台才有的路徑。路徑用預設值寫（`~/altention/plugins/<名稱>`、`~/altention/data/<名稱>`）。
- 開發過程：誰決定了什麼、改過幾版、交接的筆記。這些放 `docs/`（例如 `docs/decisions.md`），README 最多連過去。
- 給開發者看的細節（程式怎麼寫、函式名稱、測試怎麼跑）放到 README 最後的「Development／開發」一節，或放 `docs/`。前面九段不寫這些。

**Install 段落**

核心現在不會替外部插件跑安裝步驟、也不會連它的 skill，所以先寫現在的手動步驟，再加一句之後會自動做（核心做到了再把手動那段刪掉）：

- 英文：`The core does not do these steps for external plugins yet; it will do them on its own later.`
- 中文：`核心現在還不會替外部插件做這些步驟，之後會自動做。`

手動步驟照這個順序（沒有的那步就省略）：

1. 把 repo clone 到 `~/altention/plugins/<名稱>`。
2. 插件自己的安裝指令（下載工具、建資料夾），寫明要在哪幾台跑（`machines` 是 `all` 就每台都跑，是 `one` 就只在跑它的那台跑）。
3. 連 skill：`mkdir -p ~/.claude/skills && ln -sfn ~/altention/plugins/<名稱>/skill ~/.claude/skills/<名稱>`。
4. 讓核心裝上排程和頻道：在引擎跑 `./install.sh`，再跑 `python3 ops/doctor.py` 照它說的補設定。
5. 一行確認裝好了的方法（例如 `python3 ~/altention/engine/ops/plugins.py list` 看得到它）。

## 範本：README.md

````markdown
# <Name>

<One sentence: what it does for the user.>

[繁體中文說明](README.zh-TW.md)

![<what the picture shows>](docs/screenshot.png)

An [Altention](https://github.com/q12we34rt5/altention) plugin.

## What it does

- **<Point>.** <One or two sentences.>
- **<Point>.** <…>

## Requirements

- Altention <version> or later
- <Other plugins it needs, or "No other plugins.">
- <System packages: `sudo apt install …` (Debian, Ubuntu); `brew install …` (macOS). Or "Nothing else.">

## Install

The core does not do these steps for external plugins yet; it will do them on its own later.

```bash
git clone <repository URL> ~/altention/plugins/<name>
<the plugin's own setup command>          # <on every machine | on the machine it runs on>
mkdir -p ~/.claude/skills && ln -sfn ~/altention/plugins/<name>/skill ~/.claude/skills/<name>
cd ~/altention/engine && ./install.sh && python3 ops/doctor.py
```

<How to tell it is installed.>

## Use

<What the user says or presses, and in which channel. One short example.>

## Settings

In the instance's `plugins.json`:

```json
{
  "<section>": {"<key>": <default>},
  "plugins": {"<name>": {"schedules": {"<schedule>": {"when": {"at": "13:00", "days": "daily"}}}}}
}
```

- `<section>.<key>` (default `<value>`): <what it changes>.
- Schedule `<schedule>` (default <time>): <what it does>. Turn it off with `{"enabled": false}`.
- Turn the plugin off on a machine: add the machine to `plugins.<name>.disabled_on`. On that machine, also delete
  the skill link you made, `~/.claude/skills/<name>`; the core will do it on its own later.

## Data and privacy

- **Where:** <folder>.
- **Git:** <not in git | a local git repository only | pushed to your own private backup>.
- **Subscription:** <never | each time …, about …>. <How to stop it.>

## Known limitations

- <In the user's words.>

## License

MIT; see [LICENSE](LICENSE).
````

## 範本：README.zh-TW.md

````markdown
# <名稱>

<一句話：它替使用者做什麼。>

[English](README.md)

![<圖裡是什麼>](docs/screenshot.png)

[Altention](https://github.com/q12we34rt5/altention) 的插件。

## 它做什麼

- **<重點>。** <一兩句。>

## 需求

- Altention <版本> 以上
- <要先開的插件，或「不需要其他插件。」>
- <系統套件：`sudo apt install …`（Debian、Ubuntu）；`brew install …`（macOS）。或「不用另外裝。」>

## 安裝

核心現在還不會替外部插件做這些步驟，之後會自動做。

```bash
git clone <repo 網址> ~/altention/plugins/<名稱>
<插件自己的安裝指令>          # <每台都跑｜只在跑它的那台跑>
mkdir -p ~/.claude/skills && ln -sfn ~/altention/plugins/<名稱>/skill ~/.claude/skills/<名稱>
cd ~/altention/engine && ./install.sh && python3 ops/doctor.py
```

<怎麼確認裝好了。>

## 用法

<使用者說什麼或按什麼、在哪個頻道。一個短例子。>

## 設定

實例的 `plugins.json`：

```json
{
  "<區塊>": {"<鍵>": <預設值>},
  "plugins": {"<名稱>": {"schedules": {"<排程>": {"when": {"at": "13:00", "days": "daily"}}}}}
}
```

- `<區塊>.<鍵>`（預設 `<值>`）：<改了會怎樣>。
- 排程 `<排程>`（預設 <時間>）：<做什麼>。只關這一項寫 `{"enabled": false}`。
- 在某台關掉這個插件：把那台加進 `plugins.<名稱>.disabled_on`。那台手動連的 skill 要自己刪掉
  `~/.claude/skills/<名稱>`；之後核心會自動做。

## 資料與隱私

- **存在哪：** <資料夾>。
- **git：** <不進 git｜只在本機的 git｜推到你自己的私有備份>。
- **訂閱額度：** <不花｜每次…，大約…>。<怎麼關掉。>

## 已知限制

- <用使用者的話說。>

## 授權

MIT，見 [LICENSE](LICENSE)。
````

## 照範本改寫的大綱

只列每段要寫什麼，內容由各插件自己寫。現在的 README 裡要拿掉的：內部代號、`docs/decisions.md` 以外的開發過程，以及放在前面的實作細節。

### display

1. 一句話：Claude 在 Discord 給你看公式、表格、圖表、流程圖、程式差異時，先轉成圖片再貼。截圖：同一則回覆，左邊是原始文字，右邊是轉好的圖。
2. 它做什麼：支援的種類，用使用者的話說（數學式、表格、資料圖表、流程圖和時序圖、程式差異、終端機顏色、SVG／HTML／PDF、完整 LaTeX、matplotlib）；深底、可以改白底；也會在 Discord 自己就排得好的時候直接用程式碼區塊。
3. 需求：Chrome 或 Chromium（它怎麼找）；`pdf`、`tex` 要 poppler；其他工具第一次安裝時會下載。不需要其他插件。
4. 安裝：每台都要跑。步驟是 clone、`bin/display --setup all`（下載什麼、放哪、不用 sudo）、連 skill、`bin/display --check`。最後加「之後核心會自動做」那句。
5. 用法：在任何頻道說「畫出來」「轉成圖」「公式看不懂」，Claude 該用的時候也會自己用。終端機的 `display` 指令放一個例子就好。
6. 設定：plugins.json 沒有自己的鍵，只有 `disabled_on`；環境變數 `DISPLAY_CHROME`。
7. 資料與隱私：工具和輸出放在 `~/.cache/altention-display`，輸出一天後清掉；不進 git；不花訂閱額度（這段要核對）。另外說明畫圖時網路是擋掉的、給它的 HTML 不能讀這台的檔案，現在「安全」那段改寫成給使用者看的兩三句。
8. 已知限制：例如第一次編 LaTeX 要下載約 40MB、要等幾分鐘，`plot` 會執行給它的 Python 腳本。
9. 授權：MIT。repo 裡現在沒有 `LICENSE`，照引擎的那份加上（`Copyright (c) 2026 The Altention authors`）。
- 種類清單的完整參數和測試怎麼跑，移到最後的「Development」或 `skill/SKILL.md`。

### learn

1. 一句話：一個學習頻道，裡面的 Claude 當老師，每天固定時間出練習、改答案、記下進度和常錯的地方。截圖：一則練習題加上批改。
2. 它做什麼：自己定主題和時間；之後跟那個頻道說就能改主題或時間；會記得你學到哪、常錯什麼。
3. 需求：不需要其他插件；不用另外裝系統套件（這點要核對）。
4. 安裝：只在跑它的那台跑。步驟是 clone、`install.sh`（建學習資料夾，可以重跑，不會蓋掉筆記）、在引擎跑 `./install.sh`（裝上排程）、doctor（`#learn` 頻道還沒設定時，它會說怎麼開；這段要核對）。最後加「之後核心會自動做」那句。
5. 用法：在 `#learn` 跟 Claude 說想學什麼；每天 13:00 會出題，用打字作答。
6. 設定：`learn.channel`（頻道 ID，安裝時寫入）；排程 `practice`（預設每天 13:00），用 `plugins.learn.schedules.practice` 改時間或關掉；跑在哪台用 `plugins.learn.on`。
7. 資料與隱私：放在 `~/altention/data/learn`（權限 700），是一個本機的 git repo，筆記只在這裡，不進插件的 repo；同步會定時提交，會不會推、推到哪要寫清楚（這段要核對）；每次出題、改答案都花訂閱額度，關掉排程就不會定時出題。
8. 已知限制：目前只能打字作答，用按鈕作答還不支援。
9. 授權：MIT。repo 裡現在沒有 `LICENSE`，照引擎的那份加上（`Copyright (c) 2026 The Altention authors`）。
- `plugin.json`、`room/CLAUDE.md` 各管什麼，移到最後的「Development」。
