import asyncio
import json
import websockets
import signal
from datetime import datetime
from aiohttp import web

# 全局变量，用于存储最新心率数据
latest_heart_rate = None
connected_clients = set()
is_shutting_down = False  # 新增：标记是否正在关闭

# 网页 HTML 内容
HTML_CONTENT = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>心率显示</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        :root {
            --primary-color: #ff4757;
            --highlight-color: #ff6b81;
        }
        
        body, html {
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            background: transparent;
        }
        
        .container {
            display: flex;
            align-items: center;
            justify-content: center;
            white-space: nowrap;
        }
        
        .heart-icon {
            display: inline-block;
            color: var(--primary-color);
            font-size: 60px;
            margin-right: 15px;
            animation: heartbeat 1.5s infinite;
            vertical-align: middle;
        }
        
        .heart-rate {
            display: inline-block;
            font-size: 80px;
            font-weight: bold;
            color: var(--highlight-color);
            text-shadow: 0 0 10px rgba(255, 107, 129, 0.7);
            vertical-align: middle;
        }
        
        @keyframes heartbeat {
            0% { transform: scale(1); }
            25% { transform: scale(1.1); }
            50% { transform: scale(1); }
            75% { transform: scale(1.1); }
            100% { transform: scale(1); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="heart-icon">
            <i class="fas fa-heart"></i>
        </div>
        <div class="heart-rate" id="currentRate">--</div>
    </div>
    
    <script>
        const accessCode = 'XPH5qChgcd';
        let ws = null;
        
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            const host = window.location.host;
            
            ws = new WebSocket(`${protocol}${host}/ws`);
            
            ws.onopen = function() {
                ws.send(JSON.stringify({
                    type: 'auth',
                    code: accessCode
                }));
            };
            
            ws.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'heart_rate') {
                        updateHeartRate(data.current);
                    }
                } catch (e) {
                    console.error('数据解析错误:', e);
                }
            };
            
            ws.onclose = function() {
                setTimeout(connectWebSocket, 3000);
            };
            
            ws.onerror = function(error) {
                console.error('WebSocket 错误:', error);
            };
        }
        
        function updateHeartRate(current) {
            const rateElement = document.getElementById('currentRate');
            const heartIcon = document.querySelector('.heart-icon i');
            
            if (rateElement) {
                rateElement.textContent = current !== undefined ? current : '--';
            }
            
            if (heartIcon && current !== '--' && !isNaN(current)) {
                const rate = parseInt(current);
                const duration = Math.max(0.5, 2 - (rate - 60) / 100);
                heartIcon.style.animationDuration = `${duration}s`;
            }
        }
        
        // 初始化连接
        connectWebSocket();
    </script>
</body>
</html>'''


class HeartRateClient:
    def __init__(self, uri):
        self.uri = uri
        self.websocket = None
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        self.heartbeat_interval = 15
        self.is_running = True
        self.heartbeat_task = None
        
    def is_connection_open(self):
        """安全检查连接是否打开"""
        if self.websocket is None:
            return False
        try:
            if hasattr(self.websocket, 'closed'):
                return not self.websocket.closed
            elif hasattr(self.websocket, 'open'):
                return self.websocket.open
            else:
                return True
        except AttributeError:
            return False
        
    async def send_heartbeat(self):
        """发送心跳保持连接"""
        try:
            if self.is_connection_open():
                heartbeat = {
                    "type": "heartbeat",
                    "timestamp": datetime.now().isoformat()
                }
                await self.websocket.send(json.dumps(heartbeat))
        except Exception as e:
            print(f"[心跳失败] {e}")
    
    async def heartbeat_loop(self):
        """心跳循环"""
        try:
            while self.is_running and self.is_connection_open():
                try:
                    await asyncio.sleep(self.heartbeat_interval)
                    if self.is_connection_open():
                        await self.send_heartbeat()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[心跳错误] {e}")
                    break
        except asyncio.CancelledError:
            pass
        finally:
            print("[心跳] 心跳循环结束")
    
    async def connect(self):
        """主连接逻辑"""
        while self.is_running:
            try:
                print(f"[*] 尝试连接：{self.uri} (时间：{datetime.now().strftime('%H:%M:%S')})")
                
                async with websockets.connect(
                    self.uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=2**20,
                    compression=None,
                ) as websocket:
                    self.websocket = websocket
                    self.reconnect_delay = 5
                    print(f"[✓] 连接成功！(时间：{datetime.now().strftime('%H:%M:%S')})")
                    
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                    
                    try:
                        async for message in websocket:
                            try:
                                data = json.loads(message)
                                
                                if isinstance(data, dict):
                                    msg_type = data.get('type', 'unknown')
                                    
                                    if msg_type == 'heart_rate':
                                        value = data.get('value')
                                        print(f"  ❤️  心率：{value} {data.get('unit', 'bpm')}")
                                        # 更新全局心率数据并推送给网页
                                        await broadcast_heart_rate(value)
                                    elif msg_type == 'heartbeat':
                                        print(f"  ✓ 心跳响应")
                                    elif msg_type == 'ack':
                                        print(f"  ✓ 服务器确认：{data.get('message')}")
                                    else:
                                        print(f"  📦 {data}")
                                elif isinstance(data, (int, float)):
                                    print(f"  ❤️  心率值：{data} bpm")
                                    # 更新全局心率数据并推送给网页
                                    await broadcast_heart_rate(data)
                                else:
                                    print(f"  📝 {message}")
                                    
                            except json.JSONDecodeError:
                                print(f"  📝 原始：{message}")
                                
                    except websockets.exceptions.ConnectionClosedError as e:
                        print(f"\n[⚠️] 连接异常断开：{e}")
                    except websockets.exceptions.ConnectionClosedOK as e:
                        print(f"\n[✓] 连接正常关闭：{e}")
                    except asyncio.CancelledError:
                        print("\n[⚠️] 连接被取消")
                        break
                    finally:
                        if self.heartbeat_task and not self.heartbeat_task.done():
                            self.heartbeat_task.cancel()
                            try:
                                await self.heartbeat_task
                            except asyncio.CancelledError:
                                pass
                        self.heartbeat_task = None
                        self.websocket = None
                        
            except websockets.exceptions.InvalidStatus as e:
                print(f"[错误] HTTP 状态码：{e.status_code}")
            except ConnectionRefusedError:
                print(f"[错误] 连接被拒绝")
            except OSError as e:
                print(f"[错误] 网络错误：{e}")
            except asyncio.CancelledError:
                print("\n[⚠️] 连接任务被取消")
                break
            except KeyboardInterrupt:
                print("\n[⚠️] 用户中断")
                break
            except Exception as e:
                print(f"[错误] 未知：{type(e).__name__}: {e}")
            
            if not self.is_running:
                print("[*] 停止信号已收到，退出重连循环")
                break
                
            print(f"[*] {self.reconnect_delay}秒后重连... (按 Ctrl+C 停止)")
            
            try:
                for i in range(int(self.reconnect_delay)):
                    if not self.is_running:
                        print("[*] 重连被取消")
                        break
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                print("\n[⚠️] 重连等待被取消")
                break
            except KeyboardInterrupt:
                print("\n[⚠️] 用户中断重连")
                break
                
            if not self.is_running:
                break
            
            if self.reconnect_delay < self.max_reconnect_delay:
                self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)
        
        print("[*] 连接循环已结束")
    
    def stop(self):
        """停止客户端"""
        self.is_running = False
        print("\n[*] 收到停止信号...")


async def broadcast_heart_rate(value):
    """广播心率数据到所有连接的网页客户端"""
    global latest_heart_rate
    latest_heart_rate = value
    
    message = json.dumps({
        "type": "heart_rate",
        "current": value,
        "timestamp": datetime.now().isoformat()
    })
    
    # 发送给所有连接的客户端
    disconnected = set()
    for ws in connected_clients:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)
    
    # 清理断开的连接
    connected_clients.difference_update(disconnected)

async def handle_websocket(request):
    """处理网页 WebSocket 连接"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    connected_clients.add(ws)
    print(f"[🌐] 网页客户端连接，当前连接数：{len(connected_clients)}")
    
    # 如果有最新心率数据，立即发送给新连接的客户端
    if latest_heart_rate is not None:
        try:
            await ws.send_str(json.dumps({
                "type": "heart_rate",
                "current": latest_heart_rate,
                "timestamp": datetime.now().isoformat()
            }))
        except Exception:
            pass
    
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get('type') == 'auth':
                        # 验证访问码
                        if data.get('code') == 'XPH5qChgcd':
                            await ws.send_str(json.dumps({
                                "type": "auth_result",
                                "success": True
                            }))
                            print(f"[🌐] 网页客户端认证成功")
                        else:
                            await ws.send_str(json.dumps({
                                "type": "auth_result",
                                "success": False,
                                "message": "访问码错误"
                            }))
                            await ws.close()
                except json.JSONDecodeError:
                    pass
            elif msg.type == web.WSMsgType.ERROR:
                print(f"[🌐] WebSocket 错误：{ws.exception()}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        if not is_shutting_down:
            print(f"[🌐] WebSocket 异常：{e}")
    finally:
        connected_clients.discard(ws)
        if not is_shutting_down:
            print(f"[🌐] 网页客户端断开，当前连接数：{len(connected_clients)}")
    
    return ws


async def handle_index(request):
    """处理网页请求"""
    return web.Response(text=HTML_CONTENT, content_type='text/html')


async def start_web_server():
    """启动网页服务器"""
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', handle_websocket)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 20888)
    await site.start()
    
    print("=" * 60)
    print("🌐 网页服务已启动")
    print("📍 访问地址：http://127.0.0.1:20888")
    print("=" * 60)
    
    return runner


async def main():
    global is_shutting_down
    
    # 手动输入 IP 地址
    print("=" * 60)
    print("🔗 WebSocket 心率客户端 + 网页服务")
    print("=" * 60)
    
    while True:
        ip = input("\n请输入服务器 IP 地址 (如 192.168.3.168): ").strip()
        if ip:
            break
        print("[错误] IP 地址不能为空，请重新输入！")
    
    port = 6667  # 固定端口
    uri = f"ws://{ip}:{port}"
    
    print(f"\n[*] 目标地址：{uri}")
    print("[*] 按 Ctrl+C 停止程序\n")
    
    # 启动网页服务器
    web_runner = await start_web_server()
    
    client = HeartRateClient(uri)
    
    # 设置信号处理
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, client.stop)
    except (NotImplementedError, OSError):
        pass
    
    try:
        # 同时运行心跳客户端
        await client.connect()
    except asyncio.CancelledError:
        pass
    finally:
        # 标记正在关闭，避免关闭时的日志输出
        is_shutting_down = True
        
        # 取消心跳任务
        if client.heartbeat_task and not client.heartbeat_task.done():
            client.heartbeat_task.cancel()
            try:
                await client.heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # 关闭所有网页客户端连接
        for ws in list(connected_clients):
            try:
                await ws.close()
            except Exception:
                pass
        connected_clients.clear()
        
        # 关闭网页服务器
        try:
            await web_runner.cleanup()
        except Exception:
            pass
        
        print("[*] 程序已退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] 强制退出")
    except Exception as e:
        print(f"\n[错误] {e}")