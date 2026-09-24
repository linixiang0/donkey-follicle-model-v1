# 排卵模型第一版

这是一个可直接本地运行或部署的 HTML + FastAPI 决策支持工具。V1 使用两类证据：

1. 东阿现有摸排数据中“当前卵泡直径 → 首次确认 CL”的统计规律；
2. 已发表驴繁殖研究中的成熟阈值、德州驴卵泡生长速度和排卵前参考直径。

系统将“预计排卵窗口”和“预计确认 CL 时间”分开显示。后者受摸排频率影响，不等于真实排卵时刻。

## V1 输入

- 当前摸排时间；
- 左、右卵泡状态：毫米数值、SF 或 CL；
- 可选：上一次最大卵泡及时间，用于修正个体生长速度；

网页固定采用东阿/德州驴参数，不要求录入编号、API密钥、品种或促排信息。

天气、温度和湿度在 V1 不进入公式。当前数据没有逐次摸排对应的气象记录，现有文献也不能提供可直接迁移到本场的小时级作用系数。

## 推荐：启动 HTML 预测页面和接口

页面和接口使用同一个端口。在项目目录执行：

```bash
python -m pip install -r requirements-api.txt && python -m uvicorn api:app --host 127.0.0.1 --port 8001
```

浏览器打开 `http://127.0.0.1:8001/` 使用预测页面，打开 `http://127.0.0.1:8001/docs` 查看接口文档。预测页面包括：

- 左右卵泡和可选历史摸排录入；
- 成熟时间、排卵窗口和下次复查建议；
- 实测点、中心预测线、快慢生长范围与排卵窗口图；
- 可拖动或自动播放的时间推演。

8001 端口被占用时可换为 8002：

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8002
```

训练参数已保存在 `models/v1_model.json`，所以日常使用不需要再次训练。

原有 Streamlit 页面仍保留在 `app.py`，如有需要可单独启动，但推荐使用新的 HTML 页面。

## 提供给外部公司的HTTP接口

项目同时包含FastAPI接口。接口使用说明、请求字段、鉴权和Docker部署方法见 `API_README.md`。

```bash
python -m pip install -r requirements-api.txt && python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://127.0.0.1:8000/` 使用网页，访问 `http://127.0.0.1:8000/docs` 查看自动接口文档。对外部署时必须设置 `DONKEY_API_KEY` 并通过HTTPS或公司API网关开放。

## 使用新数据重新校准

将新 Excel 放在项目目录，然后执行：

```bash
python train_model.py --input "b_estrus_breeding_mapping_processed(6).xlsx" --output models/v1_model.json
```

重新启动服务后即使用新参数。

## Docker 部署

```bash
docker build -f Dockerfile.api -t donkey-follicle-v1 . && docker run --rm -p 8001:8000 donkey-follicle-v1
```

## 测试

```bash
python -m pip install -r requirements-test.txt && python -m unittest discover -s tests -v
```

## V1 边界

- V1 是生产摸排与配种安排的决策支持工具，不代替兽医超声判断。
- 当前排卵标签是区间标签：排卵发生在最后一次见到优势卵泡与首次确认 CL 之间。
- 对 30、35、40 mm 附近的输入，本场数据支持较强；远离这些值时属于外推。
- 当出现 CL 时，系统只报告“已经进入排卵后状态”，不会继续预测未来排卵。
