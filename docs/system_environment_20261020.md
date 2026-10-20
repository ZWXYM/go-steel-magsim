# 运行环境与资源目录

这是2026-10-20已运行本机系统的配置说明。原环境保留，不需要为阅读结果、校准或下载而重新安装。系统首页显示实际源码、资源目录、任务存储和当前版本；启动默认不恢复旧队列。

## 本机重开

双击统一工作区的start_system.cmd。需要手动指定目录时，在magsim目录运行：

```powershell
..\.runtime\magsim\Scripts\python.exe -X utf8 tools/start_workbench.py --root "D:\Project\go-steel-thesis" --storage "D:\Project\go-steel-thesis\work\system_entry_20261008_005642\storage" --page system --open-browser
```

主工作区根目录与magsim子目录的start_system.cmd已统一到根入口，并保留显式参数覆盖。可先运行`.\start_system.cmd --check-config`查看实际路径、请求端口、模式和默认不恢复队列的设置；该选项只返回配置，不启动应用或验证原生许可。

资源目录和任务存储是两个独立选项。更换存储会显示另一组记录；这不代表原记录丢失。若需要禁止本次实例的原生/训练提交，可追加--cpu-only；正常重开不附加此参数，GPU授权不因CPU对照工作而撤销。旧端口被其他版本占用时启动器顺延，使用其打印的地址。

## 两套Python环境

| 用途 | 相对资源根目录的解释器 | 本机查询到的版本 |
| --- | --- | --- |
| Web、校准、数据整理、MuMax任务控制 | .runtime/magsim/Scripts/python.exe | Python3.10.11，NumPy1.26.4 |
| 独立Maxwell导入、求解worker | .runtime/aedt/Scripts/python.exe | Python3.12.14，PyAEDT1.0.0，NumPy2.4.6 |

PyAEDT的Python导入路径是ansys.aedt.core，已安装分发包名称是pyaedt。直接查询ansys-aedt-core分发包名称会报告PackageNotFoundError，不能据此判断本机PyAEDT不存在。电机worker明确调用第二个解释器；网页进程没有安装PyAEDT也不等于电机环境失效。以上是本机已核验版本，不表示要求升级到其他版本；不要混合两套环境或把其NumPy约束互相覆盖。

本机虚拟环境仍依赖创建时的基础Python安装，其中AEDT环境启用system-site-packages。把.runtime文件夹直接复制到另一台电脑，不能保证独立运行。没有在本次交付中安装或升级任何依赖，也没有创建AEDT会话。

## 公开程序与本机完整资源

公开仓库的requirements.txt用于Web/CPU分析环境。按README创建.venv和安装这些依赖后，可以复现已公开校准证据、运行软件测试和使用页面；它不同时安装AEDT、驱动、MuMax3或本机私有电机工程。

完整本机电机工作台另外读取资源根目录下的motor/v8_1/manifest.json，以及motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt；原生worker还需要.runtime/aedt/Scripts/python.exe、已有AEDT安装及可用许可。网页显示许可TCP可达只表示连接，不等于已成功checkout或设计可访问；实际任务仍保存原生错误。

MuMax任务使用现有.runtime/mumax3/mumax3.exe、MUMAX3_EXE或PATH配置，并需要兼容GPU。只查看旧结果不会运行该程序。完整原生工程、缓存和论文归档不随公开副本发布；公开副本没有本机历史任务，不应为了填满页面而重新提交旧任务。

当前主工作区保存21份材料和原作者扫描/优化结果，保持原资源根目录和存储即可重开。缺少本机原生工程时，校准和只读结果入口仍可分别使用；电机目录错误应先检查资源路径，而非重新计算材料或重装Web依赖。
