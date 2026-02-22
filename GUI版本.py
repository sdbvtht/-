import asyncio
import json
import websockets
import signal
import sys
import os
import threading
import queue
import socket
from datetime import datetime
from aiohttp import web
import tkinter as tk
from tkinter import ttk

# 全局变量
latest_heart_rate = None
connected_clients = set()
is_shutting_down = False

# GUI 全局变量
gui_root = None
heart_rate_label = None
ip_entry = None
status_label = None
log_text = None
web_url_label = None
client = None
web_runner = None
asyncio_loop = None
ip_change_queue = None

# 重定向输出到 GUI
class TextRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget
    
    def write(self, text):
        if self.text_widget and not is_shutting_down and text.strip():
            try:
                self.text_widget.insert(tk.END, text)
                self.text_widget.see(tk.END)
            except:
                pass
    
    def flush(self):
        pass


# 网页 HTML 内容
HTML_CONTENT = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>心率显示</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body, html {
            width: 100%;
            height: 100%;
            display: flex;
            justify-content: center;
            align-items: center;
            background: transparent;
            font-family: Arial, sans-serif;
        }
        
        .container {
            display: flex;
            align-items: center;
            justify-content: center;
            white-space: nowrap;
        }
        
        .heart-icon {
            display: inline-block;
            color: #ff4757;
            font-size: 60px;
            margin-right: 15px;
            animation: heartbeat 1.5s infinite;
            vertical-align: middle;
        }
        
        .heart-rate {
            display: inline-block;
            font-size: 80px;
            font-weight: bold;
            color: #ff6b81;
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
        <div class="heart-icon">❤</div>
        <div class="heart-rate" id="currentRate">--</div>
    </div>
    
    <script>
        const accessCode = 'XPH5qChgcd';
        let ws = null;
        
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            const host = window.location.host;
            
            ws = new WebSocket(protocol + '//' + host + '/ws');
            
            ws.onopen = function() {
                console.log('WebSocket 已连接');
                ws.send(JSON.stringify({
                    type: 'auth',
                    code: accessCode
                }));
            };
            
            ws.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('收到消息:', data);
                    if (data.type === 'heart_rate') {
                        updateHeartRate(data.current);
                    }
                } catch (e) {
                    console.error('数据解析错误:', e);
                }
            };
            
            ws.onclose = function() {
                console.log('WebSocket 已关闭，3 秒后重连');
                setTimeout(connectWebSocket, 3000);
            };
            
            ws.onerror = function(error) {
                console.error('WebSocket 错误:', error);
            };
        }
        
        function updateHeartRate(current) {
            const rateElement = document.getElementById('currentRate');
            const heartIcon = document.querySelector('.heart-icon');
            
            if (rateElement) {
                rateElement.textContent = current !== undefined ? current : '--';
            }
            
            if (heartIcon && current !== '--' && current !== null && !isNaN(current)) {
                const rate = parseInt(current);
                const duration = Math.max(0.5, 2 - (rate - 60) / 100);
                heartIcon.style.animationDuration = duration + 's';
            }
        }
        
        window.addEventListener('load', connectWebSocket);
    </script>
</body>
</html>'''


class HeartRateClient:
    def __init__(self, uri):
        self.uri = uri
        self.websocket = None
        self.reconnect_delay = 5
        self.heartbeat_interval = 15
        self.is_running = True
        self.heartbeat_task = None
        self.reconnect_requested = False
        
    def is_connection_open(self):
        if self.websocket is None:
            return False
        try:
            return not self.websocket.closed
        except:
            return False
    
    def request_reconnect(self, new_uri):
        self.uri = new_uri
        self.reconnect_requested = True
        self.is_running = False
    
    async def send_heartbeat(self):
        try:
            if self.is_connection_open():
                heartbeat = {
                    "type": "heartbeat",
                    "timestamp": datetime.now().isoformat()
                }
                await self.websocket.send(json.dumps(heartbeat))
        except Exception:
            pass
    
    async def heartbeat_loop(self):
        try:
            while self.is_running and self.is_connection_open():
                await asyncio.sleep(self.heartbeat_interval)
                if self.is_connection_open():
                    await self.send_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    
    async def connect(self):
        while True:
            self.is_running = True
            self.reconnect_requested = False
            
            try:
                log_message(f"[*] 尝试连接：{self.uri}")
                update_status("连接中...")
                
                async with websockets.connect(
                    self.uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=2**20,
                    compression=None,
                ) as websocket:
                    self.websocket = websocket
                    log_message(f"[✓] 连接成功！")
                    update_status("已连接")
                    
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                    
                    try:
                        async for message in websocket:
                            if self.reconnect_requested:
                                log_message("[*] 收到重连请求，关闭当前连接")
                                break
                            if not self.is_running:
                                break
                                
                            try:
                                data = json.loads(message)
                                
                                if isinstance(data, dict):
                                    msg_type = data.get('type', 'unknown')
                                    
                                    if msg_type == 'heart_rate':
                                        value = data.get('value')
                                        log_message(f"  ❤️  心率：{value} {data.get('unit', 'bpm')}")
                                        await broadcast_heart_rate(value)
                                    elif msg_type == 'heartbeat':
                                        pass
                                    elif msg_type == 'ack':
                                        pass
                                elif isinstance(data, (int, float)):
                                    log_message(f"心率值：{data} bpm")
                                    await broadcast_heart_rate(data)
                                else:
                                    log_message(f"  📝 {message}")
                                    
                            except json.JSONDecodeError:
                                log_message(f"  📝 原始：{message}")
                                
                    except websockets.exceptions.ConnectionClosedError:
                        log_message("[⚠️] 连接断开")
                        update_status("连接断开")
                    except websockets.exceptions.ConnectionClosedOK:
                        log_message("[✓] 连接正常关闭")
                        update_status("已关闭")
                    except asyncio.CancelledError:
                        log_message("[⚠️] 连接被取消")
                        break
                    except ConnectionResetError:
                        log_message("[⚠️] 连接被重置")
                        update_status("连接重置")
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
                log_message(f"[错误] HTTP 状态码：{e.status_code}")
                update_status("连接失败")
            except ConnectionRefusedError:
                log_message("[错误] 连接被拒绝")
                update_status("连接被拒绝")
            except OSError as e:
                log_message(f"[错误] 网络错误")
                update_status("网络错误")
            except asyncio.CancelledError:
                log_message("[⚠️] 连接任务被取消")
                break
            except ConnectionResetError:
                log_message("[⚠️] 连接被重置")
                update_status("连接重置")
            except Exception as e:
                log_message(f"[错误] {type(e).__name__}")
                update_status("错误")
            
            if self.reconnect_requested:
                log_message(f"[*] 开始重连到：{self.uri}")
                self.reconnect_requested = False
                continue
                
            if not self.is_running:
                break
            
            log_message(f"[*] {self.reconnect_delay}秒后重连...")
            
            try:
                for i in range(self.reconnect_delay):
                    if self.reconnect_requested or not self.is_running:
                        break
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
        
        log_message("[*] 连接循环已结束")
    
    def stop(self):
        self.is_running = False
        self.reconnect_requested = False
        log_message("[*] 收到停止信号...")


async def broadcast_heart_rate(value):
    global latest_heart_rate
    latest_heart_rate = value
    
    if gui_root:
        gui_root.after(0, lambda: update_heart_rate_display(value))
    
    message = json.dumps({
        "type": "heart_rate",
        "current": value,
        "timestamp": datetime.now().isoformat()
    })
    
    disconnected = set()
    for ws in connected_clients:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)
    
    connected_clients.difference_update(disconnected)


async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    connected_clients.add(ws)
    log_message(f"[🌐] 网页客户端连接，当前连接数：{len(connected_clients)}")
    
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
                        if data.get('code') == 'XPH5qChgcd':
                            await ws.send_str(json.dumps({
                                "type": "auth_result",
                                "success": True
                            }))
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
                pass
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        # 优雅关闭 WebSocket 连接
        try:
            connected_clients.discard(ws)
            if not ws.closed:
                await ws.close(code=1000, message=b'')
        except Exception:
            pass
        if not is_shutting_down:
            log_message(f"[🌐] 网页客户端断开，当前连接数：{len(connected_clients)}")
    
    return ws


async def handle_index(request):
    return web.Response(text=HTML_CONTENT, content_type='text/html')


async def start_web_server():
    global web_runner
    
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', handle_websocket)
    
    # 配置访问日志，减少错误输出
    web_runner = web.AppRunner(app, access_log=None)
    await web_runner.setup()
    site = web.TCPSite(web_runner, '0.0.0.0', 20888)
    await site.start()
    
    log_message("=" * 50)
    log_message("🌐 网页服务已启动")
    log_message("📍 访问地址：http://127.0.0.1:20888")
    log_message("=" * 50)
    
    if web_url_label:
        gui_root.after(0, lambda: web_url_label.config(text="http://127.0.0.1:20888"))
    
    return web_runner


# ==================== GUI 更新函数 ====================

def update_heart_rate_display(value):
    global heart_rate_label
    if heart_rate_label:
        try:
            heart_rate_label.config(text=f"{value} bpm")
            try:
                val = int(float(value))
                if val < 60:
                    heart_rate_label.config(foreground="#2ed573")
                elif val > 100:
                    heart_rate_label.config(foreground="#ff4757")
                else:
                    heart_rate_label.config(foreground="#ff6b81")
            except:
                heart_rate_label.config(foreground="#ff6b81")
        except Exception:
            pass

def update_status(status):
    global status_label
    if status_label:
        try:
            status_label.config(text=f"状态：{status}")
        except:
            pass

def log_message(message):
    global log_text
    if log_text and not is_shutting_down:
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            log_text.see(tk.END)
        except:
            pass

def on_ip_change():
    global client, ip_change_queue
    if ip_entry and client and ip_change_queue is not None:
        new_ip = ip_entry.get().strip()
        if new_ip:
            port = 6667
            new_uri = f"ws://{new_ip}:{port}"
            log_message(f"[*] IP 地址已修改为：{new_ip}")
            log_message(f"[*] 新目标地址：{new_uri}")
            ip_change_queue.put(new_uri)

def create_gui():
    global gui_root, heart_rate_label, ip_entry, status_label, log_text, web_url_label
    
    gui_root = tk.Tk()
    gui_root.title("心率监控器")
    gui_root.geometry("520x420")
    gui_root.resizable(False, False)
    
    default_font = ("Microsoft YaHei UI", 10)
    title_font = ("Microsoft YaHei UI", 14, "bold")
    heart_font = ("Microsoft YaHei UI", 32, "bold")
    
    main_frame = ttk.Frame(gui_root, padding="10")
    main_frame.pack(fill=tk.BOTH, expand=True)
    
    title_label = ttk.Label(main_frame, text="心率监控器", font=title_font)
    title_label.pack(pady=(0, 10))
    
    heart_frame = ttk.Frame(main_frame)
    heart_frame.pack(pady=10)
    
    heart_rate_label = tk.Label(heart_frame, text="-- bpm", font=heart_font, 
                                 foreground="#ff6b81")
    heart_rate_label.pack()
    
    web_frame = ttk.Frame(main_frame)
    web_frame.pack(pady=5)
    
    ttk.Label(web_frame, text="网页地址：", font=default_font).pack(side=tk.LEFT)
    web_url_label = tk.Label(web_frame, text="http://127.0.0.1:20888", 
                             font=default_font, foreground="#0066cc")
    web_url_label.pack(side=tk.LEFT)
    
    ip_frame = ttk.Frame(main_frame)
    ip_frame.pack(pady=10)
    
    ttk.Label(ip_frame, text="服务器 IP:", font=default_font).pack(side=tk.LEFT, padx=(0, 5))
    ip_entry = ttk.Entry(ip_frame, width=18, font=default_font)
    ip_entry.pack(side=tk.LEFT, padx=(0, 5))
    ip_entry.insert(0, "192.168.3.168")
    
    update_btn = ttk.Button(ip_frame, text="更新", command=on_ip_change, width=6)
    update_btn.pack(side=tk.LEFT)
    
    status_label = tk.Label(main_frame, text="状态：未连接", font=default_font, 
                            foreground="#666666")
    status_label.pack(pady=5)
    
    log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
    log_frame.pack(fill=tk.BOTH, expand=True, pady=10)
    
    log_text = tk.Text(log_frame, height=8, font=("Consolas", 9), wrap=tk.WORD, bg="#f5f5f5")
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    
    scrollbar = ttk.Scrollbar(log_frame, command=log_text.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=scrollbar.set)
    
    sys.stdout = TextRedirector(log_text)
    sys.stderr = TextRedirector(log_text)
    
    log_message("GUI 初始化完成")
    
    return gui_root


async def run_client_task():
    global client, asyncio_loop, ip_change_queue
    
    ip = ip_entry.get().strip() if ip_entry else "192.168.3.168"
    port = 6667
    uri = f"ws://{ip}:{port}"
    
    log_message("=" * 50)
    log_message("WebSocket 心率客户端 + 网页服务")
    log_message("=" * 50)
    log_message(f"[*] 目标地址：{uri}")
    
    await start_web_server()
    
    client = HeartRateClient(uri)
    
    asyncio.create_task(check_ip_changes())
    
    await client.connect()


async def check_ip_changes():
    global client, ip_change_queue
    
    while not is_shutting_down:
        try:
            try:
                new_uri = ip_change_queue.get_nowait()
                if client:
                    log_message(f"[*] 检测到 IP 变更，准备重连到：{new_uri}")
                    client.request_reconnect(new_uri)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break


def check_tasks():
    if is_shutting_down:
        gui_root.quit()
    else:
        gui_root.after(100, check_tasks)


def on_closing():
    global is_shutting_down
    is_shutting_down = True
    log_message("[*] 正在关闭...")
    if client:
        client.stop()
    gui_root.destroy()


async def cleanup():
    global web_runner, connected_clients
    
    # 关闭所有网页客户端连接
    for ws in list(connected_clients):
        try:
            if not ws.closed:
                await ws.close(code=1000, message=b'')
        except Exception:
            pass
    connected_clients.clear()
    
    # 关闭网页服务器
    if web_runner:
        try:
            await web_runner.cleanup()
        except Exception:
            pass
    
    log_message("[*] 程序已退出")


def main():
    global gui_root, asyncio_loop, ip_change_queue
    
    ip_change_queue = queue.Queue()
    
    create_gui()
    
    asyncio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(asyncio_loop)
    
    def run_asyncio_thread():
        try:
            asyncio_loop.run_until_complete(run_client_task())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_message(f"[错误] {e}")
        finally:
            global is_shutting_down
            is_shutting_down = True
            try:
                asyncio_loop.run_until_complete(cleanup())
            except:
                pass
    
    asyncio_thread = threading.Thread(target=run_asyncio_thread, daemon=True)
    asyncio_thread.start()
    
    gui_root.after(100, check_tasks)
    
    gui_root.protocol("WM_DELETE_WINDOW", on_closing)
    
    gui_root.mainloop()
    
    try:
        asyncio_loop.close()
    except:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] 强制退出")
    except Exception as e:
        print(f"\n[错误] {e}")