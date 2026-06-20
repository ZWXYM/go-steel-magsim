# generate_parallel_batch.py (跨平台版 - 支持Windows/Linux)
import os
import re
from pathlib import Path
from datetime import datetime


# ========================================
# 工具函数(保持不变)
# ========================================
def scan_grain_scripts_dir(base_dir='grain_scripts'):
    """扫描grain_scripts目录,识别已生成的配置(只扫描single模式)"""
    base_path = Path(base_dir)

    if not base_path.exists():
        raise FileNotFoundError(f"目录不存在: {base_dir}")

    configs = {}

    for config_dir in base_path.iterdir():
        if not config_dir.is_dir() or config_dir.name == 'all_angles_combined':
            continue

        config_name = config_dir.name
        angle_data = {}

        for angle_dir in config_dir.iterdir():
            if not angle_dir.is_dir() or not angle_dir.name.startswith('angle_'):
                continue

            match = re.match(r'angle_(\d{3})', angle_dir.name)
            if not match:
                continue

            angle_code = match.group(1)
            mx3_files = list(angle_dir.glob('grain_*.mx3'))
            count = len(mx3_files)

            if count > 0:
                angle_data[angle_code] = {
                    'count': count,
                    'path': str(angle_dir),
                    'angle_value': int(angle_code)
                }

        if angle_data:
            n_grains = next(iter(angle_data.values()))['count']
            configs[config_name] = {
                'n_grains': n_grains,
                'angles': angle_data
            }

    return configs


def format_angle_list(angle_codes):
    """将角度编码列表格式化为显示字符串"""
    angles = [int(code) for code in angle_codes]
    return '_'.join([str(a) for a in sorted(angles)])


def get_timestamp():
    """获取时间戳字符串"""
    return datetime.now().strftime('%Y%m%d_%H%M%S')


# ========================================
# 批处理脚本生成(Windows)
# ========================================
def generate_multi_config_batch_script(selected_configs, output_filename):
    """生成支持多配置的Windows批处理脚本"""
    timestamp = get_timestamp()

    # 构建运行目录名称
    if len(selected_configs) == 1:
        config_name = selected_configs[0][0]
        angle_str = format_angle_list(selected_configs[0][2])
        run_name = f"run_{config_name}_angles_{angle_str}_{timestamp}"
    else:
        run_name = f"run_multi_configs_{timestamp}"

    # 计算总任务数
    total_tasks = sum(
        config_data['n_grains'] * len(angle_codes)
        for _, config_data, angle_codes in selected_configs
    )

    is_single_config = len(selected_configs) == 1

    script = f"""@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Fe-Si Polycrystalline {"Single" if is_single_config else "Multi"}-Config Simulation
echo ========================================
echo Total configurations: {len(selected_configs)}
echo Total tasks: {total_tasks}
echo Output: output\\{run_name}
echo ========================================
echo.

:: Create output base directory
set OUTPUT_BASE=output\\{run_name}
if not exist %OUTPUT_BASE% mkdir %OUTPUT_BASE%

:: Global statistics
set TOTAL_TASKS={total_tasks}
set COMPLETED_TASKS=0
set FAILED_TASKS=0
set START_TIME=%time%

"""

    # 为每个配置生成处理代码
    for config_idx, (config_name, config_data, selected_angle_codes) in enumerate(selected_configs, 1):
        n_grains = config_data['n_grains']
        angles_display = ' '.join([str(int(code)) for code in selected_angle_codes])

        script += f"""
echo.
echo ========================================
echo Configuration {config_idx}/{len(selected_configs)}: {config_name}
echo Grains: {n_grains}, Angles: {angles_display} degrees
echo ========================================

"""

        if is_single_config:
            script += f"""
:: Single config mode - no subdirectory
set CONFIG_DIR=%OUTPUT_BASE%

:: Copy parameter template to base directory
if exist grain_scripts\\{config_name}\\simulation_parameters_template.txt (
    copy grain_scripts\\{config_name}\\simulation_parameters_template.txt !CONFIG_DIR!\\
)

"""
        else:
            script += f"""
:: Multi-config mode - create config subdirectory
set CONFIG_DIR=%OUTPUT_BASE%\\{config_name}
if not exist !CONFIG_DIR! mkdir !CONFIG_DIR!

:: Copy parameter template
if exist grain_scripts\\{config_name}\\simulation_parameters_template.txt (
    copy grain_scripts\\{config_name}\\simulation_parameters_template.txt !CONFIG_DIR!\\
)

"""

        # 处理该配置的每个角度
        for angle_code in selected_angle_codes:
            angle_value = int(angle_code)
            angle_path = config_data['angles'][angle_code]['path']

            script += f"""
echo.
echo [%time%] Processing {config_name} - Angle {angle_value} degrees
echo ----------------------------------------

:: Create angle output directory
set ANGLE_DIR=!CONFIG_DIR!\\angle_{angle_code}
if not exist !ANGLE_DIR! mkdir !ANGLE_DIR!

:: Copy parameters file to angle directory
if exist grain_scripts\\{config_name}\\simulation_parameters_template.txt (
    copy grain_scripts\\{config_name}\\simulation_parameters_template.txt !ANGLE_DIR!\\simulation_parameters.txt
)

:: Simulate all grains for this angle
for /L %%G in (1,1,{n_grains}) do (
    set GRAIN_ID=000%%G
    set GRAIN_ID=!GRAIN_ID:~-3!

    echo [!time!] [{config_name}] Grain %%G/{n_grains} at {angle_value}°

    :: Run mumax3
    mumax3 {angle_path}\\grain_!GRAIN_ID!.mx3

    :: Check and move output
    if exist {angle_path}\\grain_!GRAIN_ID!.out\\table.txt (
        move /Y {angle_path}\\grain_!GRAIN_ID!.out\\table.txt !ANGLE_DIR!\\grain_!GRAIN_ID!.txt
        rmdir /s /q {angle_path}\\grain_!GRAIN_ID!.out
        set /a COMPLETED_TASKS+=1
    ) else (
        echo [ERROR] grain_!GRAIN_ID! at angle {angle_value}° failed!
        echo {config_name} grain_!GRAIN_ID! at angle {angle_value}° >> %OUTPUT_BASE%\\failed_list.txt
        set /a FAILED_TASKS+=1
    )

    :: Progress indicator every 10 grains
    set /a REMAINDER=%%G %% 10
    if !REMAINDER!==0 (
        set /a PROGRESS=!COMPLETED_TASKS!*100/!TOTAL_TASKS!
        echo Global Progress: !PROGRESS!%%  ^(!COMPLETED_TASKS!/{total_tasks}^)
    )
)

"""

        script += f"""
echo {config_name} completed!
echo.
"""

    script += f"""
:: Calculate total time
set END_TIME=%time%

echo.
echo ========================================
echo All configurations completed!
echo ========================================
echo Total tasks: !COMPLETED_TASKS! / {total_tasks}
echo Failed tasks: !FAILED_TASKS!
echo Start time: %START_TIME%
echo End time: %END_TIME%
echo Results saved in: %OUTPUT_BASE%
echo.

if exist %OUTPUT_BASE%\\failed_list.txt (
    echo Warning: Some simulations failed!
    type %OUTPUT_BASE%\\failed_list.txt
)

pause
"""

    with open(output_filename, 'w', encoding='gbk') as f:
        f.write(script)

    print(f"[OK] Windows批处理脚本已生成: {output_filename}")


# ========================================
# PowerShell脚本生成
# ========================================
def generate_multi_config_powershell_script(selected_configs, output_filename, run_name=None):
    """生成支持多配置的PowerShell脚本"""
    timestamp = get_timestamp()

    # 构建运行目录名称
    if not run_name:
        if len(selected_configs) == 1:
            config_name = selected_configs[0][0]
            angle_str = format_angle_list(selected_configs[0][2])
            run_name = f"run_{config_name}_angles_{angle_str}_{timestamp}"
        else:
            run_name = f"run_multi_configs_{timestamp}"

    # 计算总任务数
    total_tasks = sum(
        config_data['n_grains'] * len(angle_codes)
        for _, config_data, angle_codes in selected_configs
    )

    is_single_config = len(selected_configs) == 1

    script = f"""# PowerShell script for {"single" if is_single_config else "multi"}-config Fe-Si simulation
$ErrorActionPreference = 'Continue'

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Fe-Si {"Single" if is_single_config else "Multi"}-Config Simulation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Total configurations: {len(selected_configs)}" -ForegroundColor White
Write-Host "Total tasks: {total_tasks}" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Output directory
$outputBase = "output\\{run_name}"
if (-not (Test-Path $outputBase)) {{
    New-Item -ItemType Directory -Force -Path $outputBase | Out-Null
}}

# Global statistics
$totalTasks = {total_tasks}
$completedTasks = 0
$failedTasks = 0
$startTime = Get-Date

"""

    # 为每个配置生成处理代码
    for config_idx, (config_name, config_data, selected_angle_codes) in enumerate(selected_configs, 1):
        n_grains = config_data['n_grains']
        source_dir = config_data.get('source_dir', f'grain_scripts\\{config_name}').replace('/', '\\')
        output_name = config_data.get('output_name', config_name).replace('/', '\\')
        angles_display = ', '.join([f"{int(code)}°" for code in selected_angle_codes])

        script += f"""
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Configuration {config_idx}/{len(selected_configs)}: {config_name}" -ForegroundColor Cyan
Write-Host "Grains: {n_grains}, Angles: {angles_display}" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan

"""

        if is_single_config:
            script += f"""
# Single config mode - no subdirectory
$configDir = $outputBase

# Copy parameter template to base directory
$paramTemplate = "{source_dir}\\simulation_parameters_template.txt"
if (Test-Path $paramTemplate) {{
    Copy-Item $paramTemplate -Destination $configDir
}}

"""
        else:
            script += f"""
# Multi-config mode - create config subdirectory
$configDir = "$outputBase\\{output_name}"
if (-not (Test-Path $configDir)) {{
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}}

# Copy parameter template
$paramTemplate = "{source_dir}\\simulation_parameters_template.txt"
if (Test-Path $paramTemplate) {{
    Copy-Item $paramTemplate -Destination $configDir
}}

"""

        # 处理该配置的每个角度
        for angle_code in selected_angle_codes:
            angle_value = int(angle_code)
            angle_path = config_data['angles'][angle_code]['path']

            script += f"""
Write-Host ""
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Processing {config_name} - Angle {angle_value}°" -ForegroundColor Yellow

# Create angle directory
$angleDir = "$configDir\\angle_{angle_code}"
if (-not (Test-Path $angleDir)) {{
    New-Item -ItemType Directory -Force -Path $angleDir | Out-Null
}}

# Copy parameters file
if (Test-Path $paramTemplate) {{
    Copy-Item $paramTemplate -Destination "$angleDir\\simulation_parameters.txt"
}}

# Simulate all grains
for ($g = 1; $g -le {n_grains}; $g++) {{
    $grainId = $g.ToString("000")
    $timestamp = Get-Date -Format 'HH:mm:ss'

    Write-Host "[$timestamp] [{config_name}] Grain $g/{n_grains} at {angle_value}°" -NoNewline

    # Run mumax3
    $scriptPath = "{angle_path}\\grain_$grainId.mx3"
    $outputPath = "{angle_path}\\grain_${{grainId}}.out\\table.txt"

    mumax3 $scriptPath 2>&1 | Out-Null

    # Check result
    if (Test-Path $outputPath) {{
        Move-Item -Force $outputPath "$angleDir\\grain_$grainId.txt"
        Remove-Item -Recurse -Force "{angle_path}\\grain_${{grainId}}.out" -ErrorAction SilentlyContinue
        Write-Host " [OK]" -ForegroundColor Green
        $completedTasks++
    }} else {{
        Write-Host " [FAIL]" -ForegroundColor Red
        Add-Content -Path "$outputBase\\failed_list.txt" -Value "{config_name} grain_$grainId at {angle_value}°"
        $failedTasks++
    }}

    # Progress update every 10 grains
    if ($g % 10 -eq 0) {{
        $elapsed = (Get-Date) - $startTime
        $avgTime = $elapsed.TotalSeconds / $completedTasks
        $remaining = [TimeSpan]::FromSeconds($avgTime * ($totalTasks - $completedTasks))

        Write-Host ""
        Write-Host "  Global Progress: $completedTasks/$totalTasks ($([math]::Round($completedTasks*100/$totalTasks, 1))%)" -ForegroundColor Cyan
        Write-Host "  Estimated remaining: $($remaining.ToString('hh\\:mm\\:ss'))" -ForegroundColor Cyan
        Write-Host ""
    }}
}}

Write-Host "{config_name} - Angle {angle_value}° completed!" -ForegroundColor Green
"""

        script += f"""
Write-Host "{config_name} completed!" -ForegroundColor Green
"""

    script += f"""
# Final summary
$totalTime = (Get-Date) - $startTime

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "All configurations completed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Total tasks: $completedTasks / $totalTasks" -ForegroundColor White
Write-Host "Failed tasks: $failedTasks" -ForegroundColor $(if ($failedTasks -eq 0) {{"Green"}} else {{"Red"}})
Write-Host "Total time: $($totalTime.ToString('hh\\:mm\\:ss'))" -ForegroundColor White
Write-Host "Average per task: $([math]::Round($totalTime.TotalSeconds / $completedTasks, 1))s" -ForegroundColor White
Write-Host "Results saved in: $outputBase" -ForegroundColor White
Write-Host ""

if (Test-Path "$outputBase\\failed_list.txt") {{
    Write-Host "Warning: Some simulations failed!" -ForegroundColor Red
    Get-Content "$outputBase\\failed_list.txt"
}}

Write-Host ""
if (-not $env:YUCE_NONINTERACTIVE) {{
    Read-Host "Press Enter to exit"
}}
"""

    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(script)

    print(f"[OK] PowerShell脚本已生成: {output_filename}")


# ========================================
# Bash脚本生成(Linux) - 修改版本,支持自定义mumax3路径
# ========================================
def generate_multi_config_bash_script(selected_configs, output_filename, mumax3_path='mumax3', run_name=None):
    """生成支持多配置的Linux Bash脚本

    Args:
        selected_configs: 选中的配置列表
        output_filename: 输出文件名
        mumax3_path: mumax3可执行文件路径,默认为'mumax3'(已添加到PATH)
        run_name: 输出目录名；None 时自动生成带时间戳的名称
    """
    timestamp = get_timestamp()

    # 构建运行目录名称
    if not run_name:
        if len(selected_configs) == 1:
            config_name = selected_configs[0][0]
            angle_str = format_angle_list(selected_configs[0][2])
            run_name = f"run_{config_name}_angles_{angle_str}_{timestamp}"
        else:
            run_name = f"run_multi_configs_{timestamp}"

    # 计算总任务数
    total_tasks = sum(
        config_data['n_grains'] * len(angle_codes)
        for _, config_data, angle_codes in selected_configs
    )

    is_single_config = len(selected_configs) == 1

    # 显示使用的mumax3路径信息
    mumax3_display = mumax3_path if mumax3_path != 'mumax3' else 'mumax3 (from PATH)'

    script = f"""#!/bin/bash
# Bash script for {"single" if is_single_config else "multi"}-config Fe-Si simulation
# MuMax3 executable: {mumax3_display}

# Color codes
RED='\\033[0;31m'
GREEN='\\033[0;32m'
YELLOW='\\033[1;33m'
CYAN='\\033[0;36m'
NC='\\033[0m' # No Color

# MuMax3 command
MUMAX3_CMD="{mumax3_path}"

echo "========================================"
echo "Fe-Si {"Single" if is_single_config else "Multi"}-Config Simulation"
echo "========================================"
echo "MuMax3: $MUMAX3_CMD"
echo "Total configurations: {len(selected_configs)}"
echo "Total tasks: {total_tasks}"
echo "Output: output/{run_name}"
echo "========================================"
echo ""

# Create output base directory
OUTPUT_BASE="output/{run_name}"
mkdir -p "$OUTPUT_BASE"

# Global statistics
TOTAL_TASKS={total_tasks}
COMPLETED_TASKS=0
FAILED_TASKS=0
START_TIME=$(date +%s)

"""

    # 为每个配置生成处理代码
    for config_idx, (config_name, config_data, selected_angle_codes) in enumerate(selected_configs, 1):
        n_grains = config_data['n_grains']
        angles_display = ' '.join([str(int(code)) for code in selected_angle_codes])

        script += f"""
echo ""
echo "${{CYAN}}========================================${{NC}}"
echo "${{CYAN}}Configuration {config_idx}/{len(selected_configs)}: {config_name}${{NC}}"
echo "Grains: {n_grains}, Angles: {angles_display} degrees"
echo "${{CYAN}}========================================${{NC}}"

"""

        if is_single_config:
            script += f"""
# Single config mode - no subdirectory
CONFIG_DIR="$OUTPUT_BASE"

# Copy parameter template to base directory
if [ -f "grain_scripts/{config_name}/simulation_parameters_template.txt" ]; then
    cp "grain_scripts/{config_name}/simulation_parameters_template.txt" "$CONFIG_DIR/"
fi

"""
        else:
            script += f"""
# Multi-config mode - create config subdirectory
CONFIG_DIR="$OUTPUT_BASE/{config_name}"
mkdir -p "$CONFIG_DIR"

# Copy parameter template
if [ -f "grain_scripts/{config_name}/simulation_parameters_template.txt" ]; then
    cp "grain_scripts/{config_name}/simulation_parameters_template.txt" "$CONFIG_DIR/"
fi

"""

        # 处理该配置的每个角度
        for angle_code in selected_angle_codes:
            angle_value = int(angle_code)
            angle_path = config_data['angles'][angle_code]['path']

            script += f"""
echo ""
echo "${{YELLOW}}[$(date +%H:%M:%S)] Processing {config_name} - Angle {angle_value}°${{NC}}"
echo "----------------------------------------"

# Create angle output directory
ANGLE_DIR="$CONFIG_DIR/angle_{angle_code}"
mkdir -p "$ANGLE_DIR"

# Copy parameters file to angle directory
if [ -f "grain_scripts/{config_name}/simulation_parameters_template.txt" ]; then
    cp "grain_scripts/{config_name}/simulation_parameters_template.txt" "$ANGLE_DIR/simulation_parameters.txt"
fi

# Simulate all grains for this angle
for G in $(seq 1 {n_grains}); do
    GRAIN_ID=$(printf "%03d" $G)
    TIMESTAMP=$(date +%H:%M:%S)

    echo -n "[$TIMESTAMP] [{config_name}] Grain $G/{n_grains} at {angle_value}°"

    # Run mumax3 with specified command
    $MUMAX3_CMD "{angle_path}/grain_${{GRAIN_ID}}.mx3" > /dev/null 2>&1

    # Check and move output
    if [ -f "{angle_path}/grain_${{GRAIN_ID}}.out/table.txt" ]; then
        mv "{angle_path}/grain_${{GRAIN_ID}}.out/table.txt" "$ANGLE_DIR/grain_${{GRAIN_ID}}.txt"
        rm -rf "{angle_path}/grain_${{GRAIN_ID}}.out"
        echo " ${{GREEN}}[OK]${{NC}}"
        ((COMPLETED_TASKS++))
    else
        echo " ${{RED}}[FAIL]${{NC}}"
        echo "{config_name} grain_${{GRAIN_ID}} at {angle_value}°" >> "$OUTPUT_BASE/failed_list.txt"
        ((FAILED_TASKS++))
    fi

    # Progress indicator every 10 grains
    if [ $((G % 10)) -eq 0 ]; then
        PROGRESS=$((COMPLETED_TASKS * 100 / TOTAL_TASKS))
        echo ""
        echo "${{CYAN}}  Global Progress: $COMPLETED_TASKS/$TOTAL_TASKS ($PROGRESS%)${{NC}}"

        # Calculate estimated remaining time
        CURRENT_TIME=$(date +%s)
        ELAPSED=$((CURRENT_TIME - START_TIME))
        if [ $COMPLETED_TASKS -gt 0 ]; then
            AVG_TIME=$((ELAPSED / COMPLETED_TASKS))
            REMAINING_TASKS=$((TOTAL_TASKS - COMPLETED_TASKS))
            REMAINING_TIME=$((AVG_TIME * REMAINING_TASKS))
            REMAINING_HMS=$(printf '%02d:%02d:%02d' $((REMAINING_TIME/3600)) $((REMAINING_TIME%3600/60)) $((REMAINING_TIME%60)))
            echo "${{CYAN}}  Estimated remaining: $REMAINING_HMS${{NC}}"
        fi
        echo ""
    fi
done

echo "${{GREEN}}{config_name} - Angle {angle_value}° completed!${{NC}}"
"""

        script += f"""
echo "${{GREEN}}{config_name} completed!${{NC}}"
"""

    script += f"""
# Calculate total time
END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))
HOURS=$((TOTAL_TIME / 3600))
MINUTES=$(((TOTAL_TIME % 3600) / 60))
SECONDS=$((TOTAL_TIME % 60))
TIME_STR=$(printf '%02d:%02d:%02d' $HOURS $MINUTES $SECONDS)

echo ""
echo "${{GREEN}}========================================${{NC}}"
echo "${{GREEN}}All configurations completed!${{NC}}"
echo "${{GREEN}}========================================${{NC}}"
echo "Total tasks: $COMPLETED_TASKS / {total_tasks}"
if [ $FAILED_TASKS -eq 0 ]; then
    echo "${{GREEN}}Failed tasks: $FAILED_TASKS${{NC}}"
else
    echo "${{RED}}Failed tasks: $FAILED_TASKS${{NC}}"
fi
echo "Total time: $TIME_STR"
if [ $COMPLETED_TASKS -gt 0 ]; then
    AVG_TIME=$((TOTAL_TIME / COMPLETED_TASKS))
    echo "Average per task: ${{AVG_TIME}}s"
fi
echo "Results saved in: $OUTPUT_BASE"
echo ""

if [ -f "$OUTPUT_BASE/failed_list.txt" ]; then
    echo "${{RED}}Warning: Some simulations failed!${{NC}}"
    cat "$OUTPUT_BASE/failed_list.txt"
fi

echo ""
echo "Press Enter to exit..."
read
"""

    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(script)

    # 设置可执行权限
    os.chmod(output_filename, 0o755)

    print(f"[OK] Linux Bash脚本已生成: {output_filename}")
    if mumax3_path != 'mumax3':
        print(f"  使用自定义mumax3路径: {mumax3_path}")


# ========================================
# 交互界面
# ========================================
def display_configs(configs):
    """显示可用配置"""
    print("\n" + "=" * 60)
    print("检测到以下已生成的仿真配置:")
    print("=" * 60)

    items = list(configs.items())
    for idx, (config_name, config_data) in enumerate(items, 1):
        n_grains = config_data['n_grains']
        angles = config_data['angles']
        angle_list = ', '.join([str(a['angle_value']) for a in sorted(angles.values(), key=lambda x: x['angle_value'])])

        print(f"[{idx}] {config_name}")
        print(f"    晶粒数: {n_grains}, 可用角度: {angle_list}°")

    print()
    return items


def select_configs(configs):
    """选择配置(支持多选)"""
    items = display_configs(configs)

    print("选择要运行的配置:")
    print("  - 输入序号(如 1,3 )运行多个配置")
    print("  - 输入 'all' 运行所有配置")
    print("  - 输入单个序号运行一个配置")
    print()

    while True:
        choice = input(f"您的选择 [1]: ").strip() or '1'

        if choice.lower() == 'all':
            return items

        try:
            indices = [int(x.strip()) for x in choice.split(',')]
            selected = [items[i - 1] for i in indices if 1 <= i <= len(items)]

            if selected:
                return selected
            else:
                print("无效选择,请重新输入")
        except (ValueError, IndexError):
            print("输入格式错误,请重新输入")


def select_angles_for_config(config_name, config_data):
    """为特定配置选择角度"""
    angles = config_data['angles']
    angle_items = sorted(angles.items(), key=lambda x: int(x[0]))

    print(f"\n配置: {config_name}")
    print("可用角度: " + ', '.join([f"{a['angle_value']}°" for _, a in angle_items]))

    while True:
        choice = input("选择角度(如 1,3 表示角度编号;'all' 表示全部)[all]: ").strip() or 'all'

        if choice.lower() == 'all':
            return [code for code, _ in angle_items]

        try:
            parts = [x.strip() for x in choice.split(',')]

            # 尝试解析为角度值
            if all(p.isdigit() and int(p) < 180 for p in parts):
                angles_values = [int(p) for p in parts]
                selected = []
                for angle_val in angles_values:
                    angle_code = f"{angle_val:03d}"
                    if angle_code in angles:
                        selected.append(angle_code)
                    else:
                        print(f"警告: 角度 {angle_val}° 不存在,已跳过")

                if selected:
                    return selected

            print("无效选择,请重新输入")
        except (ValueError, IndexError):
            print("输入格式错误,请重新输入")


def select_script_type():
    """选择脚本类型 - 修改版本,增加自定义mumax3路径选项"""
    print("\n选择批处理脚本类型:")
    print("  [1] Windows批处理 (.bat)")
    print("  [2] PowerShell脚本 (.ps1)")
    print("  [3] Linux Bash脚本 (.sh) - mumax3已添加到PATH")
    print("  [4] Linux Bash脚本 (.sh) - 自定义mumax3路径")
    print("  [5] 生成所有类型")
    print()

    while True:
        choice = input("您的选择 [5]: ").strip() or '5'
        if choice in ['1', '2', '3', '4', '5']:
            return choice
        print("无效选择,请输入 1, 2, 3, 4 或 5")


def get_mumax3_path():
    """获取mumax3可执行文件路径 - 新增函数"""
    print("\n请输入mumax3可执行文件的完整路径或相对路径:")
    print("  示例: mumax3.11.1_linux_cuda12.0/mumax3")
    print("  示例: ./mumax3")
    print("  示例: /usr/local/bin/mumax3")
    print()

    while True:
        path = input("mumax3路径: ").strip()

        if not path:
            print("错误: 路径不能为空")
            continue

        # 检查路径是否存在（如果是相对路径，检查当前目录下是否存在）
        check_path = Path(path)
        if check_path.exists():
            if check_path.is_file():
                print(f"[OK] 已找到mumax3: {path}")
                return path
            else:
                print(f"错误: {path} 不是文件")
                continue
        else:
            # 路径不存在，但可能是用户还未下载，仍然允许使用
            confirm = input(f"警告: 在当前目录下未找到 {path}，是否仍要使用此路径? [y/N]: ").strip().lower()
            if confirm == 'y':
                return path
            else:
                print("请重新输入路径")
                continue


# ========================================
# 主程序 - 修改版本
# ========================================
def main():
    print("=" * 60)
    print("Fe-Si多晶仿真批处理脚本生成器(跨平台版)")
    print("=" * 60)

    try:
        # 1. 扫描已生成的配置
        print("\n扫描grain_scripts目录...")
        configs = scan_grain_scripts_dir()

        if not configs:
            print("错误: 未找到任何single模式的仿真配置")
            print("请先运行 generate_individual_scripts.py 生成仿真脚本")
            return

        # 2. 选择配置(支持多选)
        selected_items = select_configs(configs)

        # 3. 为每个配置选择角度
        selected_configs = []
        for config_name, config_data in selected_items:
            angle_codes = select_angles_for_config(config_name, config_data)
            selected_configs.append((config_name, config_data, angle_codes))

        # 4. 显示摘要
        print("\n" + "=" * 60)
        print("生成配置摘要")
        print("=" * 60)
        total_tasks = 0
        for config_name, config_data, angle_codes in selected_configs:
            n_grains = config_data['n_grains']
            n_angles = len(angle_codes)
            tasks = n_grains * n_angles
            total_tasks += tasks

            angles_str = ', '.join([f"{int(c)}°" for c in angle_codes])
            print(f"{config_name}: {n_grains}晶粒 × {n_angles}角度 = {tasks}任务")
            print(f"  角度: {angles_str}")

        print(f"\n总计: {total_tasks} 个仿真任务")
        print()

        confirm = input("确认生成脚本?[Y/n]: ").strip().lower()
        if confirm == 'n':
            print("已取消")
            return

        # 5. 选择脚本类型
        script_type = select_script_type()

        # 6. 如果选择了自定义mumax3路径，获取路径
        mumax3_custom_path = None
        if script_type == '4':
            mumax3_custom_path = get_mumax3_path()

        # 7. 生成文件名
        timestamp = get_timestamp()
        if len(selected_configs) == 1:
            config_name = selected_configs[0][0]
            angle_str = format_angle_list(selected_configs[0][2])
            base_name = f"run_{config_name}_angles_{angle_str}"
        else:
            base_name = f"run_multi_configs_{timestamp}"

        # 8. 生成脚本
        print("\n" + "=" * 60)
        print("开始生成脚本...")
        print("=" * 60)

        if script_type in ['1', '5']:
            bat_file = f"{base_name}.bat"
            generate_multi_config_batch_script(selected_configs, bat_file)

        if script_type in ['2', '5']:
            ps1_file = f"{base_name}.ps1"
            generate_multi_config_powershell_script(selected_configs, ps1_file)

        if script_type == '3':
            sh_file = f"{base_name}.sh"
            generate_multi_config_bash_script(selected_configs, sh_file)

        if script_type == '4':
            sh_file = f"{base_name}_custom.sh"
            generate_multi_config_bash_script(selected_configs, sh_file, mumax3_custom_path)

        if script_type == '5':
            # 生成标准PATH版本
            sh_file = f"{base_name}.sh"
            generate_multi_config_bash_script(selected_configs, sh_file)

        print("\n" + "=" * 60)
        print("[OK] 脚本生成完成!")
        print("=" * 60)

        print("\n运行说明:")
        if script_type in ['1', '5']:
            print(f"  Windows批处理: 双击运行 {base_name}.bat")
        if script_type in ['2', '5']:
            print(f"  PowerShell: 运行 powershell -ExecutionPolicy Bypass -File {base_name}.ps1")
        if script_type == '3':
            print(f"  Linux Bash: 运行 ./{base_name}.sh")
        if script_type == '4':
            print(f"  Linux Bash (自定义路径): 运行 ./{base_name}_custom.sh")
            print(f"  使用mumax3路径: {mumax3_custom_path}")
        if script_type == '5':
            print(f"  Linux Bash: 运行 ./{base_name}.sh")

        print(f"\n预计任务数: {total_tasks}")
        print(f"输出目录: output/{base_name}/")

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
