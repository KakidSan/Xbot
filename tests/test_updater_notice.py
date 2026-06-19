import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from xbot import updater


class UpdateResultNoticeTest(unittest.IsolatedAsyncioTestCase):
    def status_file(self, tmpdir: str) -> Path:
        state_file = Path(tmpdir) / ".install-state" / "update-status.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "status": "restarting",
                    "stage": "docker_up",
                    "message": "镜像已更新，正在重建 Xbot 容器。",
                    "target_version": "v3.0.0-beta2",
                    "chat_id": "12345",
                }
            ),
            encoding="utf-8",
        )
        return state_file

    async def test_update_status_is_kept_when_notice_send_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self.status_file(tmpdir)
            app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom"))))

            with patch.object(updater, "UPDATE_STATUS_FILE", state_file):
                await updater.send_update_result_notice(app)

            self.assertTrue(state_file.exists())

    async def test_update_status_is_deleted_after_notice_send_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self.status_file(tmpdir)
            app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

            with patch.object(updater, "UPDATE_STATUS_FILE", state_file):
                await updater.send_update_result_notice(app)

            self.assertFalse(state_file.exists())


if __name__ == "__main__":
    unittest.main()
