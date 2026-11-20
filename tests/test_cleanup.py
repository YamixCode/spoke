import hashlib
from io import BytesIO

import pytest

from spoke.cleanup import Cleanup, _install_file, _preserves_content


def test_rejects_lost_numbers_names_negations_and_long_omissions():
    assert _preserves_content("Смотри я думаю что всё получится", "Я думаю, что всё получится.")
    assert not _preserves_content("Не выходи до 15:30", "Выходи до 15:30.")
    assert not _preserves_content("До встречи в 15:30", "До встречи в 16:30.")
    assert not _preserves_content("До встречи в 15.30", "До встречи в 15. 30.")
    assert not _preserves_content("Проверь обновление Spoke", "Проверь обновление Spoken.")
    assert not _preserves_content("Сначала проверить документы затем отправить отчёт", "Проверить документы.")


def test_model_file_is_installed_only_after_checksum_validation(tmp_path):
    dest = tmp_path / "weights"
    data = b"weights"
    digest = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        _install_file(BytesIO(b"invalid"), dest, len(data), digest)
    assert list(tmp_path.iterdir()) == []
    _install_file(BytesIO(data), dest, len(data), digest)
    assert dest.read_bytes() == data


def test_raw_text_survives_when_cleanup_is_not_ready(monkeypatch):
    model = Cleanup()
    monkeypatch.setattr(model, "prepare", lambda: None)
    assert model.process("Исходный текст") == "Исходный текст"
