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

- `mm`：有毫米数值，此时必须提供`value_mm`；
- `SF`：小卵泡，此时`value_mm`必须为`null`；
- `CL`：已观察到黄体，此时接口返回`post_ovulation`，不继续预测未来排卵。

所有时间必须使用ISO 8601格式并带时区，例如`2026-09-23T10:00:00+08:00`。

## PowerShell调用示例

```powershell
$headers=@{"X-API-Key"="请替换成实际API Key"}; $body=@{client_request_id="EO-20260923-0001";animal_id="370001";current_time="2026-09-23T10:00:00+08:00";breed_profile="dezhou";left=@{status="mm";value_mm=35.0};right=@{status="SF";value_mm=$null};induction=@{used=$false;injection_time=$null}} | ConvertTo-Json -Depth 6; Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/predict" -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

也可以设置`DONKEY_API_BASE_URL`和`DONKEY_API_KEY`后运行：

```powershell
python .\examples\client.py
```

## 返回结果重点字段

- `prediction_status`：`predicted`或`post_ovulation`；
- `maturity_center`、`maturity_window`：达到成熟参考直径的中心时间和范围；
- `ovulation_center`、`ovulation_window`：预计排卵中心和风险窗口；
- `next_ultrasound_window`：建议复查时间；
- `growth_rate_mm_day`、`growth_rate_lower_mm_day`、`growth_rate_upper_mm_day`：中心、慢速和快速生长速度，用于页面趋势图；
- `maturity_reference_mm`、`preovulatory_reference_mm`：当前品种采用的成熟与排卵前参考直径；
- `cl_confirmation_median`：预计首次观察到CL的历史中位时间，不是真实排卵时间；
- `support_level`：当前直径在本场历史数据中的支持程度；
- `warnings`：异常增长、促排窗口冲突等提醒。

## 上线前必须确定

1. 对方公司的固定出口IP或API网关地址；
2. HTTPS域名和证书由谁维护；
3. API Key如何分配、轮换和吊销；
4. 是否允许保存`animal_id`，保存多长时间；
5. 请求频率、超时、服务可用性和模型升级通知方式；
6. 对方是否接受“排卵为风险窗口、CL误差不等于真实排卵误差”的模型边界。
