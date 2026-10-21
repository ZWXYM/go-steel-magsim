# 运行环境与资源目录

这是2026-10-20已运行本机系统的配置说明。原环境保留，不需要为阅读结果、校准或下载而重新安装。系统首页显示实际源码、资源目录、任务存储和当前版本；启动默认不恢复旧队列。

## 本机重开

双击统一工作区的start_system.cmd。需要手动指定目录时，在magsim目录运行：

```powershell
..\.runtime\magsim\Scripts\python.exe -X utf8 tools/start_workbench.py --root "D:\Project\go-steel-thesis" --storage "D:\Project\go-steel-thesis\work\system_entry_20261008_005642\storage" --page system --open-browser
```

主工作区根目录与magsim子目录的start_system.cmd已统一到根入口，并保留显式参数覆盖。可先运行`.\start_system.cmd --check-config`查看实际路径、请求端口、模式和默认不恢复队列的设置；该选项只返回配置，不启动应用或验证原生许可。

资源目录和任务存储是两个独立选项。更换存储会显示另一组记录；这不代表原记录丢失。若需要禁止本次实例的原生/训练提交，可追加--cpu-only；正常重开不附加此参数，GPU授权不因CPU对照工作而撤销。旧端口被其他版本占用时启动器顺延，使用其打印的地址。

启动器在程序目录的data/system_workbench/.launcher_endpoint_hints_v1.json保存最多32份地址提示。提示按源码、资源、任务存储及运行模式区分，重开前仍读取实际服务身份，不以文件中的端口号直接认定可复用。失效、损坏或无法写入的提示不阻断原有启动流程；--no-fallback仍只允许指定端口。--check-config不会读取或写入此提示，也不会创建任务记录。提示是本地发现信息，不随公开程序发布。

## 两套Python环境

| 用途 | 相对资源根目录的解释器 | 本机查询到的版本 |
| --- | --- | --- |
| Web、校准、数据整理、MuMax任务控制 | .runtime/magsim/Scripts/python.exe | Python3.10.11，NumPy1.26.4 |
| 独立Maxwell导入、求解worker | .runtime/aedt/Scripts/python.exe | Python3.12.14，PyAEDT1.0.0，NumPy2.4.6 |

PyAEDT的Python导入路径是ansys.aedt.core，已安装分发包名称是pyaedt。直接查询ansys-aedt-core分发包名称会报告PackageNotFoundError，不能据此判断本机PyAEDT不存在。电机worker明确调用第二个解释器；网页进程没有安装PyAEDT也不等于电机环境失效。以上是本机已核验版本，不表示要求升级到其他版本；不要混合两套环境或把其NumPy约束互相覆盖。

本机虚拟环境仍依赖创建时的基础Python安装，其中AEDT环境启用system-site-packages。把.runtime文件夹直接复制到另一台电脑，不能保证独立运行。没有在本次交付中安装或升级任何依赖，也没有创建AEDT会话。

2026-10-21补充核对：Web环境同样启用system-site-packages，NumPy1.26.4和Flask3.1.3来自基础Python目录。`pip check`报告两组基础环境冲突：TensorFlow-intel2.15.0要求TensorBoard<2.16，当前为2.20.0；OpenCV-headless4.13.0.90要求NumPy>=2，当前为1.26.4。Web依赖清单及app/modules/tools/scripts中未发现这两组包的引用，实际CPU校准、预测与相关测试记录仍保留；本机整体依赖未达到pip check无冲突的状态。

本次保留所有已安装包。新机器按公开README创建独立.venv并安装requirements.txt，避免继承其他任务的依赖；公开CPU首次使用验证复用了本机解释器，未执行全新环境安装。不要仅为消除上述两行提示而升级本项目NumPy或卸载基础Python包。

## 公开程序与本机完整资源

公开仓库的requirements.txt用于Web/CPU分析环境。按README创建.venv和安装这些依赖后，可以复现已公开校准证据、运行软件测试和使用页面；它不同时安装AEDT、驱动、MuMax3或本机私有电机工程。

### 从公开副本第一次运行

在公开仓库根目录、已按README安装Web依赖后运行：

```powershell
.venv\Scripts\python.exe tools/start_workbench.py --root . --storage data/first_cpu_workbench --cpu-only --page system --open-browser
```

新任务存储最初没有校准、预测和电机结果。进入“材料校准”，保留四个样品和“跨材料泛化，内层选参（四样品）”，点击“建立校准并检验”；这一步只读取公开的既有RD/TD table，不启动MuMax。随后可“载入B30P105原生对示例”，选择“完整排除牌号后预测”和B30P105，再点击“应用所选范围”。下载曲线、结果JSON、实际有效库、AMAT及metadata；材料库可重开本次生成的记录。若只需已知样品标定，直接下载四材料校准包。

2026-10-21实际用公开副本自身资源根和全新隔离存储完成了上述CPU流程：四样品8组曲线、每组26个H节点，一份校准加一份排除预测，材料库9条目，5个预测下载和26成员校准ZIP逐字节一致，数值与完整工作区保存结果一致。验证复用了本机已安装的Web解释器，没有重新安装依赖；不据此声明已验证其他机器或原生电机环境。

使用说明中`cal_...`、`pred_...`等固定编号是完整开发工作区的历史例子，公开副本不会自动附带这些私有任务。使用当前页面实际生成的编号和原任务存储，避免把空历史列表误认为程序损坏。未附电机工程时，电机目录会说明缺少本机资源，CPU校准与下载仍可使用。

完整本机电机工作台另外读取资源根目录下的motor/v8_1/manifest.json，以及motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt；原生worker还需要.runtime/aedt/Scripts/python.exe、已有AEDT安装及可用许可。网页显示许可TCP可达只表示连接，不等于已成功checkout或设计可访问；实际任务仍保存原生错误。

MuMax任务使用现有.runtime/mumax3/mumax3.exe、MUMAX3_EXE或PATH配置，并需要兼容GPU。只查看旧结果不会运行该程序。完整原生工程、缓存和论文归档不随公开副本发布；公开副本没有本机历史任务，不应为了填满页面而重新提交旧任务。

当前主工作区保存21份材料和原作者扫描/优化结果，保持原资源根目录和存储即可重开。缺少本机原生工程时，校准和只读结果入口仍可分别使用；电机目录错误应先检查资源路径，而非重新计算材料或重装Web依赖。
