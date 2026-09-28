"""青鸟测试统一环境变量：在 import server.py 之前把测试凭据注入 os.environ，
避免 test_*.py 顺序敏感（pytest/unittest 都按字母序收集，先 test_card 会让
server 模块加载时 os.environ 是空的）。

unittest discover 不会主动加载 conftest.py，所以在 test_*.py 顶部
`from tests._env import env` 显式触发本模块；pytest 自动加载 conftest.py
做同样的事。"""
import os

os.environ.update({
    "NOTIFY_OWNER": "testowner",
    "WEBHOOK_SECRET": "test-secret",
    "BARK_KEY": "test-key",
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "app-secret",
    "FEISHU_RECEIVE_ID": "oc_test",
    "WECOM_WEBHOOK": "https://mock.wecom",
    "SLACK_WEBHOOK": "https://hooks.slack.com/s/x",
    "GENERIC_TOKEN": "",
    "NOTIFY_AUTH_USER": "admin",
    "NOTIFY_AUTH_PASS": "secret",
    "NOTIFY_DB": "/tmp/bluebird-test.db",
    "DEDUP_SECONDS": "60",
    "LOG_RETENTION_DAYS": "30",
    "BASE_PATH": "",
})