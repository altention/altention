# dcbot 文件範本：變數與展開規則

這個資料夾是 `dcbot/CLAUDE.md` 和 `dcbot/GUIDE.md` 範本化的草稿（階段 2.4），以 commit `64b1a63` 的兩份檔為底。`dcbot/` 只由開發頻道改，所以草稿放在這裡，由 #bot-dev 併進去。

- `CLAUDE.md`：公開版的 `dcbot/CLAUDE.md`。**沒有變數**，直接取代現在那份。
- `GUIDE.md`：公開版的 `dcbot/GUIDE.md`，是範本，貼到說明頻道前要展開。
- `instance-appendix.md`：使用者自己這個安裝的內容。**含個人資訊，要放進實例，不能留在引擎。**
- `variables.md`：這份。併入後建議放在 `dcbot/GUIDE.variables.md`（`CLAUDE.md` 草稿裡是這樣指的）。

## 為什麼兩份的做法不同

- `GUIDE.md` 是貼給使用者看的，句子裡要出現真的機器名和頻道名，所以用範本，由 `dcctl guide` 在貼出前展開。
- `CLAUDE.md` 是 Claude Code 直接從 `dcbot/` 讀的。要展開就得在引擎資料夾裡產生一份因安裝而異的檔案，引擎的工作樹會變髒，引擎更新遇到變動也會跳過。所以它不用變數：通用的寫法留在公開版，這個安裝特有的內容放 `<實例>/dcbot/CLAUDE.md`（實例附錄），公開版開頭叫 Claude 去讀。不用 `@路徑` 引入，那可能跳確認，會卡住 tmux 裡的對話。

## 範本語法

只有四種，用標準函式庫的 `re` 就能做（`dcctl` 用系統 Python 3.8 跑）：

- `{{名字}}`：換成值。
- `{{#名字}}…{{/名字}}`：值不是空字串才保留中間的字。
- `{{^名字}}…{{/名字}}`：值是空字串才保留。
- `{{> 名字}}`：整段換成一個檔案的內容（去掉結尾的換行）。

規則：

- 不能巢狀使用同一個名字的區段；區段裡可以有 `{{名字}}`。
- 展開順序：引入 → 區段 → 變數。引入的檔案裡也可以用變數。
- 遇到不認得的名字就失敗，不貼出去。設定值不是文字（例如寫成數字或清單）也一樣，`dcctl guide` 會講出是哪個名稱、對應實例 `plugins.json` 的哪個設定。說明頻道同步失敗本來就不擋部署（`deploy.sh` 的 `sync_guide`）。
- 展開後才切段（`guide_chunks()`）。每個 `## ` 段落展開後仍要在 1900 字以內；用使用者現在的值最長的一段是 1606 字。

參考實作（驗證用的就是這段）：

```python
import re

def expand(text, values, includes):
    text = re.sub(r"\{\{> *([\w.]+) *\}\}", lambda m: includes[m.group(1)].rstrip("\n"), text)
    def section(m):
        keep = bool(values[m.group(2)]) == (m.group(1) == "#")
        return m.group(3) if keep else ""
    text = re.sub(r"\{\{([#^])([\w.]+)\}\}(.*?)\{\{/\2\}\}", section, text, flags=re.S)
    return re.sub(r"\{\{([\w.]+)\}\}", lambda m: values[m.group(1)], text)
```

建議 `dcctl guide` 加 `--check`：只印展開後的文字，不碰 Discord。

## 變數從哪裡來

一律經過 `altention/config.py`，不自己讀檔。下面的 `config` 就是它。

### 從機器清單算出來的（不用設定）

- `bot_machine`：`config.bot_machine()[0]`；是 `None` 就用 `config.this_machine()`。
- `attach_machines`：`config.machines()` 裡有 `dcattach` 角色、有 SSH 別名、不是機器人那台的機器，依名稱排序（跟設定卡的下拉選單 `sorted(remote.hosts())` 同一個順序），用「、」接起來。沒有就是空字串，所以它同時當「是不是多機」的開關。
- `machines`：機器人那台，加上 `attach_machines` 的各台。
- 顯示名稱：上面三個都先查 `config.setting("core.guide.machine_titles", {})`（名稱 → 顯示名稱），查不到就用清單裡的名稱。清單的鍵常是小寫（例如 `host-b`），說明裡想寫成別的樣子時用這個。

沒有清單（單機）時：`bot_machine` 是這台的名字，`attach_machines` 是空字串，說明裡跟別台機器有關的句子會自動消失或換成「怎麼加一台」。

### 從實例設定讀的（`plugins.json`，都有預設值）

除了 `default_cwd`，都在 `core.guide` 底下，例如 `config.setting("core.guide.example_board", "")`。

- `default_cwd`（設定是 `core.default_cwd`，不在 `core.guide` 裡，因為機器人也要讀）：新頻道預設的工作目錄，顯示時把家目錄縮成 `~`。**這個值要跟機器人實際用的一致**：現在寫死在 `bot/main.py`（`os.path.dirname(config.ROOT)`，就是引擎）。建議兩邊都讀 `config.setting("core.default_cwd")`，預設是實例資料夾，沒有實例就用引擎（DESIGN.md 第 5.4 節）。
- `example_channel_a`、`example_channel_b`：舉例用的兩個頻道。預設 `#project-a`、`#project-b`。
- `example_board`：看板的例子，整句放進「（例如 …）」。預設空字串，那個括號就不出現。
- `example_machine`：「（某機器 頻道就看 某機器 的檔案）」裡的機器。預設是 `attach_machines` 的第一台的顯示名稱，沒有就是空字串。
- `skill_examples`：「Claude Code 的指令和 skill」那一段列的 skill。預設 `` `/deep-research`、`/code-review`、`/simplify`、`/loop`、`/schedule` ``（Claude Code 內建的）。插件執行層上線後，可以改成自動加上已啟用插件的全域 skill。

### 引入的檔案

- `{{> channels}}`：「其他頻道」那一段的內容。先找 `<實例>/dcbot/guide/channels.md`，沒有就用引擎的預設 `dcbot/guide/channels.md`。引擎的預設只列核心自己會建的頻道：

```
- `{{chat_channel}}`：一般對話，Claude 的工作目錄是實例資料夾。
- `{{audit_channel}}`：機器人做過的每件事都記在這裡（上線、部署、退回也報在這裡）。
- `#notifications`：不屬於任何專案的通知。
- **要叫機器人修改自己**：新開一個頻道（名字隨你，例如 `#bot-dev`），設定卡選 🤖 Claude 對話，📁 選引擎資料夾 `{{engine}}`。
```

- `{{chat_channel}}`、`{{audit_channel}}`、`{{guide_channel}}`：機器人自己的三個頻道的名字（含 `#`），從實例 `plugins.json` 的 `dcbot.channels`（`chat`、`audit`、`guide`）讀，沒寫就是 `#chat`、`#audit`、`#guide`。新安裝第一次上線時建 `#audit` 和 `#chat`（`#chat` 直接設定好，工作目錄是實例資料夾）；說明頻道由 `dcctl guide` 建。開發頻道不自動建。
- `{{engine}}`：引擎資料夾（家目錄寫成 `~`）。

沒有實例時，一律用預設值和引擎的預設檔，說明照樣貼得出來。

## 沒有做成變數的（刻意留著）

- `#reports`、`#notifications`、`#bot-dev`（開發頻道的名字例子）：通用的例子，不是變數。
- `tmux -L dcbot`：機器人自己的 tmux 名稱（`TMUX_SOCKET`），同上。
- 時區、介面語言：不在這兩份文件裡。

## 怎麼驗證的

- 用上面的參考實作加 `instance-appendix.md` 列的值展開 `GUIDE.md`，結果跟 `64b1a63` 的 `dcbot/GUIDE.md` 逐字相同（8054 字）。
- 用單機的假值（`host-a`、沒有別台機器、`example_board` 和 `example_machine` 是空字串）展開：沒有殘留的 `{{`，每一段都在 1900 字以內。
- `bot_machine`、`attach_machines`、`machines` 用 `altention/config.py` 對現在的機器清單實際算過，加上一筆 `machine_titles` 後跟說明裡現在寫的相同。
- `CLAUDE.md`、`GUIDE.md` 兩份草稿搜過機器名、帳號、私人 repo 名、私人頻道名和對話名，都沒有。

## 併入時要做的事（給 #bot-dev）

1. `dcbot/GUIDE.md` 換成這裡的 `GUIDE.md`；`tools/dcctl` 的 `guide()` 在 `guide_chunks()` 之前展開；加引擎的預設檔 `dcbot/guide/channels.md`。
2. `dcbot/CLAUDE.md` 換成這裡的 `CLAUDE.md`。兩份都以 `64b1a63` 為底，之後 `dcbot/` 那兩份有新的修改要再對一次（`git diff 64b1a63 -- dcbot/CLAUDE.md dcbot/GUIDE.md`）。
3. `instance-appendix.md` 的三個部分分別放進實例（階段 2.3a 建好之後）：`plugins.json` 的 `core.guide`、`dcbot/guide/channels.md`、`dcbot/CLAUDE.md`。
4. 實例還沒建好的這段期間，展開用的值從哪裡來要先決定，否則說明頻道會被改成預設值的版本。最簡單的是等實例建好再換 `GUIDE.md`。
