"""让根目录与 fixtures/ 下的测试都能导入 src 布局的包"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
