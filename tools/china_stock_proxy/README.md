# China Stock Data Proxy

这个小服务用于解决国外后端无法稳定访问国内 A 股行情接口的问题。

推荐部署位置：阿里云、腾讯云、华为云、百度智能云等中国大陆或香港节点。GitHub 只负责托管代码，不能提供实时国内网络出口。

## 运行

```bash
cd tools/china_stock_proxy
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
DATA_PROXY_TOKEN=change-me uvicorn main:app --host 0.0.0.0 --port 8787
```

也可以用 Docker：

```bash
cd tools/china_stock_proxy
docker build -t china-stock-proxy .
docker run -p 8787:8787 -e DATA_PROXY_TOKEN=change-me china-stock-proxy
```

Render 部署时建议选择 `Singapore` 区域，并把服务根目录设为
`tools/china_stock_proxy`。服务会自动读取 Render 提供的 `PORT`。

上线后先检查两个地址：

```text
GET /health           # 进程存活
GET /health/upstream  # 东方财富上游确实可访问
```

## 后端配置

在 Render 后端环境变量中增加：

```bash
DATA_PROXY_BASE_URL=https://your-proxy-domain.example.com
DATA_PROXY_TOKEN=change-me
DATA_PROXY_TIMEOUT=20
```

配置后，后端所有东方财富实时数据请求会先走该代理；代理不可用时会回退直连。
主后端的 `GET /health/data-source` 会进一步确认完整代理链路可用。

## 安全

代理只允许访问：

- `push2.eastmoney.com`
- `datacenter.eastmoney.com`

如果后续新增数据源，需要先在 `main.py` 的 `ALLOWED_HOSTS` 中显式加入域名。
