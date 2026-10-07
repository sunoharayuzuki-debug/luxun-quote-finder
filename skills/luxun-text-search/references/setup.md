# 安装、导入与数据边界

## 安装到 Codex

解压分享包，将其中整个 `luxun-text-search` 文件夹放到 `~/.codex/skills/`（Windows 为用户主目录的 `.codex/skills/`）。如果设置了 `CODEX_HOME`，放在其 `skills/` 下。不要覆盖同名旧目录而丢失本地配置。重新打开会话后尝试 `$luxun-text-search`。其他支持 Agent Skills 的客户端需按其安装方式加载，并提供本地 Python 执行权限；仅把文件上传到普通聊天不保证可以运行。

这个包不附带书籍、数据库或 Python。Python 3.10+ 可运行查询；PDF 导入另需 `pypdf`。建议使用虚拟环境而不更改系统 Python：

```sh
python3 -m venv "/自行选择的位置/luxun-env"
"/自行选择的位置/luxun-env/bin/python" -m pip install -r "<skill>/requirements.txt"
"/自行选择的位置/luxun-env/bin/python" "<skill>/scripts/luxun.py" doctor
```

Windows 虚拟环境解释器在 `luxun-env\Scripts\python.exe`。后续命令使用同一解释器。

## 使用现有索引

每次传入 `--database`，或设置环境变量 `LUXUN_DATABASE`。也可在自己安装的 Skill 根目录创建 `local.json`：

```json
{"database": "/你自己的目录/luxun.sqlite"}
```

这份配置只在本机保存，不放进分享包。源 PDF 移动后需重新导入，因为索引内保留原文件的绝对路径。

## 从自己的 OCR PDF 建立索引

```sh
python3 "<skill>/scripts/luxun.py" import --source "/自己的鲁迅全集" --database "/自己的数据目录/luxun.sqlite"
```

输入文件名须含 `第01卷` 或 `第1卷` 等阿拉伯数字卷号；读取目录直属的 `.pdf` 文件。可以只导入部分卷。输入必须已有可提取的文字层，脚本不会重新 OCR。

默认数据库位置是用户目录下 `.local/share/luxun-text-search/luxun.sqlite`，并在其所在目录生成 `cache/` 与 `import-report.json`。数据放在 Skill 目录外，避免升级或分享时混入。

索引已存在时脚本拒绝覆盖。只有用户要求重新导入并确认原索引为可再生的自动结果后，才加 `--rebuild`。不得在此数据库内保存唯一一份人工校订，重建会替换索引。

同卷多个 PDF 按文件名优先选择无 `(1)` 后缀者，其余记录为跳过；不保证各文件内容相同。导入报告列出卷页统计及空白文字层页数，空白不必然代表缺页，也可能是图片。

## 结果限制

目标版本为用户指定的人民文学出版社 2005 年版；程序不鉴定输入版本。脚注、篇目边界和页码均为自动候选，书信、日记和层级目录更需要人工检查。检索并不直接给出一份已经校勘完成的初刊数据库。

检索只读连接数据库。`page` 最多返回单页 6,000 字符，并标明是否截断；它会把该页文字交给调用方，按核验需要使用。

## 分享与隐私

本包仅提供代码和工作流程。接收者自行准备有权使用的文本；不要附加第三方下载链接、扫描件或整套注释。原项目的 ZIP 打包脚本采用逐文件白名单，普通压缩软件不会遵守 `.gitignore`。

Python 脚本无云端 API 调用。客户端仍可能把命令结果发送到它使用的模型服务，因此“本地执行”不等于“模型看不到命中文字”。保密要求严格时，在本地终端或原项目网页自行查询，避免将结果传给云端模型。
