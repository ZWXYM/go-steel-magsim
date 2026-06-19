# 各向异性插值工作流

## 为什么 XGBoost 默认只训练 RD/TD

取向电工钢的主要各向异性由轧向 RD 和横向 TD 控制。当前工作流中，XGBoost 新训练默认只使用 0 度和 90 度目标，避免把 45 度等中间角度强行作为独立训练目标，从而减少无效仿真和重复样本。

`DatasetBuilder.build_dataset()` 仍可在分析验证场景下读取已有的 30、45、60、120、135、150、180 度结果，但 pipeline 和新模型训练默认使用 RD/TD。

## 椭圆磁导模型

全向性能图由 RD/TD 曲线插值得到。对每个标准 H 点，先计算：

```text
mu_RD(H) = B_RD(H) / (mu0 * H)
mu_TD(H) = B_TD(H) / (mu0 * H)
```

然后使用椭圆极坐标磁导插值：

```text
mu_theta(H) =
    mu_RD(H) * mu_TD(H)
    / sqrt((mu_TD(H) * cos(theta))^2 + (mu_RD(H) * sin(theta))^2)
```

最后得到 `B_theta(H) = mu0 * mu_theta(H) * H`。边界满足 0 度为 RD、90 度为 TD、180 度回到 RD。插值前后都会执行物理约束校准。

## 页面中的两类用途

结果分析页的全向图来自已仿真的 RD/TD 材料级代表曲线。如果配置目录中已经存在中间角度仿真结果，页面会把这些结果作为对照点叠加。

快速预测页的全向图来自 XGBoost 预测的 RD/TD 曲线。该图用于展示材料角度响应和论文图，不作为训练输入。

## Maxwell 导出

Maxwell `.amat` 当前仍只写入 RD/TD 两个方向的 B-H 数据，对应 X/Y 方向。插值得到的中间角度不会写入 `.amat`，除非后续明确采用支持全向曲线的 Maxwell 材料格式。

参考资料路径：

`C:\Users\10760\Desktop\汇报\1\取向电工钢.pdf`
