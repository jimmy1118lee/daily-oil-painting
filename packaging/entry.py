"""PyInstaller 打包入口。

打包后 dop 包会被解到临时目录，所以这里直接按包名导入，
源码运行时则由 --paths 指定 src 目录。
"""

import sys

from dop.cli import main

if __name__ == "__main__":
    sys.exit(main())
