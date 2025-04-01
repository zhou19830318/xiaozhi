import json
import _thread
import time
import machine
import network
import ws.client as websocket
from machine import Pin, I2S

# 配置参数
is_manualmode = False

# 状态变量
listen_state = "stop"
tts_state = "idle"
key_state = "release"
is_connected = False
send_audio_thread = None
ping_thread = None

# WebSocket 配置
ws_url = "ws://14.103.130.118:8000/xiaozhi/v1/"
msg_info = {"type": "hello", "session_id": "3a66666c"}
access_token = "test-token"
device_mac = "32:23:42:24:52:25"
device_uuid = "4321414214214"

# WiFi 配置
WIFI_SSID = "Prefoco"
WIFI_PASSWORD = "18961210318"
WIFI_RETRY_MAX = 5
WIFI_RETRY_DELAY = 5

# WebSocket 重试配置
WS_RETRY_MAX = 3
WS_RETRY_DELAY = 3
WS_PING_INTERVAL = 30

# 音频参数
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 960

# WebSocket 请求头
websocket_headers = {
    "Authorization": f"Bearer {access_token}",
    "Protocol-Version": "1",
    "Device-Id": device_mac,
    "Client-Id": device_uuid
}

BINARY = 0x2

# 日志函数
def log(level, message):
    print(f"{time.localtime()[:6]} - {level} - {message}")

def log_info(message): log("INFO", message)
def log_error(message): log("ERROR", message)
def log_debug(message): log("DEBUG", message)

# 硬件配置
I2S_ID_IN, SCK_PIN_IN, WS_PIN_IN, SD_PIN_IN = 0, 9, 8, 7
I2S_ID_OUT, SCK_PIN_OUT, WS_PIN_OUT, SD_PIN_OUT = 1, 11, 12, 10
KEY_PIN = 13

# 全局变量
key = Pin(KEY_PIN, Pin.IN, Pin.PULL_UP)
audio_in, audio_out, ws, websocket_thread = None, None, None, None

def send_json_message(message):
    global ws
    try:
        if ws and ws.open:
            ws.send(json.dumps(message))
            log_info(f"Sent message: {message}")
            return True
        log_debug("WebSocket not connected")
        return False
    except Exception as e:
        log_error(f"Send message error: {e}")
        return False

# 添加一个全局变量来存储服务器期望的音频格式
server_audio_format = "pcm"

def on_message(received_message):
    global msg_info, tts_state, send_audio_thread, listen_state, is_manualmode, server_audio_format
    
    if isinstance(received_message, bytes):
        try:
            audio_out.write(received_message)
            log_debug("Played received audio")
        except Exception as e:
            log_error(f"Audio playback error: {e}")
    else:
        try:
            msg = json.loads(received_message)
            log_info(f"Received msg: {msg}")
            
            if msg['type'] == 'hello':
                msg_info = msg
                log_info(f"Hello response received, audio params: {msg.get('audio_params', {})}")
                
                # 保存服务器期望的音频格式
                if 'audio_params' in msg and 'format' in msg['audio_params']:
                    server_audio_format = msg['audio_params']['format']
                    log_info(f"服务器期望的音频格式: {server_audio_format}")
                
                if not send_audio_thread:
                    log_info("Starting audio send thread")
                    send_audio_thread = _thread.start_new_thread(send_audio, ())
            
            elif msg['type'] == 'tts':
                tts_state = msg['state']
                log_info(f"TTS state: {tts_state}")
                if tts_state == 'sentence_start' and 'text' in msg:
                    log_info(f"TTS text: {msg['text']}")
            
            if (tts_state == 'stop' or msg['type'] == 'hello') and not is_manualmode:
                log_info("Auto mode: start listening")
                msg = {"session_id": msg_info.get('session_id', ""), "type": "listen", 
                       "state": "start", "mode": "auto"}
                if send_json_message(msg):
                    listen_state = "start"
            
            elif msg['type'] == 'goodbye':
                log_info("Session ended")
                msg_info['session_id'] = ""
            
            elif msg['type'] == 'llm' and 'emotion' in msg:
                log_info(f"Emotion state: {msg['emotion']}")
                
        except Exception as e:
            log_error(f"Message processing error: {e}")

def on_open():
    global is_connected
    log_info("=" * 34)
    log_info("WebSocket connected")
    
    # 修改点2：保持客户端和服务端的音频格式声明一致
    hello_msg = {
        "type": "hello", "version": 1, "transport": "websocket",
        # 修改为实际支持的PCM格式（与发送的数据一致）
        "audio_params": {"format": "pcm", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
        "authorization": websocket_headers["Authorization"],
        "protocol_version": websocket_headers["Protocol-Version"],
        "device_id": websocket_headers["Device-Id"],
        "client_id": websocket_headers["Client-Id"]
    }
    if send_json_message(hello_msg):
        is_connected = True

def on_close():
    global is_connected, listen_state
    is_connected = False
    listen_state = "stop"
    log_info("=" * 50)
    log_info("WebSocket closed! Press key to reconnect")

def send_ping():
    global ws, is_connected
    last_ping = time.time()
    
    while is_connected:
        if time.time() - last_ping >= WS_PING_INTERVAL:
            try:
                if ws and ws.open:
                    # Replace the ping() call with a proper ping message
                    ws.send(json.dumps({"type": "ping"}))
                    log_debug("Ping sent")
                    last_ping = time.time()
            except Exception as e:
                log_error(f"Ping error: {e}")
                break
        time.sleep(1)

def connect_websocket():
    global ws, websocket_thread, ping_thread, is_connected
    
    for attempt in range(WS_RETRY_MAX):
        try:
            log_info(f"Connecting WebSocket ({attempt+1}/{WS_RETRY_MAX}): {ws_url}")
            ws = websocket.connect(ws_url)
            on_open()
            websocket_thread = _thread.start_new_thread(ws_run_forever, ())
            ping_thread = _thread.start_new_thread(send_ping, ())
            return True
        except Exception as e:
            log_error(f"WebSocket connection error ({attempt+1}/{WS_RETRY_MAX}): {str(e)}")
            if attempt < WS_RETRY_MAX - 1:
                log_info(f"Retrying in {WS_RETRY_DELAY} seconds...")
                time.sleep(WS_RETRY_DELAY)
            else:
                log_error(f"Failed to connect after {WS_RETRY_MAX} attempts")
    return False

def ws_run_forever():
    global ws
    log_info("WebSocket receiver started")
    try:
        while ws and ws.open:
            try:
                message = ws.recv()
                if message:
                    on_message(message)
                else:
                    log_debug("Received empty message, continuing to wait...")
                    time.sleep(0.1)  # 收到空消息时不退出，继续等待
            except websocket.NoDataException:
                log_debug("No data received, waiting...")
                time.sleep(0.1)
            except Exception as e:
                log_error(f"Receive error: {e}")
                break
        log_info("WebSocket connection closed by server")
    except Exception as e:
        log_error(f"Receiver thread error: {e}")
    finally:
        log_info("WebSocket receiver stopped")
        on_close()

def init_audio():
    global audio_in, audio_out
    try:
        log_info("Initializing audio...")
        audio_in = I2S(I2S_ID_IN, sck=Pin(SCK_PIN_IN), ws=Pin(WS_PIN_IN), sd=Pin(SD_PIN_IN),
                      mode=I2S.RX, bits=16, format=I2S.MONO, rate=SAMPLE_RATE, ibuf=CHUNK * 4)
        audio_out = I2S(I2S_ID_OUT, sck=Pin(SCK_PIN_OUT), ws=Pin(WS_PIN_OUT), sd=Pin(SD_PIN_OUT),
                       mode=I2S.TX, bits=16, format=I2S.MONO, rate=SAMPLE_RATE, ibuf=CHUNK * 4)
        log_info("Audio initialized")
        return True
    except Exception as e:
        log_error(f"Audio init error: {e}")
        return False

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    global device_mac, websocket_headers
    device_mac = ':'.join(['%02x' % b for b in wlan.config('mac')])
    websocket_headers["Device-Id"] = device_mac
    log_info(f"MAC: {device_mac}")
    
    if wlan.isconnected():
        log_info(f"WiFi connected: {wlan.ifconfig()}")
        return True
    
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for attempt in range(WIFI_RETRY_MAX):
        if wlan.isconnected():
            log_info(f"WiFi connected! IP: {wlan.ifconfig()[0]}")
            return True
        log_info(f"Waiting for WiFi ({attempt+1}/{WIFI_RETRY_MAX})...")
        time.sleep(WIFI_RETRY_DELAY)
    log_error(f"WiFi connection failed after {WIFI_RETRY_MAX} attempts")
    return False

def cleanup():
    global audio_in, audio_out, ws
    log_info("Cleaning up resources...")
    try:
        if audio_in: audio_in.deinit()
        if audio_out: audio_out.deinit()
        if ws: ws.close()
    except Exception as e:
        log_error(f"Cleanup error: {e}")

def main():
    try:
        log_info("Program starting...")
        if not connect_wifi():
            log_error("WiFi connection failed")
            return
        if not init_audio():
            log_error("Audio initialization failed")
            return
            
        key.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=key_callback)
        
        # 初始连接尝试
        if not connect_websocket():
            log_error("Initial WebSocket connection failed")
        
        # 主循环
        while True:
            if not is_connected:
                log_info("WebSocket disconnected, attempting to reconnect...")
                connect_websocket()
            time.sleep(1)
            
    except Exception as e:
        log_error(f"Main error: {e}")
    finally:
        cleanup()

def send_audio():
    global listen_state, audio_in, ws, is_connected, server_audio_format
    buf = bytearray(CHUNK * 2)
    
    while is_connected:
        if listen_state != "start":
            time.sleep(0.1)
            continue
        
        try:
            bytes_read = audio_in.readinto(buf)
            if bytes_read and ws and ws.open:
                log_debug(f"准备发送音频帧: {bytes_read} 字节, 前10字节: {buf[:10]}")
                
                # 修改点1：移除自定义的PCM标记头，直接发送原始PCM数据
                if server_audio_format == "opus":
                    # 直接发送原始PCM数据（需要服务器支持PCM格式）
                    ws.write_frame(BINARY, buf[:bytes_read])
                    log_debug(f"已发送音频帧: {bytes_read} 字节")
                else:
                    ws.write_frame(BINARY, buf[:bytes_read])
                    log_debug(f"已发送音频帧: {bytes_read} 字节")
        except Exception as e:
            log_error(f"Audio send error: {e}")
            time.sleep(0.1)

def key_callback(pin):
    global key_state
    if pin.value() == 0:
        if key_state == "release":
            key_state = "press"
            on_key_press()
    else:
        if key_state == "press":
            key_state = "release"
            on_key_release()

def on_key_press():
    global msg_info, listen_state, is_connected, tts_state, is_manualmode
    
    if not is_connected:
        log_info("WebSocket not connected, attempting reconnect")
        connect_websocket()
    elif tts_state in ["start", "sentence_start"]:
        log_info("Interrupting TTS playback")
        send_json_message({"type": "abort"})
    elif is_manualmode:
        log_info("Manual mode: start listening")
        msg = {"session_id": msg_info.get('session_id', ""), "type": "listen", 
               "state": "start", "mode": "manual"}
        if send_json_message(msg):
            listen_state = "start"

def on_key_release():
    global msg_info, listen_state, is_connected, is_manualmode
    
    if is_manualmode and is_connected:
        log_info("Manual mode: stop listening")
        msg = {"session_id": msg_info.get('session_id', ""), "type": "listen", "state": "stop"}
        if send_json_message(msg):
            listen_state = "stop"

def on_message(received_message):
    global msg_info, tts_state, send_audio_thread, listen_state, is_manualmode
    
    if isinstance(received_message, bytes):
        try:
            audio_out.write(received_message)
            log_debug("Played received audio")
        except Exception as e:
            log_error(f"Audio playback error: {e}")
    else:
        try:
            msg = json.loads(received_message)
            log_info(f"Received msg: {msg}")
            
            if msg['type'] == 'hello':
                msg_info = msg
                log_info(f"Hello response received, audio params: {msg.get('audio_params', {})}")
                
                # 保存服务器期望的音频格式
                if 'audio_params' in msg and 'format' in msg['audio_params']:
                    log_info(f"服务器期望的音频格式: {msg['audio_params']['format']}")
                
                if not send_audio_thread:
                    log_info("Starting audio send thread")
                    send_audio_thread = _thread.start_new_thread(send_audio, ())
            
            elif msg['type'] == 'tts':
                tts_state = msg['state']
                log_info(f"TTS state: {tts_state}")
                if tts_state == 'sentence_start' and 'text' in msg:
                    log_info(f"TTS text: {msg['text']}")
            
            if (tts_state == 'stop' or msg['type'] == 'hello') and not is_manualmode:
                log_info("Auto mode: start listening")
                msg = {"session_id": msg_info.get('session_id', ""), "type": "listen", 
                       "state": "start", "mode": "auto"}
                if send_json_message(msg):
                    listen_state = "start"
            
            elif msg['type'] == 'goodbye':
                log_info("Session ended")
                msg_info['session_id'] = ""
            
            elif msg['type'] == 'llm' and 'emotion' in msg:
                log_info(f"Emotion state: {msg['emotion']}")
                
        except Exception as e:
            log_error(f"Message processing error: {e}")

def on_open():
    global is_connected
    log_info("=" * 34)
    log_info("WebSocket connected")
    
    # 修改hello消息，使用与服务器相同的音频格式
    hello_msg = {
        "type": "hello", "version": 1, "transport": "websocket",
        # 使用opus格式，与服务器期望的格式一致
        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
        "authorization": websocket_headers["Authorization"],
        "protocol_version": websocket_headers["Protocol-Version"],
        "device_id": websocket_headers["Device-Id"],
        "client_id": websocket_headers["Client-Id"]
    }
    if send_json_message(hello_msg):
        is_connected = True

def on_close():
    global is_connected, listen_state
    is_connected = False
    listen_state = "stop"
    log_info("=" * 50)
    log_info("WebSocket closed! Press key to reconnect")

def send_ping():
    global ws, is_connected
    last_ping = time.time()
    
    while is_connected:
        if time.time() - last_ping >= WS_PING_INTERVAL:
            try:
                if ws and ws.open:
                    # Replace the ping() call with a proper ping message
                    ws.send(json.dumps({"type": "ping"}))
                    log_debug("Ping sent")
                    last_ping = time.time()
            except Exception as e:
                log_error(f"Ping error: {e}")
                break
        time.sleep(1)

def connect_websocket():
    global ws, websocket_thread, ping_thread, is_connected
    
    for attempt in range(WS_RETRY_MAX):
        try:
            log_info(f"Connecting WebSocket ({attempt+1}/{WS_RETRY_MAX}): {ws_url}")
            ws = websocket.connect(ws_url)
            on_open()
            websocket_thread = _thread.start_new_thread(ws_run_forever, ())
            ping_thread = _thread.start_new_thread(send_ping, ())
            return True
        except Exception as e:
            log_error(f"WebSocket connection error ({attempt+1}/{WS_RETRY_MAX}): {str(e)}")
            if attempt < WS_RETRY_MAX - 1:
                log_info(f"Retrying in {WS_RETRY_DELAY} seconds...")
                time.sleep(WS_RETRY_DELAY)
            else:
                log_error(f"Failed to connect after {WS_RETRY_MAX} attempts")
    return False

def ws_run_forever():
    global ws
    log_info("WebSocket receiver started")
    try:
        while ws and ws.open:
            try:
                message = ws.recv()
                if message:
                    on_message(message)
                else:
                    log_debug("Received empty message, continuing to wait...")
                    time.sleep(0.1)  # 收到空消息时不退出，继续等待
            except websocket.NoDataException:
                log_debug("No data received, waiting...")
                time.sleep(0.1)
            except Exception as e:
                log_error(f"Receive error: {e}")
                break
        log_info("WebSocket connection closed by server")
    except Exception as e:
        log_error(f"Receiver thread error: {e}")
    finally:
        log_info("WebSocket receiver stopped")
        on_close()

def init_audio():
    global audio_in, audio_out
    try:
        log_info("Initializing audio...")
        audio_in = I2S(I2S_ID_IN, sck=Pin(SCK_PIN_IN), ws=Pin(WS_PIN_IN), sd=Pin(SD_PIN_IN),
                      mode=I2S.RX, bits=16, format=I2S.MONO, rate=SAMPLE_RATE, ibuf=CHUNK * 4)
        audio_out = I2S(I2S_ID_OUT, sck=Pin(SCK_PIN_OUT), ws=Pin(WS_PIN_OUT), sd=Pin(SD_PIN_OUT),
                       mode=I2S.TX, bits=16, format=I2S.MONO, rate=SAMPLE_RATE, ibuf=CHUNK * 4)
        log_info("Audio initialized")
        return True
    except Exception as e:
        log_error(f"Audio init error: {e}")
        return False

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    global device_mac, websocket_headers
    device_mac = ':'.join(['%02x' % b for b in wlan.config('mac')])
    websocket_headers["Device-Id"] = device_mac
    log_info(f"MAC: {device_mac}")
    
    if wlan.isconnected():
        log_info(f"WiFi connected: {wlan.ifconfig()}")
        return True
    
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for attempt in range(WIFI_RETRY_MAX):
        if wlan.isconnected():
            log_info(f"WiFi connected! IP: {wlan.ifconfig()[0]}")
            return True
        log_info(f"Waiting for WiFi ({attempt+1}/{WIFI_RETRY_MAX})...")
        time.sleep(WIFI_RETRY_DELAY)
    log_error(f"WiFi connection failed after {WIFI_RETRY_MAX} attempts")
    return False

def cleanup():
    global audio_in, audio_out, ws
    log_info("Cleaning up resources...")
    try:
        if audio_in: audio_in.deinit()
        if audio_out: audio_out.deinit()
        if ws: ws.close()
    except Exception as e:
        log_error(f"Cleanup error: {e}")

def main():
    try:
        log_info("Program starting...")
        if not connect_wifi():
            log_error("WiFi connection failed")
            return
        if not init_audio():
            log_error("Audio initialization failed")
            return
            
        key.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=key_callback)
        
        # 初始连接尝试
        if not connect_websocket():
            log_error("Initial WebSocket connection failed")
        
        # 主循环
        while True:
            if not is_connected:
                log_info("WebSocket disconnected, attempting to reconnect...")
                connect_websocket()
            time.sleep(1)
            
    except Exception as e:
        log_error(f"Main error: {e}")
    finally:
        cleanup()

if __name__ == "__main__":
    main()
