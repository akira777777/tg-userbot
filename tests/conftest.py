"""Обычный pytest не подхватывает живые скрипты панели."""

collect_ignore = ["../test_api_live.py", "../test_media.py"]
collect_ignore_glob = ["test_api_live.py", "test_media.py"]
