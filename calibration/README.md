# 后处理校准工作入口

目前存在两条明确区分的路径：旧平台/保存模型仍使用历史 scaled-mix 修正；新研究路径为 `fixed_h_delta_v1`，输入必须来自 `cubic_sample_frame_v2`，在同一个物理 H（A/m）网格计算 δ=B_reference−B_simulation。旧缓存和 24 行旧训练集保留，不能直接改名为新版数据。

## 四材料试验

目录 `pilot_20261003_n8/`；四个牌号 B23R075、B27R090、B27R095、B30P105，每个 RD/TD 各 8 晶粒，共 64 个 GPU 仿真。使用登记表中报告列的 ODF 与标称 Si（3.1/3.0 wt%），避免混用另一套代码锚点 ODF。ODF 是估计；Si 不是在本工作区确认的同批次完整化学分析。

主要文件：

- `manifest.json`：输入、脚本、冻结参考、参数和哈希。
- `<牌号>/<RD或TD>/raw/`：MuMax3 原始 table、日志和逐晶粒提取。
- `<牌号>/aggregate_RD.csv` / TD：等体积晶粒平均与晶粒标准差。
- `calibration_bank.json`：全材料拟合 bank；`bank_holdout_<牌号>.json` 物理排除该牌号。
- `validation_metrics.csv`、`curve_comparison.csv`：raw、同牌号拟合、完整牌号留出分别列出。
- `calibration_fit_diagnostics.csv`：训练列格式的四行拟合诊断，两个训练器均拒绝将其作为正式训练集。
- `exports/`：8 份拟合/留出 `.amat` 与 metadata，H/B 数值已经过导出前回读验证。
- `summary.json`、校准图及 `figure_QA.md`：结果和边界。

从公开仓库根目录，或本地论文工作区的 `magsim/` 执行；后者可用 `..\.runtime\magsim\Scripts\python.exe` 替代下面的 `python`。默认试验目录自动识别两种布局。

```powershell
python tools/run_calibration_pilot.py --analyze
# 原生执行另加 --run，可用 --mumax <可执行文件> / MUMAX3_EXE / PATH。
python tools/plot_calibration_pilot.py '<试验目录>'
```

`--run` 会按完成记录及 table/script 哈希跳过已完成任务；内容变化会停止，不覆盖旧原生输出。分析步骤可重新执行，原始 table 不改动。扩大 N 时必须用新的目录，例如公开仓库用 `--run-dir calibration/runs/pilot_n32 --n-grains 32 --seed 20261003`，论文工作区从 magsim/ 用 `../calibration/runs/pilot_n32`；不能覆盖 n8 试验。

对新的同版本原始曲线应用 bank：

```powershell
python tools/predict_calibrated_material.py --bank '<试验目录>/bank_holdout_B23R075.json' --raw-pair '<试验目录>/B23R075/raw_pair.json' --exclude-grade B23R075 --output-dir tmp/calibration_check --name B23R075_independent
```

该 CLI 只读取声明匹配物理版本的 raw_pair；它不是四行数据训练出的机器学习代理。

## 协议与使用边界

仿真设置：16×16×1 nm 小晶体、4×4×1 网格；在样品坐标系旋转 cubic axes，Kc1 沿用原工程 Si 经验先验、Ku1=0。Msat=1.56e6 A/m、Aex=2.1e-11 J/m 保持为先验。准静态完整大回线的两支中点作为工程代理，既不是去磁态初始曲线，也不是已经验证的正常磁化曲线。

聚合采用等体积均值；晶粒间标准差不是材料实验置信区间。固定 H 下 IDW 插值差分，距离尺度 f=0.5、theta=10°、halfwidth=10°、Si=0.2 wt% 是预先设定的协议先验，并未用留出曲线调参。成分和 ODF 的因果响应尚不能由四牌号辨识。

守恒约束作用于 J=B−μ0H：非负、单调、≤μ0Msat，每次修复均登记。冻结参考本身可能与该 Msat 先验不一致；因此同牌号拟合也不保证零误差，不能通过放松上界来掩盖来源/模型冲突。

所有 `.amat` 标记 `experimental_BH_only`，Kh/Kc/Ke 为零占位，ND=1000 为先验；没有完成铁损或电机效率验证。不得把它们当作已校准损耗材料使用。本轮没有改写/重新求解已有电机算例。

升级条件：来源/成分/曲线测量定义可追溯，跨 seed 与晶粒数收敛，独立材料误差达标（包括低场/高场，而不仅 B800），再冻结 bank 并产生新版独立模拟训练集。旧平台默认链仍有历史 H 缩放语义，不作为新版校准验证入口。

科学依据：MuMax3 3.11.1 官方 API 明确区分 Kc1（立方）、Ku1（单轴）、B_ext（T）及 anisC1/anisC2。https://mumax.github.io/api3111.html 。原生解析能量/多角度验证在 `diagnostics/generator_checks/native_validation.json`。
