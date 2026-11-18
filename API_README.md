# 东阿驴卵泡预测接口

该接口将现有V1模型封装为标准HTTP JSON服务。外部系统不需要读取Excel，也不需要运行Streamlit。

## 接口

| 方法 | 路径 | 用途 | 是否需要API Key |
| --- | --- | --- | --- |
| GET | `/` | 人工录入与动态预测图页面 | 页面内调用预测时需要 |
| GET | `/health` | 服务健康检查 | 否 |
| GET | `/v1/model/info` | 模型版本、参数范围和验证说明 | 是 |
| POST | `/v1/predict` | 单次卵泡成熟与排卵预测 | 是 |
| GET | `/docs` | Swagger接口文档和在线调试 | 由环境变量控制 |

接口版本放在URL中。未来修改字段或计算逻辑时，保持`/v1`兼容；不兼容修改使用新的主版本路径。

## Windows本地启动

在项目目录打开PowerShell，安装接口依赖：

```powershell
python -m pip install -r .\requirements-api.txt
```

设置一个强API Key并启动：

```powershell
$env:DONKEY_API_KEY="请替换成长随机字符串"; python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/` 使用预测页面；打开 `http://127.0.0.1:8000/docs` 查看接口文档。

## Docker部署

复制环境变量示例并修改API Key：

```powershell
Copy-Item .env.example .env
```

启动服务：

```powershell
docker compose up -d --build
```

生产环境应通过公司API网关或Nginx配置HTTPS，限制来源IP，并保存调用量、耗时和错误码；不要记录养殖业务明细或完整请求体。

## 请求示例

```json
{
  "client_request_id": "EO-20260923-0001",
  "animal_id": "370001",
  "current_time": "2026-09-23T10:00:00+08:00",
  "breed_profile": "dezhou",
  "breeding_method": "fresh",
  "breeding_lead_hours": null,
  "left": {"status": "mm", "value_mm": 35.0},
  "right": {"status": "SF", "value_mm": null},
  "previous": {
    "time": "2026-09-22T10:00:00+08:00",
    "max_follicle_mm": 31.0
  },
  "induction": {"used": false, "injection_time": null}
}
```

状态值：

- `mm`：30～60 mm数值（含30和60），此时必须提供`value_mm`；
- `SF`：小于30 mm的小卵泡，此时`value_mm`必须为`null`；
- `CL`：已观察到黄体，此时接口返回`post_ovulation`，不继续预测未来排卵。

左右侧的毫米数值分别校验；即使另一侧有效，任意一侧以`mm`提交小于30或大于60的数值也会返回HTTP 422。`previous.max_follicle_mm`同样限定30～60 mm；上次仅有SF时省略`previous`或设为`null`。左右均为SF时无法预测，返回HTTP 422。小于30的数值不会自动改写成SF，请调用方明确提交状态。

所有时间必须使用ISO 8601格式并带时区，例如`2026-09-23T10:00:00+08:00`。

鲜精常规调用不需要配种参数。以下可选参数保留上一版接口兼容行为：

- `breeding_method`：默认`fresh`，不填写也直接给出鲜精建议；仍兼容`unspecified`、`natural`、`cooled`或`frozen`的显式请求。
- `breeding_lead_hours`：默认`null`，鲜精使用24小时参考提前量。保留显式提交非负有限提前量的旧版兼容行为；冷藏/冻精仍需明确提前量。常规鲜精调用不必提供该字段。
- 该提前量是“距预计排卵提前多久”，不是“促排注射后多久”。`induction.used=true`时，不使用该简化公式生成配种计划，需具体促排配种方案。

## PowerShell调用示例

```powershell
$headers=@{"X-API-Key"="请替换成实际API Key"}; $body=@{client_request_id="EO-20260923-0001";animal_id="370001";current_time="2026-09-23T10:00:00+08:00";breed_profile="dezhou";breeding_method="fresh";left=@{status="mm";value_mm=35.0};right=@{status="SF";value_mm=$null};induction=@{used=$false;injection_time=$null}} | ConvertTo-Json -Depth 6; Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/predict" -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

也可以设置`DONKEY_API_BASE_URL`和`DONKEY_API_KEY`后运行：

```powershell
python .\examples\client.py
```

## 返回结果重点字段

- `prediction_status`：`predicted`或`post_ovulation`；
- `point_estimate_policy`：`window_lower_bound`，单点输出采用对应最终窗口的最早时间；CL状态下为`null`；
- **`maturity_prediction`、`ovulation_prediction`**：对外使用的偏早单点预测，包含`hours`（距当前摸排的小时数）和`time`（带时区的时间）；分别等于对应窗口的`lower_hours`和`lower_time`；
- `maturity_center`、`maturity_window`：达到成熟参考直径的中心时间和范围；
- `ovulation_center`、`ovulation_window`：预计排卵中心和风险窗口；
- `breeding_recommendation`：建议配种时间，含`hours`及`time`；默认鲜精取`max(0,ovulation_prediction.hours-24)`小时后；
- `breeding_status`：`planned`（已计算，待现场确认）、`method_required`（缺少方式）、`protocol_required`（需明确方案）或`post_ovulation`（已观察到CL）；
- `breeding_method`、`breeding_lead_hours`、`breeding_lead_source`：本次采用方式、提前量及其来源（`initial_default`或`site_confirmed`）；
- `breeding_policy`：能够计算时为`early_ovulation_minus_lead`，否则为`null`；`breeding_note`说明适用场景和执行条件；
- `growth_rate_mm_day`、`growth_rate_lower_mm_day`、`growth_rate_upper_mm_day`：中心、慢速和快速生长速度，用于页面趋势图；
- `maturity_reference_mm`、`preovulatory_reference_mm`：当前品种采用的成熟与排卵前参考直径；
- `cl_confirmation_median`：预计首次观察到CL的历史中位时间，不是真实排卵时间；
- `support_level`：当前直径在本场历史数据中的支持程度；
- `warnings`：异常增长、促排窗口冲突等提醒。

成熟、排卵单点字段自服务1.1.0引入，本版服务版本为1.4.0，模型参数版本仍为1.0.0。配种方式默认鲜精，无需额外选择。页面主要结果改为完整的北京时间日期时刻，不再显示相对倒计时或预测范围卡片。接口保留计算用小时数和参考窗口，但已移除`next_ultrasound_window`超声建议字段。

`breeding_recommendation=null`时不得将其解释为“立即配种”，应读取`breeding_status`和`breeding_note`。`hours=0`仅表示现在起安排现场确认，不代表已经排卵或可以省略检查。24小时属于参考安排，没有验证为本场最优受胎时机。数据分析见 [鲜精配种数据分析.md](鲜精配种数据分析.md)。

达到参考直径时，单点预测可为当前摸排时刻（`hours=0`）；排卵预测为当前时刻只表示从现在起需要重点关注，不等于已确认排卵。`post_ovulation`时两个单点预测均为`null`。

当前窗口是公式估计的风险范围，不是经过覆盖率校准的置信区间，取下界仍可能晚于真实时间。本场已有MAE针对首次确认CL，不能当作真实成熟或排卵的晚报率。示例与验证口径见 [偏早输出说明.md](偏早输出说明.md)。

## 上线前必须确定

1. 对方公司的固定出口IP或API网关地址；
2. HTTPS域名和证书由谁维护；
3. API Key如何分配、轮换和吊销；
4. 是否允许保存`animal_id`，保存多长时间；
5. 请求频率、超时、服务可用性和模型升级通知方式；
6. 对方是否接受“排卵为风险窗口、CL误差不等于真实排卵误差”的模型边界。
