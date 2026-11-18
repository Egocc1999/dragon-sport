# DRAGON SPORTS

足球分析提示網站 V1：以可重現模型在開波前鎖定提示，賽後按官方賽果結算，並公開紙上往績。

目前是紙上往績階段，**尚未收費、沒有登入或付費牆**。鎖定後的提示會公開，方便核對。這是分析資料，不是博彩公司，亦不推介任何投注站。模型估計**不保證獲勝**。

V1 只覆蓋：**英超、西甲、德甲、法甲**。不含意甲、歐聯。

所有向使用者顯示的時間均為 **Asia/Hong_Kong（UTC+8）**，並標明「香港時間」。

## 本機預覽網站

網站是靜態頁面（`index.html`、`styles.css`、`app.js`），以相對路徑讀取 `./data/tips.json` 與 `./data/fixtures.json`。用手機 Safari 預覽前，請先開本機伺服器（直接用檔案協議開啟可能無法讀取 JSON）：

```bash
cd /workspace/dragon-sports
python3 -m http.server 8766
```

然後在瀏覽器開啟：<http://127.0.0.1:8766/>

預設讀取的是空白真實資料。若尚未有已鎖定提示，頁面會清楚顯示「尚未有提示／尚未有往績」，**不會出現假作成績的零勝率**。

若只想預覽卡片排版，可開啟 `http://127.0.0.1:8766/?demo=1`。示範模式會讀取 `data/example-tips.json`，並在頁面頂部標明「示範資料，非真實往績」。

稍後可用 GitHub Pages 託管公開紙上往績：把本目錄的靜態檔與 `data/tips.json` 一併發佈即可。鎖定等於把帶有香港時間時戳的 JSON 公開出去；賽後只附加賽果，不改歷史。

把網站加到 iPhone 主畫面：用 Safari 開啟頁面，再經分享選單加入主畫面。V1 已準備好 PWA 清單與圖示。

## 執行資料管線

請在專案根目錄、使用 Python 3.11 或以上。管線只用標準庫，不必安裝額外套件。

```bash
cd /workspace/dragon-sports
python3 -m pipeline.fetch            # 擷取賽程、賽果、積分榜；預設不取賠率
python3 -m pipeline.fetch --no-odds  # 同上（明確略過賠率）
python3 -m pipeline.fetch --odds     # 嘗試取聯賽／日期參考賠率（免費方案通常會拒絕，勿輕易使用）
python3 -m pipeline.fetch --verify-leagues  # 額外核對聯賽編號
python3 -m pipeline.propose          # 寫出尚未鎖定的提案
python3 -m pipeline.lock             # 只鎖定仍未開波的提案
python3 -m pipeline.lock --dry-run
python3 -m pipeline.grade            # 以官方全場賽果結算
python3 -m pipeline.grade --dry-run
python3 -m unittest discover -s tests -v
```

即時資料來自 **5DollarFootballAPI**（<https://5dollarfootballapi.com>）。把金鑰放到環境變數 `FIVE_DOLLAR_FOOTBALL_API_KEY`，或寫入專案根目錄的 `.env` / `.api_key`（已列入 `.gitignore`，切勿提交）。相容介面的請求標頭為 `x-apisports-key`。舊變數名 `API_FOOTBALL_KEY` 僅作後備。

沒有金鑰時，`python3 -m pipeline.fetch` 會清楚報錯並**不會呼叫即時 API**；網站仍可用空白的 `data/tips.json` 預覽。

回應會快取在 `cache/`，以免耗盡免費方案每小時 60 次請求的額度。額度按時段計算，並非每日上限。可用 `--force` 忽略快取，但不要為了重試而濫用。

聯賽編號與賽季寫在 `pipeline/leagues.json`，也可用環境變數 `DRAGON_SPORTS_SEASON` 覆寫賽季。預設賽季為 2026（即 2026-27 球季）。編號已於 2026-11-17 以 5DollarFootballAPI 即時核對：英超 4160026622、西甲 4212821298、德甲 686337048、法甲 3614399544。可用 `fetch --verify-leagues` 再核對名稱。

**免費方案限制：** 已核實可取得四大聯賽 2026-27 賽季的賽程、賽果與積分榜。每小時約 60 次請求；歷史資料窗口約三個月；免費方案不提供按聯賽或日期列出的賠率，因此預設擷取會略過賠率。不要把原始資料饋送轉售或再分發。公開頁面必須標明來源：`Football data by 5DollarFootballAPI`（連結至 <https://5dollarfootballapi.com>）。

目前尚未鎖定任何提示，網站預設仍是空白真實資料，並沒有公開勝率或即時 tip。

模型規則見 `pipeline/MODEL.md`。日常操作見 `OPERATIONS.md`。

## 公開資料約定

- `data/tips.json`：網站預設讀取的真實資料。無提示時，`tips` 為空陣列。
- `data/fixtures.json`：公開賽程頁使用的場次清單，按開波時間（香港時間）排列；由 `pipeline.fetch` 從擷取結果產生，未開波場次不含比分。
- `data/stats.json`：由已結算提示重算的匯總。沒有往績時 `has_settled_record` 為 false，`overall` 為 null。
- `data/locked/*.json`：每條已鎖定提示的原始檔。鎖定後只可附加賽果與盈虧。
- `data/proposed/proposals.json`：尚未鎖定的提案，不會出現在公開頁面。
- `data/example-tips.json`：示範資料，僅供排版預覽。

## 文案原則

不要使用「成日中」或任何保證勝出的說法。示範資料必須標明「示範資料，非真實往績」。介面與說明一律使用香港繁體中文。
