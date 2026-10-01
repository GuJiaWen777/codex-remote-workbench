# Codex Remote Workbench

轻量的远程补丁 MCP。让本地 Codex 通过系统 OpenSSH 检查远端文件、预览补丁、备份并应用修改。

首版面向 **Windows / Linux / macOS 本地客户端 + Linux/POSIX 远端**。不需要远端运行模型会话，也不修改已安装的 npm 包。

## 功能与边界

| 工具 | 行为 |
| --- | --- |
| `remote_doctor` | 只读检查工作目录、Python 与 Codex 可执行文件 |
| `remote_inspect` | 读取 UTF-8 文件并返回 SHA-256；不存在的文件返回 `null` |
| `remote_apply_patch` | 核对哈希，在暂存副本应用标准 Codex 补丁，预览或备份后写回 |

支持 Add / Update / Delete，最多 32 个文件。暂不支持移动文件、二进制文件、符号链接、硬链接、远端 Windows、任意命令执行或后台任务管理。

这是现有 SSH 工作流的增强层。需要通用命令和传输时，可以并行使用原版 SSH MCP，例如 `@aiondadotcom/mcp-ssh`；本项目不修改或复制其实现，也不依赖该包。

## 前提

- 本地 Node.js 22+，系统 `ssh` 位于 PATH。
- 远端 Python 3.10+、支持 `--codex-run-as-apply-patch` 入口的 Codex 可执行文件。
- 已配置 SSH 别名和认证，`ssh -o BatchMode=yes -o StrictHostKeyChecking=yes YOUR_SSH_ALIAS` 可以连接。
- 首次主机指纹应由用户通过可信渠道核对。工具拒绝未知或变更的主机密钥，不自动接受。

工具只调用远端 Codex 的补丁入口，不调用模型 API。不同 Codex 版本的入口兼容性必须用临时文件实测；`remote_doctor` 只检查可执行文件存在，不能代替补丁测试。

## 安装与配置

克隆仓库后执行：

```sh
npm ci
npm test
python -m unittest discover -s test -p test_worker.py -v
```

将 `config.example.json` 复制到**仓库外的私人配置目录**，填入真实主机别名、允许编辑的工作目录和远端 Codex 绝对路径。不要把凭据或真实服务器信息放入 Git。

```json
{
  "hosts": {
    "development": {
      "sshAlias": "YOUR_SSH_ALIAS",
      "roots": ["/ABSOLUTE/PATH/TO/EDITABLE/WORKSPACE"],
      "codexPath": "/ABSOLUTE/PATH/TO/codex",
      "pythonCommand": "python3",
      "timeoutMs": 45000
    }
  }
}
```

配置由本地 MCP 读取；模型只能选择已配置的逻辑主机名，不能在工具参数里更改 SSH 目标、Codex 路径或允许根目录。SSH 密钥继续由系统 OpenSSH 管理。

在 Codex 的 `config.toml` 添加以下配置，替换占位路径。Windows TOML 路径可使用单引号 literal string。

```toml
[mcp_servers.remote_workbench]
command = "node"
args = ["/ABSOLUTE/PATH/TO/REPOSITORY/src/server.js", "--config", "/ABSOLUTE/PATH/TO/PRIVATE/config.json"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

Windows 示例：

```toml
[mcp_servers.remote_workbench]
command = "node"
args = ['C:\PATH\TO\REPOSITORY\src\server.js', '--config', 'C:\PRIVATE\config.json']
startup_timeout_sec = 20
tool_timeout_sec = 60
```

重新加载 MCP 后，先对允许目录执行 `remote_doctor`。如增加 SSH 超时，MCP 的 `tool_timeout_sec` 也要大于相应 `timeoutMs`。本项目不会自动编辑用户的全局配置或安装远端软件。

Codex 的 STDIO 配置方式参见 [官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

## 推荐使用流程

1. 在允许工作目录中调用 `remote_inspect`，读取所有将修改的文件。
2. 构造补丁，`expectedHashes` 必须完整列出每个目标：现有文件使用检查结果，新建文件使用 `null`。
3. 先调用 `remote_apply_patch`，保留默认 `dryRun: true`，核对 diff。
4. 明确需要应用时，使用同一补丁和哈希，设置 `dryRun: false`。
5. 检查返回状态，并通过现有 SSH 工作流执行项目测试。

参数示例（哈希必须来自真实检查，不能照抄占位值）：

```json
{
  "host": "development",
  "workdir": "/ABSOLUTE/PATH/TO/EDITABLE/WORKSPACE",
  "patch": "*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch",
  "expectedHashes": {"hello.txt": null},
  "dryRun": true
}
```

文件修改前的字节备份、哈希和状态保存在允许根目录下的 `.remote-workbench/<job-id>/`。预览也会创建暂存与审计目录，但不修改目标文件。该目录使用私有权限，可能包含敏感原文，应按自己的保留策略管理；首版不自动清理或自动恢复历史任务。

## 安全限制与失败语义

- 本地启动 SSH 使用 argv 和 `shell: false`；补丁正文通过 stdin 传输，不拼入远端 shell 命令。
- 仅允许配置根目录中的相对目标路径，禁止 `..`、绝对目标路径、链接和内部状态路径。
- 所有目标都要求检查哈希。根目录级锁协调本工具的写操作；写回前再核对一次哈希。
- Codex 解析器只在暂存副本上运行，补丁不成立时不会修改目标。
- 每个目标文件通过替换写回，备份先于写入；捕获到提交失败时尝试恢复已修改文件，并记录恢复失败。
- **不是跨文件、断电安全的事务。** 进程被强杀、服务器重启或网络中断时可能处于部分提交状态。SSH 超时只说明本地等待结束，不证明远端停止；请检查目标和审计，不要自动重试。
- **不是操作系统沙箱。** 外部进程不遵守本工具锁；恶意或并发修改路径仍存在竞态风险。信任本地配置与远端账户，必要时通过专用账户和权限隔离。
- 现有普通权限位保留，新文件默认为 `0600`；ACL、扩展属性、原文件所有者及时间戳不保证保留。
- 单文件及一次检查的文件总内容最多 2 MiB、补丁最多 60,000 UTF-8 字节。暂存和备份占用磁盘；首版每次保留任务记录。
- 通用 SSH MCP 如果仍开放任意命令，不会因为这个补丁工具的目录限制而一起受到约束。

## 开发与测试

`npm test` 覆盖配置校验、OpenSSH 参数、stdin 隔离、响应错误/超时以及真实 MCP 初始化与工具发现。

Python 测试覆盖路径、哈希、暂存、备份、解析失败、外部变更和提交失败恢复。Windows 会跳过 POSIX 链接测试；Linux CI 验证链接与锁行为。真实 SSH / Codex 兼容性应在自建的临时工作目录中另外验证；仓库不包含任何真实连接配置、生产文件或实测日志。

可选真实连接测试，仅对**新建的隔离测试根目录**运行：

```sh
node scripts/remote-smoke.js /ABSOLUTE/PATH/TO/PRIVATE/test-config.json development --isolated-test-root
```

该脚本只修改自己生成的 UUID 文件名，验证预览、新建、更新、删除、中文传输、哈希冲突与路径穿越防护。成功时删除自己创建的目标测试文件；审计与备份保留在测试根目录，失败时不会盲目清理。不要在生产根目录运行。

隐私与信任边界见 [SECURITY.md](SECURITY.md)。

## 致谢与来源

感谢 [197895/codex-remote-apply-patch](https://github.com/197895/codex-remote-apply-patch) 提出的本地 Codex + SSH + 远端补丁入口工作流，为本项目提供了方案灵感。

本项目为独立实现，未复制该项目的 README、GUIDE 或实现内容；不代表原作者背书，也不是其官方发行版。

感谢 [aiondadotcom/mcp-ssh](https://github.com/aiondadotcom/mcp-ssh) 展示基于系统 OpenSSH 的 MCP 工作流。可作为通用 SSH 操作的并行工具，无需修改其 npm 包。

## License

本仓库独立编写的代码与文档采用 MIT License。外部运行时和依赖遵循各自许可证。
