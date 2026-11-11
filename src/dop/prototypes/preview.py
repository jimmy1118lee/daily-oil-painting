"""独立 UI 原型预览：python src/dop/prototypes/preview.py，不启动壁纸引擎。"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/gui-prototype.html?variant=A"
        print(f"UI 原型：{url}")
        print("按键盘左右键切换方案；Ctrl+C 结束预览。")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
