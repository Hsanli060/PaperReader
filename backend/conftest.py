"""pytest 的路径锚点。这个文件为空也不需要任何代码，但【不要删】。

为什么必须有它：
    测试里写的是 from app.rag.fusion import ...，而 app 包在 backend/ 下。
    pytest 默认不会把 backend/ 加进 sys.path——之前能跑通只是因为
    "在 backend/ 目录下执行 python -m pytest"（-m 会把当前工作目录塞进 sys.path）。
    一旦从项目根目录或 PyCharm 默认工作目录跑，就报 ModuleNotFoundError: No module named 'app'。

    pytest 加载 conftest.py 时会把该文件所在目录加入 sys.path，
    所以 backend/ 里放一个空的 conftest.py，测试就能在任意工作目录下跑。

批①增补：
    另一件全测试共享的事——用户级 API Key 上线后默认"严格模式"（没配 key 就 409），
    既有测试沿用 .env 默认 key 的旧行为，所以统一打开回退开关；
    要验证"严格模式"的用例（test_user_keys.py）在测试体内自行 monkeypatch 回 False。
"""
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _allow_default_keys(monkeypatch):
    """所有测试默认开回退（等价于改造前行为）；严格模式用例自行覆盖"""
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", True)
