# Data contracts

数据组（pipeline）和前端组（web）之间只通过这几份 JSON 交接。改格式之前先在群里说一声，并同时更新这份文档。

## 通用约定

- **时间**：数据里一律存 **UTC** 的 ISO 8601 字符串，例如 `"2019-08-14T06:00:00Z"`。Copernicus 数据本身就是 UTC。界面上再换算成当地时间显示（巴利阿里群岛夏季为 UTC+2，冬季为 UTC+1）。
- **坐标**：`[lon, lat]`，十进制度。
- **单位**：浪高 m，周期 s，方向是“从哪里来”的真方位角（°），海流方向是“流向哪里”（°），航速 kn，距离 nm。
- **航线代号**：`A`、`B`、`C`，在所有案件中含义固定。

## 1. Case file — `web/public/cases/<id>.json`

由 `pipeline/` 生成，前端只读。

```json
{
  "id": "c07",
  "date": "2019-08-14",
  "title": "北风来临前",
  "goal": "beam_seas",
  "key_evidence": "wave_dir",
  "vessel": { "speed_kn": 6.0 },
  "limits": {
    "beam_hs": 1.5,
    "max_hs": 2.5,
    "arrive_by": "2019-08-14T18:30:00Z",
    "source": "阈值出处（规范或教材名称、页码）"
  },
  "routes": {
    "A": { "name": "直线横渡", "points": [[3.14, 39.84], [3.83, 40.00]], "nm": 33.2 }
  },
  "grid": {
    "lon": [3.0, 3.05],
    "lat": [39.7, 39.75],
    "times": ["2019-08-14T04:00:00Z"],
    "layers": {
      "hs":        { "unit": "m",   "values": [[[0.7]]] },
      "wave_dir":  { "unit": "deg", "values": [[[5]]] },
      "period":    { "unit": "s",   "values": [[[5.2]]] },
      "current_u": { "unit": "m/s", "values": [[[0.0]]] },
      "current_v": { "unit": "m/s", "values": [[[0.0]]] },
      "wind":      { "unit": "kn",  "values": [[[8]]] },
      "sst":       { "unit": "degC","values": [[[26.1]]] }
    }
  },
  "forecast_grid": null,
  "sims": {
    "2019-08-14T05:00:00Z": {
      "A": {
        "arrival": "2019-08-14T10:54:00Z",
        "beam_hours": 0.0,
        "over_hours": 0.0,
        "max_hs": 1.1,
        "late": false,
        "safe": true,
        "track": [{ "t": "2019-08-14T05:00:00Z", "lon": 3.14, "lat": 39.84, "hs": 0.7, "category": "beam", "bad": false }]
      }
    }
  },
  "flips": [
    { "at": "2019-08-14T05:00:00Z", "from": "A", "to": "C", "reason": "beam_hours" },
    { "at": "2019-08-14T12:00:00Z", "from": "C", "to": null, "reason": "late" }
  ],
  "copernicus_products": ["<product id>", "<dataset id>"]
}
```

说明：

- `grid.layers.*.values` 的维度顺序是 `[time][lat][lon]`。只裁剪航线周边的小区域，控制文件大小。
- `forecast_grid`：如果能拿到当天真实发布的历史预报，就放在这里，格式同 `grid`；拿不到就填 `null`，调查环节改用出发时刻之前的 `grid` 数据，并在方法说明里写明。
- `sims` 的键是出发时间，建议 30 分钟一档。每档里三条航线都要有。
- `flips[].to` 为 `null` 表示这个时间之后没有安全方案。`reason` 取值：`beam_hours` | `over_hours` | `late` | `slower`。
- `goal` 取值见下方“学习目标”。`key_evidence` 取 `grid.layers` 的键名（`current_u`/`current_v` 统一写成 `current`）。

## 2. Decision record

学生提交判断时由前端生成。发给分析引擎，也发给 `/api/log`。

```json
{
  "session": "随机生成的匿名 ID",
  "case": "c07",
  "opened": ["hs", "wind", "sst"],
  "route": "A",
  "departure": "2019-08-14T06:00:00Z",
  "reasons": ["hs", "shortest"],
  "note": "浪不高，所以选最短的",
  "submitted_at": "2026-10-30T10:12:00Z"
}
```

`reasons` 取值：`hs` | `wave_dir` | `period` | `current` | `wind` | `shortest` | `arrival`。不收集姓名、邮箱等任何个人信息。

## 3. Analysis result

前端的 `analysis.js` 用 case file 和 decision record 算出。**这是 AI 教练的全部输入**，AI 不接触原始海况数据。

```json
{
  "case": "c07",
  "safe": false,
  "facts": { "max_hs": 1.9, "beam_hours": 1.3, "arrival": "2019-08-14T11:55:00Z" },
  "missed_key_evidence": "wave_dir",
  "better_option": { "route": "C", "arrival": "2019-08-14T12:35:00Z" },
  "last_safe_departure_for_choice": "2019-08-14T05:00:00Z",
  "weak_goal": "beam_seas",
  "next_case": "c12",
  "student_reasons": ["hs", "shortest"],
  "student_note": "浪不高，所以选最短的"
}
```

`better_option` 在学生已选最优时为 `null`；`last_safe_departure_for_choice` 在所选航线全天都不安全时为 `null`。

## 4. Coach response — `POST /api/coach`

请求体是上面的 analysis result，返回：

```json
{ "feedback": "……", "numbers_used": ["1.9", "1.3", "05:00"], "source": "ai" }
```

`source` 为 `"ai"` 或 `"template"`。服务端检查 `feedback` 中出现的每个数字和时间都能在请求体里找到，对不上或超时就返回模板文字，`source` 设为 `"template"`。

## 学习目标

| 键 | 名称 | 对应证据 |
|---|---|---|
| `wind_vs_swell` | 区分风浪和涌浪 | `period` |
| `beam_seas` | 浪向与航向 | `wave_dir` |
| `current_time` | 海流与航行时间 | `current` |
| `time_window` | 出发时间窗口 | 决策地图 |
