# 股票关键价位雷达

人工通过 GitHub Actions 启动一次临时监控 Session。GitHub-hosted runner 在 A 股交易时段按设定间隔采集行情，记录价格、区间变化和关键价位机械状态；网页只读取仓库生成的 JSON，不直接访问股票行情网站。

## 启动一次 Session

1. 在仓库 **Actions → 启动股票监控 Session → Run workflow**。
2. `stocks` 填写本次监控股票，多个代码用英文逗号分隔，例如 `300398.SZ,300502.SZ`。代码必须已存在于 `config.yaml`。
3. `until` 填写北京时间的当日结束时刻，例如 `11:30` 或 `15:00`。
4. `interval_minutes` 默认 `5`，不能小于 5。
5. 点击 **Run workflow**。关闭 GitHub 和雷达网页不会停止 Session；需要提前结束时，在 Actions 运行详情中选择 **Cancel workflow**。

新的 Session 会取消仍在运行的旧 Session。Session 在交易时段立即采集，午休期间等待至 13:00，到结束时刻自动写入 `FINISHED`。第一版按周一至周五判断交易日，法定休市日会由过期行情保护阻止产生新事件。

## 配置股票

`config.yaml` 只保存长期研究配置，包括股票代码、名称、市场、回踩区、突破位、失效位和预警距离。增加股票或修改关键价位时才需要提交该文件；启动、停止或切换本次监控股票不需要修改配置。

股票代码、市场和后缀必须一致，例如 `600000.SH`。Python 不写死股票和价位。

## GitHub 设置

在仓库 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**，并在 **Settings → Actions → General → Workflow permissions** 允许 **Read and write permissions**。

`.github/workflows/deploy-pages.yml` 在页面文件变化时发布网页；`.github/workflows/monitor.yml` 只由 Run workflow 启动。监控过程每轮都会提交四个 JSON，因此中途取消或失败不会丢失此前已经完成的采样。数据提交不会递归启动新的 Session。

网页地址为 `https://<用户名>.github.io/<仓库名>/`。页面每 5 分钟从 GitHub 仓库读取最新的 `latest.json`、`history.json`、`events.json` 和 `status.json`；手动刷新也会立即重新读取。每次 Session 另存于 `data/sessions/<session_id>/`。

## 数据口径

- 主源为腾讯行情，备用源为东方财富。所有行情请求只允许从 `runs-on: ubuntu-latest` 的 GitHub-hosted runner 发出。成交量统一为“股”，成交额统一为“元”，换手率与涨跌幅为百分数。
- `quote_timestamp` 是行情源时间，`sample_timestamp` 是本轮实际采样时间，`sample_interval_seconds` 保存实际间隔。区间价格、成交量和成交额均以前后两次有效采样计算。
- `fresh` 为不超过 300 秒；301–600 秒为 `acceptable`；超过 600 秒为 `STALE`。过期行情允许保存，但不会产生新的关键价位事件。
- 状态只表示价格与人工价位的机械关系，不输出买入、卖出或自动交易指令。
- 公共行情接口和 GitHub Actions 都没有交易级可靠性承诺，本工具不适合准点交易或自动下单。

## 本地查看与测试

本地只允许查看已有 JSON 和运行无网络单元测试，不运行实时行情 Session：

```sh
python -m http.server 8766 --directory docs
python -m unittest discover -s tests -v
```

实时 Session 的入口会校验 `GITHUB_ACTIONS=true` 与 `RUNNER_ENVIRONMENT=github-hosted`，防止从本地电脑直接访问行情源。
