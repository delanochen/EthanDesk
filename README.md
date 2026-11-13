# 远程桌面（类 AnyDesk 自建版）

一套自建的三件套远程桌面系统：Windows 被控端、Windows 原生主控端、可部署在 Debian 上的公网中继。代码全部自有，**不含也不支持任何软件授权绕过**——它与"去掉 AnyDesk 商业版弹窗"没有关系，只是同类型的自建替代方案。

## 能力对照

| 能力 | 本方案 | 说明 |
|---|---|---|
| 设备 ID / 在线列表 | 有 | 9 位设备 ID，中继维护在线状态（30s 心跳超时） |
| 实时画面 | 有 | mss 抓屏 + JPEG，画质/缩放/帧率可在线调 |
| 鼠标键盘控制 | 有 | SendInput 注入，归一化坐标适配多屏 |
| 剪贴板同步 | 有（仅文本） | 双向，去回声 |
| 文件传输 | 有（双向） | 1 MiB 分块 + SHA-256 校验 |
| 设备管理台 | 有 | 设备列表、在线/离线、会话历史（SQLite） |
| 每次会话人工同意 | 有（强制） | 被控端弹窗确认，30s 未响应自动拒绝 |
| 无人值守 / 隐身运行 | 无 | 明确不做 |
| 锁屏 / UAC 界面控制 | 无 | 需要 SYSTEM 权限与安全桌面，本版不支持 |
| H.264、NAT 穿透、中继集群 | 无 | 后续可升级 |

## 架构

```
主控端(Tkinter)  --wss-->  中继(aiohttp)  --wss-->  被控端(Tkinter)
     ^                        |    |                     |
     | 二进制 JPEG 帧          |    SQLite: agents/sessions | mss 抓屏 + SendInput
     + JSON 控制/剪贴板/文件   |                           + Win32 剪贴板
```

协议要点：

- 单条 WebSocket 复用。文本帧 = JSON 信令，二进制帧 = 画面或文件块。
- 二进制帧前必须先发 `{"t":"bin","kind":"frame|file",...}` 头，接收方按序配对（`common/protocol.py`）。
- 会话握手：`session_request` → 被控端 `session_accept`/`session_denied` → 中继回 `session_accept` → 中继发 `session_start` → 被控端开始推流。中继只在会话激活后转发，避免首帧泄漏。
- 坐标统一归一化 0..1；被控端按虚拟桌面矩形换算成像素与 0..65535 绝对单位。
- 键位：主控端发 Tk keysym，被控端映射为 Windows VK（`common/keys.py`）。
- 鉴权：主控端用口令换 HMAC-SHA256 token；被控端用 `agent_id + key` 认证。

## 目录

```
remote-desk/
├─ common/        protocol.py  auth.py  coords.py  keys.py  files.py
├─ relay/         app.py(服务+桥接)  config.py  registry.py(在线表+会话库)
├─ agent/         app.py(GUI+worker)  screen.py  inject.py  clip.py  files_transfer.py  config.py
├─ viewer/        app.py(GUI)  client.py(HTTP/WS 客户端)
├─ deploy/        Dockerfile  docker-compose.yml  nginx.conf  cloudflared.yml  systemd/
├─ tests/         13 个测试模块 + smoke_e2e + live_agent_check
└─ start-*.bat    三个启动脚本
```

## 本机快速验证（三个进程）

```powershell
python -m pip install -r requirements.txt

# 1) 中继（本机测试）
$env:DESKRELAY_VIEWER_PASSWORD = "viewer-pass-12345678"
$env:DESKRELAY_AGENT_KEY       = "agent-key-0123456789abcdef0123456789"
python -m relay.app          # 或 start-relay.bat

# 2) 被控端
start-agent.bat              # 中继地址填 ws://127.0.0.1:8080/ws/agent，密钥同上，点"开始服务"

# 3) 主控端
start-viewer.bat             # 中继地址 http://127.0.0.1:8080，口令同上，登录后双击设备
```

被控端会弹出"远程控制请求"，点"是"后主控端开始出画面。剪贴板同步勾选后双向生效；"上传文件…"把主控端文件推到被控端，被控端"向远控方发送文件…"反向推送，接收目录 `%USERPROFILE%\Downloads\deskrelay`。

## 部署到 Debian（192.168.7.202）

```bash
# 1) 拷贝项目到服务器
rsync -av remote-desk/ desk@192.168.7.202:/opt/deskrelay/

# 2) 配置密钥
cd /opt/deskrelay/deploy
cp deskrelay.env.example deskrelay.env
#   编辑 deskrelay.env：口令 ≥16 字符，agent key ≥32 字符随机串

# 3) 起服务（端口只绑 127.0.0.1，公网入口交给 nginx 或 cloudflared）
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml logs -f | head -20

# 4A) nginx 反代（已有证书）
sudo cp nginx.conf /etc/nginx/sites-available/deskrelay.conf
sudo ln -sf /etc/nginx/sites-available/deskrelay.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 4B) 或 Cloudflare Tunnel（不开放任何入站端口）
cloudflared tunnel --config cloudflared.yml run deskrelay

# 5) 验证
curl -s https://desk.example.com/api/health
```

不使用 Docker 时，`deploy/systemd/deskrelay.service` 提供了 venv + systemd 的等价部署方式。

被控端与主控端把中继地址改为 `wss://desk.example.com/ws/agent`（被控端）与 `https://desk.example.com`（主控端）。

## 安全要求（务必遵守）

- 中继口令与 agent key 必须足够强：口令 ≥16 字符、agent key ≥32 字符随机值；弱值会在启动时告警。
- **必须 TLS**。明文暴露 8080 等于把每台被控端交给公网。
- compose 默认只绑 `127.0.0.1:8080`，不要改成 `0.0.0.0` 直接对公网。
- 每次会话都需要被控端本地点"是"；没有免确认模式，这是刻意设计。
- 所有会话写入 `/data/relay.db` 的 `sessions` 表，可通过主控端"会话记录"或 `GET /api/sessions` 审计。
- 被控端配置（含 agent key）保存在 `%APPDATA%\deskrelay\agent.json`，不要共享该文件。

## 测试与验证

```powershell
python -m pytest tests -q          # 权威入口：全部单元/桥接/物料测试
python -m tests.smoke_e2e          # 中继 + 假被控端 + 真客户端，无 GUI
python -m tests.live_agent_check   # 真实抓屏链路（会用当前桌面，不注入键鼠）
```

## 已知限制

1. 锁屏、登录界面、UAC 弹窗不可控（需要 SYSTEM 权限与安全桌面注入）。
2. JPEG over WebSocket 延迟高于 WebRTC：局域网约 30–80ms，跨公网取决于上行带宽；实测 1920×1080、0.5 缩放、质量 70 下单帧约 48KB。
3. 抓屏取 `monitor=0`（所有显示器合并画面），暂未做逐屏切换。
4. 文件传输整文件读入内存（>2GB 文件需改流式）。
5. 剪贴板只同步文本，不支持图片/文件。
6. 设备 ID 与密钥存在本地配置文件，无自动轮换。

## 协议速查

| 消息 | 方向 | 作用 |
|---|---|---|
| `hello` / `hello_ok` | agent→relay→agent | 设备认证与注册 |
| `session_request` / `session_accept` / `session_denied` | relay↔agent | 会话同意 |
| `session_start` / `session_close` | relay→agent | 放行推流 / 结束 |
| `meta` | agent→viewer | 桌面尺寸、缩放、帧率 |
| `input` | viewer→agent | 鼠标/键盘事件 |
| `set_quality` / `frame_recv` | viewer→agent | 调画质、流控回执 |
| `clip` | 双向 | 剪贴板文本 |
| `file_offer` / `file_done` | 双向 | 文件开始/完成 |
| `bin` | 双向 | 二进制帧头 |

## 打包为 exe（可选）

```powershell
python -m pip install pyinstaller
pyinstaller --noconsole --onefile --name DeskAgent  -p . agent/__main__.py
pyinstaller --noconsole --onefile --name DeskViewer -p . viewer/__main__.py
```
