from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.keyboards.admin import (
    build_hiddify_add_cancel,
    build_hiddify_add_options,
    build_hiddify_import_confirmation,
    build_hiddify_import_waiting_actions,
    build_hiddify_load_actions,
    build_hiddify_menu,
    build_hiddify_server_actions,
)
from app.services.exceptions import AccessDeniedError, DomainError
from app.services.users import UsersService
from app.states.admin_hiddify import AdminHiddifyState

router = Router()

HIDDIFY_TEMPLATE = Path(__file__).resolve().parents[2] / "assets" / "hiddify_servers_template.xlsx"


def _format_servers(servers) -> str:
    lines = [
        "<b>Hiddify-серверы Юлии 🖥</b>",
        "",
        "Сюда можно подключить несколько Hiddify Manager панелей. После этого Юлия сможет сама выпускать доступы после оплаты.",
        "",
    ]
    if not servers:
        lines.append("Пока ни одного сервера не подключено.")
        lines.append("Нажми «Подключить сервер», и я предложу ручной ввод или импорт XLSX.")
        return "\n".join(lines)

    for server in servers:
        status = "активен" if server.is_active else "отключён"
        health = {
            "healthy": "✅ связь в порядке",
            "unhealthy": "⚠️ нужна проверка",
            "unknown": "❔ ещё не проверяла",
        }.get(server.last_health_status, server.last_health_status)
        lines.append(f"• <b>{server.name}</b> — {server.country_name}, {status}, {health}")
    return "\n".join(lines)


def _format_server_card(server) -> str:
    checked_at = server.last_healthcheck_at.strftime("%Y-%m-%d %H:%M UTC") if server.last_healthcheck_at else "ещё не проверяла"
    lines = [
        f"<b>{server.name}</b>",
        "",
        f"Страна: <b>{server.country_name}</b>",
        f"Base URL: <code>{server.base_url}</code>",
        f"Admin path: <code>{server.admin_proxy_path}</code>",
        f"Client path: <code>{server.client_proxy_path}</code>",
        f"Статус: <b>{'активен' if server.is_active else 'отключён'}</b>",
        f"Проверка: <b>{server.last_health_status}</b>",
        f"Последняя проверка: {checked_at}",
    ]
    if server.panel_version:
        lines.append(f"Версия панели: <code>{server.panel_version}</code>")
    if server.last_error:
        lines.append(f"Последняя ошибка: <code>{server.last_error}</code>")
    return "\n".join(lines)


def _format_server_load(loads, capacity_statuses=None) -> str:
    lines = [
        "<b>Нагрузка Hiddify-серверов</b>",
        "",
        "Live-count активных пользователей берется из Hiddify API. MTProxy выдача пойдет на самый свободный доступный сервер.",
        "",
    ]
    if not loads:
        lines.append("Пока нет подключенных серверов.")
        return "\n".join(lines)

    capacity_lines = _format_location_capacity_statuses(capacity_statuses or [])
    if capacity_lines:
        lines.extend(capacity_lines)
        lines.append("")

    for item in loads:
        status = "активен" if item.is_active else "отключен"
        if item.active_users_count is None:
            active_users = "n/a"
        else:
            total_users = item.total_users_count if item.total_users_count is not None else "?"
            active_percent = f"{item.active_users_percent:.1f}%" if item.active_users_percent is not None else "n/a"
            active_users = f"{item.active_users_count}/{total_users} ({active_percent})"
        monthly_usage = (
            f"{item.average_monthly_usage_gb:.2f} ГБ/польз./мес"
            if item.average_monthly_usage_gb is not None
            else "n/a"
        )
        current_usage = (
            f"{item.total_current_usage_gb:.2f} ГБ"
            if item.total_current_usage_gb is not None
            else "n/a"
        )
        monthly_window_usage = (
            f"{item.monthly_average_user_usage_gb:.2f} ГБ/польз."
            if item.monthly_average_user_usage_gb is not None
            else "n/a"
        )
        monthly_window_total = (
            f"{item.monthly_average_total_usage_gb:.2f} ГБ"
            if item.monthly_average_total_usage_gb is not None
            else "n/a"
        )
        monthly_window_active = (
            f"{item.monthly_average_active_users_percent:.1f}%"
            if item.monthly_average_active_users_percent is not None
            else "n/a"
        )
        checked_at = item.checked_at.strftime("%Y-%m-%d %H:%M UTC") if item.checked_at else "не проверялся"
        marker = " → кандидат для MTProxy" if item.selected_for_mtproxy else ""
        mtproxy_state = "готов" if item.mtproxy_available else "недоступен"
        lines.append(
            f"• <b>{item.server_name}</b> — {item.country_name}, {status}, "
            f"активные: <b>{active_users}</b>, MTProxy: <b>{mtproxy_state}</b>{marker}"
        )
        lines.append(
            f"  Ресурсы: среднее/мес <b>{monthly_usage}</b>; текущий расход <b>{current_usage}</b>; "
            f"выборка: {item.usage_sample_users_count}"
        )
        lines.append(
            f"  30д среднее: активные <b>{monthly_window_active}</b>; "
            f"расход <b>{monthly_window_usage}</b>; общий расход <b>{monthly_window_total}</b>; "
            f"snapshots: {item.monthly_snapshots_count}"
        )
        lines.append(f"  Проверка: {checked_at}; health: <code>{item.health_status}</code>")
        if item.last_error:
            lines.append(f"  Ошибка: <code>{item.last_error}</code>")
    return "\n".join(lines)


def _format_location_capacity_statuses(capacity_statuses) -> list[str]:
    if not capacity_statuses:
        return []
    lines = [
        "<b>Capacity по локациям</b>",
        "Статус «нужен сервер» появляется, когда все активные серверы страны за порогом.",
    ]
    for item in capacity_statuses:
        status = _capacity_label(item)
        active_range = _format_range(
            minimum=item.active_users_min_percent,
            maximum=item.active_users_max_percent,
            suffix="%",
        )
        usage_range = _format_range(
            minimum=item.usage_min_gb,
            maximum=item.usage_max_gb,
            suffix=" ГБ",
        )
        lines.append(
            f"• <b>{item.country_name}</b>: {status}; серверов {item.servers_count}; "
            f"active {active_range}/{item.active_users_threshold_percent:.1f}%; "
            f"traffic {usage_range}/{item.usage_threshold_gb:.0f} ГБ; "
            f"snapshots: {item.snapshots_count}"
        )
    return lines


def _capacity_label(item) -> str:
    if item.capacity_needed:
        return "<b>нужен сервер</b>"
    if item.active_users_status == "watch" or item.usage_status == "watch":
        return "<b>наблюдать</b>"
    if item.active_users_status == "unknown" or item.usage_status == "unknown":
        return "<b>нет данных</b>"
    return "<b>запас есть</b>"


def _format_range(*, minimum, maximum, suffix: str) -> str:
    if minimum is None or maximum is None:
        return "n/a"
    if minimum == maximum:
        return f"{maximum:.1f}{suffix}" if suffix == "%" else f"{maximum:.0f}{suffix}"
    if suffix == "%":
        return f"{minimum:.1f}-{maximum:.1f}{suffix}"
    return f"{minimum:.0f}-{maximum:.0f}{suffix}"


def _format_preview(preview: dict) -> str:
    return (
        f"<b>Preview для {preview['filename']}</b>\n\n"
        f"Всего строк: <b>{preview['rows_total']}</b>\n"
        f"Валидных: <b>{preview['rows_valid']}</b>\n"
        f"Отклонено: <b>{preview['rows_rejected']}</b>\n"
        f"Первые ошибки: <code>{preview['errors'][:5]}</code>"
    )


def _format_import_result(result: dict) -> str:
    imported_lines = [
        f"• {item['name']} ({item['country_name']}) [id={item['server_id']}]"
        for item in result["servers"][:10]
    ]
    imported_block = "\n".join(imported_lines) if imported_lines else "Пока без успешно подключённых серверов."
    errors_block = f"\n\nПервые ошибки: <code>{result['errors'][:5]}</code>" if result["errors"] else ""
    return (
        "<b>Импорт Hiddify-серверов завершён ✅</b>\n\n"
        f"Импортировано: <b>{result['rows_imported']}</b>\n"
        f"Отклонено: <b>{result['rows_rejected']}</b>\n\n"
        f"{imported_block}{errors_block}"
    )


async def _render_hiddify_menu(target, services) -> None:
    servers = await services.hiddify.list_servers()
    text = _format_servers(servers)
    markup = build_hiddify_menu(servers)
    if hasattr(target, "edit_text"):
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _render_server(target, services, server_id: int) -> None:
    server = await services.hiddify.get_server(server_id)
    text = _format_server_card(server)
    markup = build_hiddify_server_actions(server_id=server.id, is_active=server.is_active)
    if hasattr(target, "edit_text"):
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _send_hiddify_template(message: Message) -> None:
    if not HIDDIFY_TEMPLATE.exists():
        return
    await message.answer_document(
        FSInputFile(HIDDIFY_TEMPLATE),
        caption=(
            "📎 Вот шаблон для импорта Hiddify-серверов.\n"
            "Заполни его и пришли сюда обратно этим же чатом."
        ),
    )


def _require_admin(user) -> None:
    UsersService.require_admin(user)


@router.message(Command("admin_hiddify"))
async def admin_hiddify_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await _render_hiddify_menu(message, services)


@router.callback_query(F.data == "admin:hiddify")
async def admin_hiddify_callback(callback: CallbackQuery, app_user, services, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await _render_hiddify_menu(callback.message, services)
    await callback.answer()


@router.callback_query(F.data == "admin:hiddify:load")
async def admin_hiddify_load_callback(callback: CallbackQuery, app_user, services, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    loads = await services.hiddify.list_server_load()
    capacity_statuses = services.hiddify_usage.build_location_capacity_status(loads)
    await callback.message.edit_text(_format_server_load(loads, capacity_statuses), reply_markup=build_hiddify_load_actions())
    await callback.answer("Нагрузка обновлена")


@router.callback_query(F.data == "admin:hiddify:snapshots:collect")
async def admin_hiddify_collect_snapshots_callback(
    callback: CallbackQuery,
    app_user,
    services,
    state: FSMContext,
    **_: dict,
) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    loads = await services.hiddify_usage.collect_snapshots_now()
    capacity_statuses = services.hiddify_usage.build_location_capacity_status(loads)
    await callback.message.edit_text(_format_server_load(loads, capacity_statuses), reply_markup=build_hiddify_load_actions())
    await callback.answer(f"Snapshots собраны: {len(loads)}")


@router.callback_query(F.data == "admin:hiddify:add")
async def admin_hiddify_add_callback(callback: CallbackQuery, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        (
            "<b>Подключить сервер ✨</b>\n\n"
            "Выбери, как тебе удобнее добавить Hiddify-панель."
        ),
        reply_markup=build_hiddify_add_options(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:hiddify:add:manual")
async def admin_hiddify_add_manual_callback(callback: CallbackQuery, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminHiddifyState.waiting_for_name)
    await callback.message.edit_text(
        (
            "<b>Подключение Hiddify-сервера ✨</b>\n\n"
            "Шаг 1 из 6.\n"
            "Пришли короткое имя сервера, чтобы я показывала его в админке."
        ),
        reply_markup=build_hiddify_add_cancel(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:hiddify:add:xlsx")
async def admin_hiddify_add_xlsx_callback(callback: CallbackQuery, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminHiddifyState.waiting_for_import_document)
    await _send_hiddify_template(callback.message)
    await callback.message.edit_text(
        (
            "<b>Импорт Hiddify-серверов из XLSX 📥</b>\n\n"
            "Пришли файл <code>.xlsx</code> с листом <code>servers</code>.\n"
            "Сначала я покажу preview, а импорт начну только после подтверждения."
        ),
        reply_markup=build_hiddify_import_waiting_actions(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:hiddify:cancel")
async def admin_hiddify_cancel_callback(callback: CallbackQuery, app_user, services, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await _render_hiddify_menu(callback.message, services)
    await callback.answer("Вернула тебя к списку серверов")


@router.message(AdminHiddifyState.waiting_for_name)
async def admin_hiddify_name_step(message: Message, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await state.update_data(server_name=message.text.strip())
    await state.set_state(AdminHiddifyState.waiting_for_country)
    await message.answer(
        (
            "<b>Шаг 2 из 6</b>\n\n"
            "Теперь пришли <b>страну сервера</b>.\n"
            "Например: <code>Germany</code>, <code>Netherlands</code> или <code>Finland</code>."
        ),
        reply_markup=build_hiddify_add_cancel(),
    )


@router.message(AdminHiddifyState.waiting_for_country)
async def admin_hiddify_country_step(message: Message, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await state.update_data(country_name=message.text.strip())
    await state.set_state(AdminHiddifyState.waiting_for_base_url)
    await message.answer(
        (
            "<b>Шаг 3 из 6</b>\n\n"
            "Теперь пришли <b>base URL</b> панели.\n"
            "Пример: <code>https://panel.example.com</code>"
        ),
        reply_markup=build_hiddify_add_cancel(),
    )


@router.message(AdminHiddifyState.waiting_for_base_url)
async def admin_hiddify_base_url_step(message: Message, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await state.update_data(base_url=message.text.strip().rstrip("/"))
    await state.set_state(AdminHiddifyState.waiting_for_admin_proxy_path)
    await message.answer(
        (
            "<b>Шаг 4 из 6</b>\n\n"
            "Пришли <b>admin proxy path</b> из Hiddify.\n"
            "Нужен только путь без домена, например <code>abc123admin</code>."
        ),
        reply_markup=build_hiddify_add_cancel(),
    )


@router.message(AdminHiddifyState.waiting_for_admin_proxy_path)
async def admin_hiddify_admin_path_step(message: Message, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await state.update_data(admin_proxy_path=message.text.strip().strip("/"))
    await state.set_state(AdminHiddifyState.waiting_for_client_proxy_path)
    await message.answer(
        (
            "<b>Шаг 5 из 6</b>\n\n"
            "Пришли <b>client proxy path</b> для пользовательских ссылок.\n"
            "Тоже только путь без домена, например <code>xyz789client</code>."
        ),
        reply_markup=build_hiddify_add_cancel(),
    )


@router.message(AdminHiddifyState.waiting_for_client_proxy_path)
async def admin_hiddify_client_path_step(message: Message, app_user, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    await state.update_data(client_proxy_path=message.text.strip().strip("/"))
    await state.set_state(AdminHiddifyState.waiting_for_api_key)
    await message.answer(
        (
            "<b>Шаг 6 из 6</b>\n\n"
            "Осталось прислать <b>Hiddify API key</b>.\n"
            "Я сразу проверю соединение и, если всё хорошо, сохраню сервер."
        ),
        reply_markup=build_hiddify_add_cancel(),
    )


@router.message(AdminHiddifyState.waiting_for_api_key)
async def admin_hiddify_api_key_step(message: Message, app_user, state: FSMContext, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return

    data = await state.get_data()
    try:
        server = await services.hiddify.register_server(
            name=data["server_name"],
            country_name=data["country_name"],
            base_url=data["base_url"],
            admin_proxy_path=data["admin_proxy_path"],
            client_proxy_path=data["client_proxy_path"],
            api_key=message.text.strip(),
            actor_user_id=app_user.id,
        )
    except Exception as exc:
        await message.answer(
            (
                "<b>Не смогла подключить сервер 😔</b>\n\n"
                f"Ошибка: <code>{exc}</code>\n"
                "Проверь URL, proxy path и API key, потом попробуй ещё раз."
            ),
            reply_markup=build_hiddify_add_cancel(),
        )
        return

    await state.clear()
    await message.answer(
        (
            "<b>Сервер подключён ✅</b>\n\n"
            f"Имя: <b>{server.name}</b>\n"
            f"Страна: <b>{server.country_name}</b>\n"
            f"Панель: <code>{server.base_url}</code>\n"
            f"Версия: <code>{server.panel_version or 'unknown'}</code>"
        ),
        reply_markup=build_hiddify_server_actions(server_id=server.id, is_active=server.is_active),
    )


@router.message(AdminHiddifyState.waiting_for_import_document, F.document)
async def admin_hiddify_import_preview(message: Message, app_user, state: FSMContext, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, ты еще не админ 🙂")
        return
    if not message.document.file_name.lower().endswith(".xlsx"):
        await message.answer("Нужен файл формата <code>.xlsx</code> 🙂")
        return

    buffer = BytesIO()
    await message.bot.download(message.document, destination=buffer)
    content = buffer.getvalue()
    try:
        preview = await services.hiddify_xlsx_import.preview(
            filename=message.document.file_name,
            content=content,
        )
    except Exception as exc:
        await message.answer(
            (
                "<b>Не смогла прочитать файл 😔</b>\n\n"
                f"Ошибка: <code>{exc}</code>"
            ),
            reply_markup=build_hiddify_import_waiting_actions(),
        )
        return

    await state.update_data(
        hiddify_import_filename=message.document.file_name,
        hiddify_import_content=base64.b64encode(content).decode("ascii"),
    )
    await state.set_state(AdminHiddifyState.waiting_for_import_confirmation)
    await message.answer(
        _format_preview(preview),
        reply_markup=build_hiddify_import_confirmation(),
    )


@router.callback_query(AdminHiddifyState.waiting_for_import_confirmation, F.data == "admin:hiddify:import:confirm")
async def admin_hiddify_import_confirm(callback: CallbackQuery, app_user, state: FSMContext, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return

    data = await state.get_data()
    content = base64.b64decode(data["hiddify_import_content"])
    result = await services.hiddify_xlsx_import.import_file(
        filename=data["hiddify_import_filename"],
        content=content,
        uploaded_by_user_id=app_user.id,
    )
    await state.clear()
    await callback.message.edit_text(
        _format_import_result(result),
        reply_markup=build_hiddify_add_cancel(),
    )
    await callback.answer("Импорт Hiddify-серверов завершён ✨")


@router.callback_query(AdminHiddifyState.waiting_for_import_confirmation, F.data == "admin:hiddify:import:cancel")
async def admin_hiddify_import_cancel(callback: CallbackQuery, app_user, services, state: FSMContext, **_: dict) -> None:
    try:
        _require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await state.clear()
    await _render_hiddify_menu(callback.message, services)
    await callback.answer("Импорт отменён")


@router.callback_query(F.data.startswith("admin:hiddify:server:"))
async def admin_hiddify_server_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
        server_id = int(callback.data.rsplit(":", maxsplit=1)[1])
        await _render_server(callback.message, services, server_id)
        await callback.answer()
    except (AccessDeniedError, DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:hiddify:toggle:"))
async def admin_hiddify_toggle_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
        server_id = int(callback.data.rsplit(":", maxsplit=1)[1])
        server = await services.hiddify.toggle_server(server_id=server_id, actor_user_id=app_user.id)
        await _render_server(callback.message, services, server.id)
        await callback.answer("Статус сервера обновлён")
    except (AccessDeniedError, DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:hiddify:check:"))
async def admin_hiddify_check_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        _require_admin(app_user)
        server_id = int(callback.data.rsplit(":", maxsplit=1)[1])
        server = await services.hiddify.refresh_server(server_id=server_id, actor_user_id=app_user.id)
        await _render_server(callback.message, services, server.id)
        await callback.answer("Сервер проверен")
    except (AccessDeniedError, DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
