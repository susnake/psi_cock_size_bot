"""
Подготовка окружения до импорта бота:
- фиктивный токен и пустые ключи (load_dotenv не перетирает уже заданные переменные,
  поэтому настоящий .env не подхватится);
- рабочая папка — временная, чтобы ./data не создавалась в репозитории.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["psi_chat_bot"] = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
for _name in ("GEMINI_API_KEY", "STORAGE_CHAT_ID", "IMAGE_SERVER_URL", "UPLOAD_TOKEN"):
    os.environ[_name] = ""

os.chdir(tempfile.mkdtemp(prefix="psi_bot_tests_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
