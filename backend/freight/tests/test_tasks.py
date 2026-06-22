from freight import tasks


def test_celery_app_is_configured():
    from config.celery import app

    assert app.main == "freight_intelligence"


def test_sync_sku_task_delegates_to_management_command(monkeypatch):
    calls = []

    def fake_call_command(command_name, **options):
        calls.append((command_name, options))

    monkeypatch.setattr(tasks, "call_command", fake_call_command)

    result = tasks.sync_sku_from_wms_task.run(full=True, limit=10)

    assert calls == [("sync_sku_from_wms", {"full": True, "limit": 10})]
    assert result == {"command": "sync_sku_from_wms", "status": "completed", "options": {"full": True, "limit": 10}}


def test_freight_audit_task_delegates_to_management_command(monkeypatch):
    calls = []

    def fake_call_command(command_name, **options):
        calls.append((command_name, options))

    monkeypatch.setattr(tasks, "call_command", fake_call_command)

    result = tasks.build_freight_audit_matrix_task.run(limit=5000)

    assert calls == [("build_freight_audit_matrix", {"limit": 5000})]
    assert result["command"] == "build_freight_audit_matrix"
