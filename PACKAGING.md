# z-paper 打包指南

本文档说明如何把 z-paper 打包成 Windows 安装程序（`.exe`）。打包流水线位于 `packaging/` 目录，产出单文件安装包。

## 一、前置条件

| 工具 | 用途 | 备注 |
|---|---|---|
| Windows 10/11 | 打包平台 | 打包脚本为 PowerShell |
| Node.js 18+ | 构建前端 | `npm run build` |
| Python 3.12（任一环境） | 准备运行时依赖 | 仅用于 `pip install`，不直接打进包 |
| Inno Setup 6 | 编译安装包 | 需有 `ISCC.exe` |

> `ISCC.exe` 默认从 `C:\Program Files (x86)\Inno Setup 6\` 或 `D:\Program Files (x86)\Inno Setup 6\` 查找；若装在别处，改 `packaging/build-windows-installer.ps1` 里 `Get-InnoCompiler` 的候选路径。

## 二、版本号（三处必须同步修改）

发新版时，以下三处版本号要一致，否则界面显示的版本号和安装包版本号会不一致：

1. `packaging/build-windows-installer.ps1` 第 7 行：`$AppVersion = "x.y.z"`
2. `packaging/z-paper.iss` 第 10 行：`#define AppVersion "x.y.z"`
3. `backend/app/api/sync.py` 第 35 行：`SYNC_APP_VERSION = "x.y.z"`

其中第 3 处是「同 WLAN 数据同步」面板上显示的版本号，漏改会让人误以为是旧版。

## 三、准备 Python 运行时（关键步骤）

> **重要**：不要用 `backend/venv` 打包。该项目开发用的 `backend/venv` 可能是 Anaconda 创建的，它的 `sys.base_prefix` 指向整个 Anaconda 目录，直接打包会复制进几十 GB 的冗余文件。必须使用干净的 Python embeddable。

在项目根目录准备一个干净的 `python-embed312/`：

```bash
# 1. 下载 Python 3.12 embeddable（约 11 MB）
curl -L -o python-embed.zip https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip

# 2. 解压到 python-embed312/
mkdir python-embed312
cd python-embed312
unzip ../python-embed.zip

# 3. 改写 python312._pth，内容改为（启用 site-packages）：
#    python312.zip
#    .
#    Lib/site-packages
#    import site
printf 'python312.zip\n.\nLib/site-packages\nimport site\n' > python312._pth

# 4. 安装 pip
curl -L -o get-pip.py https://bootstrap.pypa.io/get-pip.py
./python.exe get-pip.py

# 5. 安装项目依赖（干净安装，不复制 venv 的 site-packages）
./python.exe -m pip install -r ../backend/requirements.txt
```

完成后 `python-embed312/` 约 150–200 MB（含标准库和依赖），`Lib/site-packages` 应只有 `requirements.txt` 声明的依赖，不含 numpy/lancedb 等无关包。

## 四、执行打包

在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build-windows-installer.ps1
```

脚本流程（共 6 步）：

1. `[1/6]` 构建前端（`npm run build`）
2. `[2/6]` 组装干净的应用目录到 `packaging/build/app/`
3. `[3/6]` 生成应用图标
4. `[4/6]` 复制 `python-embed312/` 到打包目录
5. `[5/6]` 校验打包后的运行时能否 import 依赖并加载 `app.main`
6. `[6/6]` 用 Inno Setup 编译安装包

## 五、产物

- 安装包：`installer/z-paper-x.y.z-setup.exe`
- 中间产物：`packaging/build/`（可随时删除，下次打包自动重建）

## 六、隐私与数据说明

以下目录**不进 git，也不进安装包**（见 `.gitignore`），升级安装时会保留：

- `backend/data/` —— SQLite 数据库（论文、配置、API Key）
- `backend/uploads/` —— 上传的 PDF 和图片
- `backend/logs/` —— 日志
- `.env` —— 本地环境变量（可能含 API Key）
- `python-embed312/`、`python-embed/` —— 打包用运行时

因此安装包本身不含任何用户数据；用户升级时，模型名 / API 地址会按 `CONFIG_VERSION` 机制重置为新版默认值，已保存的 API Key 保留。

## 七、常见坑

- **打包出来体积巨大（几个 GB）**：说明运行时用错了（用了 Anaconda 的 `base_prefix`）。检查第 3 步是否使用了干净的 `python-embed312/`。
- **ISCC.exe 找不到**：Inno Setup 装在非默认盘符，改 `Get-InnoCompiler` 候选路径。
- **升级后界面显示旧版本号**：漏改 `sync.py` 里的 `SYNC_APP_VERSION`。
- **打包脚本复制了 venv 的 site-packages**：确认 `build-windows-installer.ps1` 的 `[4/6]` 只复制 `python-embed312/`，不再复制 `backend/venv/Lib/site-packages`。
