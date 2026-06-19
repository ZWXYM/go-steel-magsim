# 物理约束校准说明

## 为什么需要校准

当前 MuMax3 晶粒级输出会受到网格、单晶粒代表性和设备算力限制影响。机器学习模型在小样本训练后也可能给出局部非单调、负值、无限值或异常尖峰。物理约束校准用于消除这类数值伪差，让材料库、预测曲线和 Maxwell 导出更稳定。

校准并不把曲线强行贴合某个商业牌号，也不人为挑选“好看”的结果。它只处理明显非物理的数据问题，例如 H 非正、B 为负、B-H 曲线下降、斜率低于保守真空磁导下限、B 值爆点等。

## 发生位置

校准是内部隐式流程，没有用户可关闭的 UI 开关。当前发生在三个主要环节：

1. 数据集构建：`modules/dataset_builder.py` 聚合 `angle_XXX/grain_*.txt` 时，先校验单晶粒曲线，再生成材料级代表曲线。
2. 预测结果：`modules/ml_trainer.py` 的 `predict_bh()` 返回前，校准 RD/TD 曲线和标量目标，并生成全向响应。
3. Maxwell 导出：`modules/maxwell_exporter.py` 写 `.amat` 前，保证 RD/TD 曲线 H 升序、B 非负且单调。

结果分析页生成全向性能图前，也会通过同一套材料级代表曲线和校准逻辑。

## sidecar 报告

校准和材料聚合会写入可追溯报告：

- 每个配置目录：`material_representative_summary.json`
- 每个数据集旁边：`dataset_*.metadata.json`
- 每个 Maxwell 材料文件旁边：`*.metadata.json`

报告中包含有效晶粒数、剔除原因统计、代表晶粒文件、聚合方法、曲线校准摘要，以及 Hc、Mr、mu_max 的置信等级说明。

## 标量置信等级

Hc、Mr、mu_max 保留为分析和趋势观察字段。由于当前微磁学输出受网格和设备限制，这些标量在 metadata 中标记为低置信；正式 Maxwell 导出以校准后的 RD/TD B-H 曲线为主。
