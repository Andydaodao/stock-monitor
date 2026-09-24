# 股票关键价位雷达

根据 `config.yaml` 中人工设定的股票和关键价位，在 A 股交易时段约每 5 分钟采样一次。网页与 JSON 只报告行情和机械状态，不给买卖建议，也不自动下单。

## 启用

1. 在 `config.yaml` 中把需要监控的股票设为 `enabled: true`。总开关 `monitor.enabled` 默认开启，但样例股票全部关闭，不会抓取。
2. 按自己的研究修改回踩区、突破价和失效价。增减股票只改 `config.yaml`，代码、市场与代码后缀必须一致，例如 `600000.SH`。
3. `monitor_until` 可留 `null`，写 `"11:30"` 表示每天到该时刻停止，或写 `"2026-09-28T11:30:00+08:00"` 表示到指定日期时间后停止。

请把整个项目目录上传到自己新建的 GitHub 仓库根目录，保留 `.github/workflows/monitor.yml`。在仓库 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**；在 **Settings → Actions → General → Workflow permissions** 允许 **Read and write permissions**。首次上传会触发 workflow；之后可在 **Actions → 采集并发布股票雷达 → Run workflow** 手动执行，交易时段之外会正常退出。`config.yaml` 的网页提交也会触发一次。

公开网址为 `https://<用户名>.github.io/<仓库名>/`，同目录下有 `latest.json`、`history.json`、`events.json`、`status.json`。`data/` 保存跨次运行状态，`docs/` 是 Pages 发布内容；自动提交仅更新这些 JSON，不会改人工配置。

## 本地运行

使用 Python 3.11 或更新版本：

```sh
python -m pip install -r requirements.txt
python src/main.py
python -m http.server 8766 --directory docs
```

然后打开 `http://127.0.0.1:8766/`。本地若无法连接公开行情源，程序会在 `status.json` 记录单股错误，其他股票仍可继续。

## 数据口径

- 主源为腾讯行情，备用源为东方财富。成交量统一为“股”，成交额统一为“元”，换手率与涨跌幅为百分数；备用源的成交量单位用成交额和当日价格区间核验，核验失败会报错。行情源返回的时间是 `quote_timestamp`；`generated_at` 是脚本生成文件的时间，两者不能混同。
- `fresh` 为不超过 300 秒；301–600 秒为 `acceptable`；超过 600 秒为 `STALE`，不产生新事件。来源无有效时间戳也不能触发事件。一次实际采样可能延迟或漏跑，`interval_volume` 是与上一个有效报价的差额，`interval_seconds` 记录真实间隔。
- 新交易日会重置临时状态与成交量基线，但保留历史；节假日首版只靠周末判断，工作日休市会依赖行情时间戳进入过期状态。`BREAKOUT_HOLD` 表示连续两次样本位于突破价上方，并不保证两次采样之间一直站稳。
- 数据源是公开网页接口，没有稳定性承诺。若要把实时行情重新发布到公开 Pages，应先确认所用行情源的使用和再发布条款。GitHub 定时任务也可能延迟或跳过，不能用于需要准点或交易级可靠性的场景。

运行单元测试：`python -m unittest discover -s tests -v`。
